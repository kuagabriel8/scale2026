// Typed REST client for the "analyst case-time prediction" feature. This is
// a fully separate backend concern from lib/api.ts (no mock mode, no shared
// schema with risk_assessment_schema.json) - it always hits the real
// FastAPI backend directly. See ../backend README for the endpoints.

import { API_BASE_URL } from "./config";

/** One entry from GET /time-prediction/analysts, sorted open_case_count desc. */
export interface AnalystSummary {
  analyst: string;
  open_case_count: number;
}

/** One open case within GET /time-prediction/analysts/{analyst}/eta. */
export interface PredictedCase {
  alert_id: number;
  alert_type: string;
  alert_subtype: string;
  alert_priority: string;
  assigned_at: string;
  predicted_hours: number;
}

/** GET /time-prediction/analysts/{analyst}/eta response. */
export interface AnalystEta {
  analyst: string;
  open_case_count: number;
  total_predicted_hours: number;
  generated_at: string;
  cases: PredictedCase[];
}

// The ETA endpoint makes one live SAP-RPT regression call per open case
// (parallelized backend-side) and has been observed to take ~18s wall-clock
// for ~50 open cases. Give it a generous default timeout so a real analyst
// backlog doesn't get killed by a typical fetch default.
const DEFAULT_ETA_TIMEOUT_MS = 90_000;

async function apiFetch<T>(path: string, timeoutMs?: number): Promise<T> {
  const controller = new AbortController();
  const timer = timeoutMs
    ? setTimeout(() => controller.abort(), timeoutMs)
    : null;
  try {
    const res = await fetch(`${API_BASE_URL}${path}`, {
      headers: { "Content-Type": "application/json" },
      signal: controller.signal,
    });
    if (!res.ok) {
      throw new Error(`API ${path} failed: ${res.status} ${res.statusText}`);
    }
    return res.json() as Promise<T>;
  } finally {
    if (timer) clearTimeout(timer);
  }
}

/** Populate the analyst picker. Sorted by open_case_count descending. */
export function listAnalysts(): Promise<AnalystSummary[]> {
  return apiFetch<AnalystSummary[]>("/time-prediction/analysts");
}

/**
 * Fetch the full backlog-clear-time prediction for one analyst. Slow by
 * design (one live regression call per open case) - callers must show a
 * loading state, not assume this resolves quickly. Throws on 404 (unknown
 * analyst) and 422 (too few historical candidates for the model backend-side).
 */
export function getAnalystEta(
  analyst: string,
  opts: { timeoutMs?: number } = {}
): Promise<AnalystEta> {
  return apiFetch<AnalystEta>(
    `/time-prediction/analysts/${encodeURIComponent(analyst)}/eta`,
    opts.timeoutMs ?? DEFAULT_ETA_TIMEOUT_MS
  );
}
