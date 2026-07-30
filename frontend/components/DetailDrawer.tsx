"use client";

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
  onClose,
  onRecordReview,
}: {
  assessment: RiskAssessment | null;
  onClose: () => void;
  onRecordReview: (transactionId: number, status: ReviewStatus, reviewedBy: string) => void;
}) {
  if (!assessment) return null;

  return (
    <div className={styles.overlay} onClick={onClose}>
      <aside
        className={styles.drawer}
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label={`Detail for transaction ${assessment.transaction_id}`}
      >
        <div className={styles.header}>
          <div className={styles.headerMain}>
            <h2 className={styles.txnId}>Transaction #{assessment.transaction_id}</h2>
            <TierBadge tier={assessment.risk.risk_tier} />
          </div>
          <button className={styles.close} onClick={onClose} aria-label="Close detail panel">
            <CloseIcon />
          </button>
        </div>

        <div className={styles.meta}>
          <span>severity {formatScore(computeSeverity(assessment))}</span>
          <span>overall risk {formatScore(assessment.risk.overall_risk_score)}</span>
          <span>model confidence {Math.round(assessment.risk.model_confidence * 100)}%</span>
          <span>model {assessment.model_version}</span>
          {assessment.risk.is_anomaly && (
            <span className={styles.anomalyTag}>
              anomaly: {assessment.risk.anomaly_type ?? "unspecified"}
            </span>
          )}
        </div>

        {assessment.explanation?.narrative_text && (
          <p className={styles.narrative}>{assessment.explanation.narrative_text}</p>
        )}

        <div className={styles.section}>
          <TypologySignals signals={assessment.typology_signals} />
        </div>

        <div className={styles.grid}>
          <div className={styles.section}>
            <ComponentScoresChart scores={assessment.risk.component_scores} />
          </div>
          <div className={styles.section}>
            <ContributionChart scores={assessment.explanation?.top_column_scores} />
          </div>
        </div>

        <div className={styles.section}>
          <ContextRows rows={assessment.explanation?.top_relevant_context_rows} />
        </div>

        <div className={styles.section}>
          <GovernancePanel
            governance={assessment.governance}
            onRecord={(status, reviewedBy) =>
              onRecordReview(assessment.transaction_id, status, reviewedBy)
            }
          />
        </div>
      </aside>
    </div>
  );
}
