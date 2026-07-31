"use client";

import { useEffect, useMemo, useRef, useState, useCallback } from "react";
import {
  DATA_SOURCE,
  MOCK_MAX_INTERVAL_MS,
  MOCK_MIN_INTERVAL_MS,
  MOCK_SEED_COUNT,
  WS_URL,
} from "./config";
import { listRiskAssessments, updateReviewStatus } from "./api";
import { MockStream } from "./mockGenerator";
import type {
  ConnectionStatus,
  RiskAssessment,
  RiskAssessmentStreamEvent,
} from "./types";

// Initial REST seed size for live mode - mirrors MOCK_SEED_COUNT's role of
// making sure the dashboard isn't empty while the WS stream trickles in.
const LIVE_SEED_PAGE_SIZE = 100;

export interface RiskStreamState {
  assessments: RiskAssessment[];
  status: ConnectionStatus;
  dataSource: "mock" | "live";
  lastEventAt: string | null;
  eventCount: number;
  /** Manually record a governance decision. Applies an optimistic local
   * update immediately, then (in live mode) PATCHes
   * /risk-assessments/{id}/review so the decision actually persists on the
   * backend and broadcasts to other connected analysts; reverts the local
   * update if the PATCH fails. */
  recordReviewDecision: (
    transactionId: number,
    reviewStatus: RiskAssessment["governance"]["review_status"],
    reviewedBy: string
  ) => void;
}

function applyEvent(
  map: Map<number, RiskAssessment>,
  event: RiskAssessmentStreamEvent
): Map<number, RiskAssessment> {
  const next = new Map(map);
  const { assessment, event_type } = event;
  if (event_type === "REVIEW_STATUS_CHANGED") {
    const existing = next.get(assessment.transaction_id);
    if (existing) {
      next.set(assessment.transaction_id, {
        ...existing,
        governance: assessment.governance,
      });
    } else {
      next.set(assessment.transaction_id, assessment);
    }
  } else {
    // ASSESSMENT_CREATED / ASSESSMENT_UPDATED: full upsert by transaction_id.
    next.set(assessment.transaction_id, assessment);
  }
  return next;
}

export function useRiskStream(model: string | null = null): RiskStreamState {
  const [byId, setById] = useState<Map<number, RiskAssessment>>(new Map());
  const [status, setStatus] = useState<ConnectionStatus>(
    DATA_SOURCE === "mock" ? "mock" : "connecting"
  );
  const [lastEventAt, setLastEventAt] = useState<string | null>(null);
  const [eventCount, setEventCount] = useState(0);
  const lastSequenceRef = useRef<number>(-1);
  const wsRef = useRef<WebSocket | null>(null);
  // True only for the very first REST seed fetch (on mount, before the model
  // picker may have even resolved a selection) - that one must not clobber
  // rows already delivered by the WS stream. Every subsequent fetch is a
  // deliberate re-score triggered by the analyst changing the model picker,
  // so it should overwrite so the change is actually visible.
  const isFirstSeedRef = useRef(true);

  const ingest = useCallback((event: RiskAssessmentStreamEvent) => {
    // Dedupe / ignore out-of-order per schema guidance: sequence is
    // monotonically increasing per backend/generator instance.
    if (event.sequence <= lastSequenceRef.current) return;
    lastSequenceRef.current = event.sequence;
    setById((prev) => applyEvent(prev, event));
    setLastEventAt(event.emitted_at);
    setEventCount((c) => c + 1);
  }, []);

  // ---- Mock mode ----
  useEffect(() => {
    if (DATA_SOURCE !== "mock") return;
    const stream = new MockStream();
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;

    for (const evt of stream.seed(MOCK_SEED_COUNT)) {
      ingest(evt);
    }

    const scheduleNext = () => {
      const delay =
        MOCK_MIN_INTERVAL_MS +
        Math.random() * (MOCK_MAX_INTERVAL_MS - MOCK_MIN_INTERVAL_MS);
      timer = setTimeout(() => {
        if (cancelled) return;
        ingest(stream.next());
        scheduleNext();
      }, delay);
    };
    scheduleNext();

    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ---- Live mode: REST seed (initial mount, and again whenever the
  // analyst changes the selected scorer model via the model picker) ----
  useEffect(() => {
    if (DATA_SOURCE !== "live") return;
    let cancelled = false;
    const isFirst = isFirstSeedRef.current;
    isFirstSeedRef.current = false;

    listRiskAssessments({
      page_size: LIVE_SEED_PAGE_SIZE,
      sort: "severity",
      model: model ?? undefined,
    })
      .then((res) => {
        if (cancelled) return;
        setById((prev) => {
          const next = new Map(prev);
          for (const assessment of res.items) {
            if (isFirst && next.has(assessment.transaction_id)) {
              // Initial mount only: don't clobber anything already ingested
              // from the WS stream (which may connect and deliver events
              // before this resolves).
              continue;
            }
            next.set(assessment.transaction_id, assessment);
          }
          return next;
        });
      })
      .catch(() => {
        // Backend not running / network error - leave the dashboard as-is
        // and let the WS connection-status UI reflect the failure instead
        // of crashing here.
      });

    return () => {
      cancelled = true;
    };
  }, [model]);

  // ---- Live mode: WebSocket stream ----
  useEffect(() => {
    if (DATA_SOURCE !== "live") return;
    let cancelled = false;
    let reconnectTimer: ReturnType<typeof setTimeout>;
    let attempt = 0;

    const connect = () => {
      if (cancelled) return;
      setStatus(attempt === 0 ? "connecting" : "reconnecting");
      const ws = new WebSocket(WS_URL);
      wsRef.current = ws;

      ws.onopen = () => {
        attempt = 0;
        setStatus("connected");
      };
      ws.onmessage = (msg) => {
        try {
          const event: RiskAssessmentStreamEvent = JSON.parse(msg.data);
          ingest(event);
        } catch {
          // Ignore malformed frames rather than crash the dashboard.
        }
      };
      ws.onclose = () => {
        if (cancelled) return;
        setStatus("disconnected");
        attempt += 1;
        const backoff = Math.min(15000, 1000 * 2 ** attempt);
        reconnectTimer = setTimeout(connect, backoff);
      };
      ws.onerror = () => {
        setStatus("error");
        ws.close();
      };
    };

    connect();

    return () => {
      cancelled = true;
      clearTimeout(reconnectTimer);
      wsRef.current?.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const recordReviewDecision = useCallback(
    (
      transactionId: number,
      reviewStatus: RiskAssessment["governance"]["review_status"],
      reviewedBy: string
    ) => {
      // Optimistic local update so the UI feels instant, regardless of data
      // source. In live mode this is later reconciled by the PATCH response
      // and then the REVIEW_STATUS_CHANGED WS broadcast the backend follows
      // it up with (both carry the same final state, so no flicker).
      let previousGovernance: RiskAssessment["governance"] | null = null;
      setById((prev) => {
        const existing = prev.get(transactionId);
        if (!existing) return prev;
        previousGovernance = existing.governance;
        const next = new Map(prev);
        next.set(transactionId, {
          ...existing,
          governance: {
            requires_human_review: true,
            review_status: reviewStatus,
            reviewed_by: reviewedBy || null,
          },
        });
        return next;
      });

      if (DATA_SOURCE !== "live") return;

      updateReviewStatus(transactionId, reviewStatus, reviewedBy || null)
        .then((updated) => {
          setById((prev) => {
            if (!prev.has(transactionId)) return prev;
            const next = new Map(prev);
            next.set(transactionId, updated);
            return next;
          });
        })
        .catch(() => {
          // PATCH failed (network error / 404 / etc) - revert the optimistic
          // update so local state doesn't silently diverge from the backend.
          setById((prev) => {
            const existing = prev.get(transactionId);
            if (!existing || !previousGovernance) return prev;
            const next = new Map(prev);
            next.set(transactionId, { ...existing, governance: previousGovernance });
            return next;
          });
        });
    },
    []
  );

  const assessments = useMemo(() => Array.from(byId.values()), [byId]);

  return {
    assessments,
    status,
    dataSource: DATA_SOURCE,
    lastEventAt,
    eventCount,
    recordReviewDecision,
  };
}
