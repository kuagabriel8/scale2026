import type { TopColumnScore } from "@/lib/types";
import { humanizeColumn } from "@/lib/format";
import styles from "./ContributionChart.module.css";

/**
 * top_column_scores: horizontal bars, sorted descending, thin rounded-end
 * marks, values shown as direct labels (only 4 values - no axis ticks
 * needed). Single series => no legend; title suffices.
 */
export function ContributionChart({ scores }: { scores: TopColumnScore[] | undefined }) {
  const sorted = [...(scores ?? [])].sort((a, b) => b.contribution_score - a.contribution_score);
  const max = Math.max(0.0001, ...sorted.map((s) => Math.abs(s.contribution_score)));

  return (
    <div className={styles.wrap}>
      <h4 className={styles.title}>Top contribution factors</h4>
      {sorted.length === 0 ? (
        <p className={styles.empty}>No explanation columns reported for this assessment.</p>
      ) : (
        <div className={styles.rows}>
          {sorted.map((s) => {
            const pct = (Math.abs(s.contribution_score) / max) * 100;
            return (
              <div className={styles.row} key={s.column}>
                <span className={styles.label} title={s.column}>
                  {humanizeColumn(s.column)}
                </span>
                <div className={styles.track}>
                  <div className={styles.bar} style={{ width: `${pct}%` }} />
                </div>
                <span className={styles.value}>{s.contribution_score.toFixed(2)}</span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
