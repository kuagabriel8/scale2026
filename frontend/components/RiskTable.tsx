"use client";

import type { RiskAssessment } from "@/lib/types";
import { computeSeverity } from "@/lib/severity";
import { formatRelativeTime, formatScore } from "@/lib/format";
import { useNowTick } from "@/lib/useNowTick";
import { TierBadge } from "./TierBadge";
import { CategoryChip } from "./CategoryChip";
import { ReviewStatusBadge } from "./ReviewStatusBadge";
import { AlertDiamondIcon } from "./icons";
import styles from "./RiskTable.module.css";

export function RiskTable({
  assessments,
  onSelect,
  selectedId,
}: {
  assessments: RiskAssessment[];
  onSelect: (transactionId: number) => void;
  selectedId: number | null;
}) {
  const now = useNowTick(1000);

  if (assessments.length === 0) {
    return <div className={styles.empty}>No assessments match the current filters.</div>;
  }

  return (
    <div className={styles.tableWrap}>
      <table className={styles.table}>
        <thead>
          <tr>
            <th className={styles.rank}>#</th>
            <th>Transaction</th>
            <th>Risk tier</th>
            <th className={styles.num}>Severity</th>
            <th className={styles.num}>Overall risk</th>
            <th className={styles.num}>Typology strength</th>
            <th>Triggered categories</th>
            <th>Review status</th>
            <th>Scored</th>
          </tr>
        </thead>
        <tbody>
          {assessments.map((a, i) => {
            const cats = Array.from(
              new Set(a.typology_signals.filter((s) => s.triggered).map((s) => s.category))
            );
            const isFresh = now - new Date(a.scored_at).getTime() < 5000;
            return (
              <tr
                key={a.transaction_id}
                className={styles.row}
                data-selected={selectedId === a.transaction_id}
                data-fresh={isFresh}
                onClick={() => onSelect(a.transaction_id)}
                tabIndex={0}
                role="button"
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") onSelect(a.transaction_id);
                }}
              >
                <td className={styles.rank}>{i + 1}</td>
                <td>
                  <div className={styles.txnCell}>
                    <span className={styles.txnId}>#{a.transaction_id}</span>
                    {a.risk.is_anomaly && (
                      <span className={styles.anomaly} title={a.risk.anomaly_type ?? "Anomaly detected"}>
                        <AlertDiamondIcon size={11} /> anomaly
                      </span>
                    )}
                  </div>
                </td>
                <td>
                  <TierBadge tier={a.risk.risk_tier} compact />
                </td>
                <td className={styles.num}>
                  <strong>{formatScore(computeSeverity(a))}</strong>
                </td>
                <td className={styles.num}>{formatScore(a.risk.overall_risk_score)}</td>
                <td className={styles.num}>{a.exposure.typology_strength_points}</td>
                <td>
                  <div className={styles.chips}>
                    {cats.length === 0 ? (
                      <span className={styles.muted}>none triggered</span>
                    ) : (
                      cats.map((c) => <CategoryChip key={c} category={c} compact />)
                    )}
                  </div>
                </td>
                <td>
                  <ReviewStatusBadge status={a.governance.review_status} />
                </td>
                <td className={styles.muted}>{formatRelativeTime(a.scored_at, now)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
