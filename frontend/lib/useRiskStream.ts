"use client";

import { useEffect, useMemo, useRef, useState, useCallback } from "react";
import {
  DATA_SOURCE,
  MOCK_MAX_INTERVAL_MS,
  MOCK_MIN_INTERVAL_MS,
  MOCK_SEED_COUNT,
  WS_URL,
} from "./config";
import { MockStream } from "./mockGenerator";
import type {
  ConnectionStatus,
  RiskAssessment,
  RiskAssessmentStreamEvent,
} from "./types";

export interface RiskStreamState {
  assessments: RiskAssessment[];
  status: ConnectionStatus;
  dataSource: "mock" | "live";
  lastEventAt: string | null;
  eventCount: number;
  /** Manually record a governance decision (patches local state; also
   * forwards a REVIEW_STATUS_CHANGED-shaped intent to the live backend if
   * connected, so the analyst action isn't silently local-only). */
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

export function useRiskStream(): RiskStreamState {
  const [byId, setById] = useState<Map<number, RiskAssessment>>(new Map());
  const [status, setStatus] = useState<ConnectionStatus>(
    DATA_SOURCE === "mock" ? "mock" : "connecting"
  );
  const [lastEventAt, setLastEventAt] = useState<string | null>(null);
  const [eventCount, setEventCount] = useState(0);
  const lastSequenceRef = useRef<number>(-1);
  const wsRef = useRef<WebSocket | null>(null);

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

  // ---- Live mode ----
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
      setById((prev) => {
        const existing = prev.get(transactionId);
        if (!existing) return prev;
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

      // Best-effort forward to a live backend if connected; the manual
      // governance action always applies locally regardless of this.
      const ws = wsRef.current;
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(
          JSON.stringify({
            action: "REVIEW_STATUS_CHANGED",
            transaction_id: transactionId,
            review_status: reviewStatus,
            reviewed_by: reviewedBy || null,
          })
        );
      }
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
