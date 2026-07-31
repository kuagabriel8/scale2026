"use client";

import { useEffect, useRef, useState } from "react";
import { getRiskAssessment } from "@/lib/api";
import type { ReviewStatus, RiskAssessment } from "@/lib/types";
import { computeSeverity } from "@/lib/severity";
import { formatScore } from "@/lib/format";
import { TierBadge } from "./TierBadge";
import { TypologySignals } from "./TypologySignals";
import { ComponentScoresChart } from "./charts/ComponentScoresChart";
import { ContributionChart } from "./charts/ContributionChart";
import { ContextRows } from "./ContextRows";
import { GovernancePanel } from "./GovernancePanel";
import { CloseIcon } from "./icons";
import styles from "./DetailDrawer.module.css";

export function DetailDrawer({
  assessment,
  model,
  dataSource,
  onClose,
  onRecordReview,
}: {
  /** In-memory row from the live stream/seed - always available, used as an
   * immediate fallback and as the source of truth for governance updates. */
  assessment: RiskAssessment | null;
  /** Currently selected scoring model (live mode only, null = backend default). */
  model?: string | null;
  dataSource?: "mock" | "live";
  onClose: () => void;
  onRecordReview: (transactionId: number, status: ReviewStatus, reviewedBy: string) => void;
}) {
  // The in-memory `assessment` may have been seeded/streamed with a
  // different model than the one currently selected in the picker, so in
  // live mode we do our own per-transaction fetch whenever the drawer opens
  // or the selected model changes, to actually reflect that model's score.
  const [fetched, setFetched] = useState<RiskAssessment | null>(null);
  const [loading, setLoading] = useState(false);
  const requestIdRef = useRef(0);

  const transactionId = assessment?.transaction_id ?? null;

  useEffect(() => {
    const fetchDetail = async () => {
      if (dataSource !== "live" || transactionId === null) {
        // Invalidate any in-flight request rather than setState here; the
        // display logic below already ignores a stale `fetched` whose
        // transaction_id no longer matches the in-memory assessment.
        requestIdRef.current += 1;
        return;
      }
      const requestId = ++requestIdRef.current;
      setLoading(true);
      try {
        const res = await getRiskAssessment(transactionId, model ?? undefined);
        if (requestIdRef.current !== requestId) return;
        setFetched(res);
      } catch {
        // Leave whatever's already shown (in-memory fallback) rather than
        // blanking the drawer on a transient fetch error.
      } finally {
        if (requestIdRef.current === requestId) setLoading(false);
      }
    };
    fetchDetail();
  }, [transactionId, model, dataSource]);

  // Governance decisions are recorded through the shared in-memory state
  // (optimistic update + PATCH), not through this per-model fetch, so keep
  // whichever governance is freshest by always trusting the in-memory copy.
  const displayed =
    fetched && assessment && fetched.transaction_id === assessment.transaction_id
      ? { ...fetched, governance: assessment.governance }
      : assessment;

  if (!displayed) return null;
  const displayedAssessment = displayed;

  return (
    <div className={styles.overlay} onClick={onClose}>
      <aside
        className={styles.drawer}
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label={`Detail for transaction ${displayedAssessment.transaction_id}`}
      >
        <div className={styles.header}>
          <div className={styles.headerMain}>
            <h2 className={styles.txnId}>Transaction #{displayedAssessment.transaction_id}</h2>
            <TierBadge tier={displayedAssessment.risk.risk_tier} />
          </div>
          <button className={styles.close} onClick={onClose} aria-label="Close detail panel">
            <CloseIcon />
          </button>
        </div>

        <div className={styles.meta}>
          <span>severity {formatScore(computeSeverity(displayedAssessment))}</span>
          <span>overall risk {formatScore(displayedAssessment.risk.overall_risk_score)}</span>
          <span>model confidence {Math.round(displayedAssessment.risk.model_confidence * 100)}%</span>
          <span>model {displayedAssessment.model_version}</span>
          {displayedAssessment.risk.is_anomaly && (
            <span className={styles.anomalyTag}>
              anomaly: {displayedAssessment.risk.anomaly_type ?? "unspecified"}
            </span>
          )}
          {loading && <span className={styles.refreshNote}>refreshing with selected model...</span>}
        </div>

        {displayedAssessment.explanation?.narrative_text && (
          <p className={styles.narrative}>{displayedAssessment.explanation.narrative_text}</p>
        )}

        <div className={styles.section}>
          <TypologySignals signals={displayedAssessment.typology_signals} />
        </div>

        <div className={styles.grid}>
          <div className={styles.section}>
            <ComponentScoresChart scores={displayedAssessment.risk.component_scores} />
          </div>
          <div className={styles.section}>
            <ContributionChart scores={displayedAssessment.explanation?.top_column_scores} />
          </div>
        </div>

        <div className={styles.section}>
          <ContextRows rows={displayedAssessment.explanation?.top_relevant_context_rows} />
        </div>

        <div className={styles.section}>
          <GovernancePanel
            governance={displayedAssessment.governance}
            onRecord={(status, reviewedBy) =>
              onRecordReview(displayedAssessment.transaction_id, status, reviewedBy)
            }
          />
        </div>
      </aside>
    </div>
  );
}
