import type { ContextRow } from "@/lib/types";
import { formatPercent, humanizeOutcome } from "@/lib/format";
import styles from "./ContextRows.module.css";

export function ContextRows({ rows }: { rows: ContextRow[] | undefined }) {
  const sorted = [...(rows ?? [])].sort((a, b) => b.similarity_score - a.similarity_score);

  return (
    <div className={styles.wrap}>
      <h4 className={styles.title}>Similar historical precedent cases</h4>
      {sorted.length === 0 ? (
        <p className={styles.empty}>No similar historical cases retrieved.</p>
      ) : (
        <ul className={styles.list}>
          {sorted.map((row) => (
            <li key={row.reference_id} className={styles.row}>
              <span className={styles.refId}>{row.reference_id}</span>
              <span className={styles.similarity}>
                {formatPercent(row.similarity_score)} similar
              </span>
              <span className={styles.outcome} data-outcome={row.outcome ?? "UNRESOLVED"}>
                {humanizeOutcome(row.outcome)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
