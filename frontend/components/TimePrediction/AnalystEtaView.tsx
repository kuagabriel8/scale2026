"use client";

import { useEffect, useState } from "react";
import { getAnalystEta } from "@/lib/timePredictionApi";
import type { AnalystEta } from "@/lib/timePredictionApi";
import { formatDateTime, formatPredictedHours, formatYearsHeadline } from "@/lib/formatHours";
import { PriorityBadge } from "./PriorityBadge";
import styles from "./AnalystEtaView.module.css";

type ResultState =
  | { analyst: string; status: "error"; message: string }
  | { analyst: string; status: "ready"; eta: AnalystEta };

/**
 * Fetches GET /time-prediction/analysts/{analyst}/eta for the selected
 * analyst. This call is slow by design (~18s+ for ~50 open cases, one live
 * SAP-RPT regression per case) so it always shows an explicit loading state
 * rather than a frozen screen, and uses a 90s client timeout.
 *
 * "Loading" is derived (rather than tracked as its own state) by comparing
 * the analyst currently selected against the analyst the last-settled result
 * belongs to, so a new selection is shown as loading immediately even before
 * the fetch promise resolves.
 */
export function AnalystEtaView({ analyst }: { analyst: string | null }) {
  const [result, setResult] = useState<ResultState | null>(null);

  useEffect(() => {
    if (!analyst) return;
    let cancelled = false;
    getAnalystEta(analyst)
      .then((eta) => {
        if (!cancelled) setResult({ analyst, status: "ready", eta });
      })
      .catch((err) => {
        if (cancelled) return;
        const message =
          err instanceof Error ? err.message : "Failed to fetch the ETA prediction.";
        setResult({ analyst, status: "error", message });
      });
    return () => {
      cancelled = true;
    };
  }, [analyst]);

  if (!analyst) {
    return (
      <div className={styles.placeholder}>Pick an analyst to see their predicted backlog-clear time.</div>
    );
  }

  const loading = !result || result.analyst !== analyst;

  if (loading) {
    return (
      <div className={styles.wrap}>
        <div className={styles.loading}>
          <span className={styles.spinner} aria-hidden="true" />
          <span>
            Running live SAP-RPT predictions for every open case assigned to{" "}
            <strong>{analyst}</strong>&hellip; this makes one model call per case and can take
            20&ndash;30 seconds for a large backlog.
          </span>
        </div>
      </div>
    );
  }

  if (result.status === "error") {
    return (
      <div className={styles.error}>
        Could not load the ETA prediction for <strong>{analyst}</strong>: {result.message}
      </div>
    );
  }

  const { eta } = result;

  if (eta.open_case_count === 0 || eta.cases.length === 0) {
    return (
      <div className={styles.emptyState}>
        <strong>{eta.analyst}</strong> has no open cases right now - nothing to predict.
      </div>
    );
  }

  return (
    <div className={styles.wrap}>
      <div className={styles.summary}>
        <span className={styles.summaryHeadline}>
          {formatYearsHeadline(eta.total_predicted_hours)} to clear all {eta.open_case_count} open
          case{eta.open_case_count === 1 ? "" : "s"}
        </span>
        <span className={styles.summaryDetail}>
          {Math.round(eta.total_predicted_hours).toLocaleString()} total predicted hours across{" "}
          {eta.analyst}&apos;s backlog &middot; generated {formatDateTime(eta.generated_at)}
        </span>
        <span className={styles.caveat}>
          These are model-estimated hours derived from historical assigned-to-resolved case
          durations in this dataset, not literal person-hours of continuous work - treat them as
          a relative workload signal for prioritization, not a literal staffing estimate.
        </span>
      </div>

      <div className={styles.tableWrap}>
        <table className={styles.table}>
          <thead>
            <tr>
              <th>Alert</th>
              <th>Type</th>
              <th>Priority</th>
              <th>Assigned</th>
              <th>Predicted duration</th>
            </tr>
          </thead>
          <tbody>
            {eta.cases.map((c) => (
              <tr key={c.alert_id}>
                <td>#{c.alert_id}</td>
                <td className={styles.type}>
                  {c.alert_type}
                  <div className={styles.subtype}>{c.alert_subtype}</div>
                </td>
                <td>
                  <PriorityBadge priority={c.alert_priority} />
                </td>
                <td>{formatDateTime(c.assigned_at)}</td>
                <td className={styles.hours}>{formatPredictedHours(c.predicted_hours)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
