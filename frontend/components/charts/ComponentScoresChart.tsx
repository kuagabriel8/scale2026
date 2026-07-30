import type { ComponentScores } from "@/lib/types";
import { mixHex } from "@/lib/color";
import { formatScore } from "@/lib/format";
import styles from "./ComponentScoresChart.module.css";

const ROWS: { key: keyof ComponentScores; label: string }[] = [
  { key: "amount_risk_score", label: "Amount" },
  { key: "frequency_risk_score", label: "Frequency" },
  { key: "geography_risk_score", label: "Geography" },
  { key: "counterparty_risk_score", label: "Counterparty" },
  { key: "pattern_risk_score", label: "Pattern" },
  { key: "velocity_risk_score", label: "Velocity" },
];

/**
 * Magnitude comparison across 6 same-unit (0-100) component scores.
 * Single horizontal-bar series (one x-axis, no dual scales, no radar -
 * radar charts distort area perception). Single series => no legend
 * required; bar fill uses the sequential blue ramp to reinforce magnitude.
 */
export function ComponentScoresChart({ scores }: { scores: ComponentScores | undefined }) {
  const rows = ROWS.map((r) => ({ ...r, value: scores?.[r.key] }));
  const hasAny = rows.some((r) => typeof r.value === "number");

  return (
    <div className={styles.wrap}>
      <h4 className={styles.title}>Component risk scores</h4>
      {!hasAny ? (
        <p className={styles.empty}>No component scores reported for this assessment.</p>
      ) : (
        <>
          <div className={styles.rows}>
            {rows.map((r) => {
              const value = r.value ?? 0;
              const pct = Math.max(0, Math.min(100, value));
              const color = mixHex("#cde2fb", "#2a78d6", pct / 100);
              return (
                <div className={styles.row} key={r.key}>
                  <span className={styles.label}>{r.label}</span>
                  <div className={styles.track}>
                    <div
                      className={styles.bar}
                      style={{ width: `${pct}%`, background: color }}
                    />
                  </div>
                  <span className={styles.value}>
                    {typeof r.value === "number" ? formatScore(r.value) : "—"}
                  </span>
                </div>
              );
            })}
          </div>
          <div className={styles.axis}>
            <div className={styles.axisSpacer} aria-hidden="true" />
            <div className={styles.axisTicks}>
              <span>0</span>
              <span>25</span>
              <span>50</span>
              <span>75</span>
              <span>100</span>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
