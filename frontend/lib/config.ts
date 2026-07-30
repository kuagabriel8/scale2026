export type DataSource = "mock" | "live";

export const DATA_SOURCE: DataSource =
  (process.env.NEXT_PUBLIC_DATA_SOURCE as DataSource) === "live"
    ? "live"
    : "mock";

export const WS_URL =
  process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8000/ws/risk-stream";

// Interval bounds (ms) for the mock stream generator.
export const MOCK_MIN_INTERVAL_MS = 1600;
export const MOCK_MAX_INTERVAL_MS = 4200;

// How many synthetic assessments to seed immediately on mount so the
// dashboard is never empty in mock mode.
export const MOCK_SEED_COUNT = 16;

// Cap on how many distinct mock transactions we keep "alive" for
// ASSESSMENT_UPDATED / REVIEW_STATUS_CHANGED events to reference.
export const MOCK_POOL_CAP = 60;
