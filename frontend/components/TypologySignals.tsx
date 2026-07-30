import type { TypologySignal } from "@/lib/types";
import { CategoryChip } from "./CategoryChip";
import styles from "./TypologySignals.module.css";

/**
 * The auditable "why" - presented as the primary explanation for a
 * transaction's risk, not an afterthought. Triggered typologies are shown
 * first as expanded cards with their grounded narrative; the remaining
 * (not-triggered) typologies are available but collapsed by default.
 */
export function TypologySignals({ signals }: { signals: TypologySignal[] }) {
  const triggered = signals.filter((s) => s.triggered);
  const notTriggered = signals.filter((s) => !s.triggered);

  return (
    <div className={styles.wrap}>
      <h4 className={styles.title}>
        Typology signals - the auditable basis for this assessment
      </h4>

      {triggered.length === 0 ? (
        <p className={styles.emptyNote}>
          No deterministic typology rule triggered for this transaction. Any elevated risk score
          below is driven solely by the ML/RPT layer.
        </p>
      ) : (
        <ul className={styles.list}>
          {triggered.map((s) => (
            <li key={s.id} className={styles.card}>
              <div className={styles.cardHead}>
                <span className={styles.id}>{s.id}</span>
                <span className={styles.name}>{s.name}</span>
                <CategoryChip category={s.category} />
                <span className={styles.points}>+{s.points} pts</span>
              </div>
              {s.narrative && <p className={styles.narrative}>{s.narrative}</p>}
            </li>
          ))}
        </ul>
      )}

      {notTriggered.length > 0 && (
        <details className={styles.details}>
          <summary>
            {notTriggered.length} typolog{notTriggered.length === 1 ? "y" : "ies"} evaluated and
            not triggered
          </summary>
          <ul className={styles.notList}>
            {notTriggered.map((s) => (
              <li key={s.id} className={styles.notItem}>
                <span className={styles.id}>{s.id}</span>
                <span>{s.name}</span>
                <CategoryChip category={s.category} compact />
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
