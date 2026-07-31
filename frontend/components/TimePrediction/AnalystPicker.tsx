"use client";

import { useEffect, useState } from "react";
import { listAnalysts } from "@/lib/timePredictionApi";
import type { AnalystSummary } from "@/lib/timePredictionApi";
import styles from "./AnalystPicker.module.css";

/**
 * Populates from GET /time-prediction/analysts (already sorted by
 * open_case_count descending) and lets the user select one analyst.
 */
export function AnalystPicker({
  selected,
  onSelect,
}: {
  selected: string | null;
  onSelect: (analyst: string) => void;
}) {
  const [analysts, setAnalysts] = useState<AnalystSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    listAnalysts()
      .then((list) => {
        if (!cancelled) setAnalysts(list);
      })
      .catch((err) => {
        if (!cancelled) {
          setError(
            err instanceof Error
              ? err.message
              : "Failed to load analysts from the backend."
          );
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className={styles.wrap}>
      <span className={styles.label}>Analyst</span>
      {error && (
        <div className={styles.error}>
          Could not load analysts - is the backend running? ({error})
        </div>
      )}
      {!error && analysts === null && <div className={styles.empty}>Loading analysts...</div>}
      {!error && analysts !== null && analysts.length === 0 && (
        <div className={styles.empty}>No analysts found.</div>
      )}
      {!error && analysts !== null && analysts.length > 0 && (
        <div className={styles.list} role="listbox" aria-label="Select analyst">
          {analysts.map((a) => (
            <button
              key={a.analyst}
              type="button"
              role="option"
              aria-selected={selected === a.analyst}
              className={styles.row}
              data-active={selected === a.analyst}
              onClick={() => onSelect(a.analyst)}
            >
              <span className={styles.name}>{a.analyst}</span>
              <span className={styles.count}>
                {a.open_case_count} open case{a.open_case_count === 1 ? "" : "s"}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
