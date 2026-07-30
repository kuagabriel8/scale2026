import { MOCK_POOL_CAP } from "./config";
import { TYPOLOGIES, TYPOLOGY_BY_ID } from "./typologies";
import type {
  ComponentScores,
  ContextRow,
  GovernanceBlock,
  ReviewStatus,
  RiskAssessment,
  RiskAssessmentStreamEvent,
  RiskTier,
  TopColumnScore,
  TypologySignal,
} from "./types";

/**
 * Fabricates conformant risk_assessment_stream_event_schema.json events,
 * shaped like risk_assessment_example.json but with varied transaction ids,
 * scores, and triggered typologies - so the dashboard is fully demoable
 * without the FastAPI backend running. Toggle via NEXT_PUBLIC_DATA_SOURCE.
 */

let txnIdCounter = 40000 + Math.floor(Math.random() * 5000);
let sequenceCounter = 0;

function rand(min: number, max: number): number {
  return min + Math.random() * (max - min);
}

function randInt(min: number, max: number): number {
  return Math.floor(rand(min, max + 1));
}

function pick<T>(arr: readonly T[]): T {
  return arr[randInt(0, arr.length - 1)];
}

function uuid(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  // Fallback UUID v4-ish generator for older runtimes.
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === "x" ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

const ANALYSTS = ["M. Alvarez", "R. Chen", "S. Okafor", "T. Novak", "J. Kowalski"];
const ANOMALY_TYPES = [
  "MULTI_TYPOLOGY_CONVERGENCE",
  "VELOCITY_OUTLIER",
  "GEOGRAPHIC_OUTLIER",
  "COUNTERPARTY_NOVELTY",
  "PATTERN_BREAK",
];
const OUTCOMES = ["SAR_FILED", "TRUE_POSITIVE", "FALSE_POSITIVE", "CLOSED_NO_ACTION", null];
const CURRENCIES = ["USD", "EUR", "GBP", "SGD"];
const COUNTRIES = ["Cyprus", "Panama", "the UAE", "Malta", "the Cayman Islands", "Latvia"];

function narrativeFor(id: string): string {
  switch (id) {
    case "T01":
      return `${randInt(3, 7)} transfers of ${pick(CURRENCIES)} ${randInt(8, 9)},${randInt(
        100,
        900
      )} against a reporting threshold of ${randInt(9, 10)},000 within ${randInt(24, 72)} hours.`;
    case "T02":
      return `Transaction of USD ${randInt(80, 400)},${randInt(100, 900)} is ${(
        rand(3.0, 5.5)
      ).toFixed(1)} standard deviations above this entity's monthly baseline average.`;
    case "T03":
      return `Entity received and disbursed ${randInt(80, 98)}% of inbound funds within ${randInt(
        2,
        5
      )} days - consistent with pass-through behaviour.`;
    case "T04":
      return `Funds traced through ${randInt(3, 4)} entities returning to origin within ${randInt(
        10,
        30
      )} days, ${randInt(5, 15)}% amount variance.`;
    case "T05":
      return `Counterparty reports high revenue with fewer than ${randInt(
        2,
        5
      )} employees, incorporated in ${pick(COUNTRIES)} but headquartered elsewhere.`;
    case "T06":
      return `Beneficial owner appears across ${randInt(3, 6)} otherwise unrelated companies.`;
    case "T07":
      return `Declared beneficial ownership totals ${randInt(40, 85)}%, leaving the remainder unaccounted for.`;
    case "T08":
      return `Beneficial owner is a politically exposed person resident in a jurisdiction with a corruption index above threshold.`;
    case "T09":
      return `Beneficiary matches a sanctioned alias at ${randInt(85, 98)}% similarity.`;
    case "T10":
      return `Payment routed to a destination diverging from the beneficiary's registered country with settlement bank in ${pick(
        COUNTRIES
      )}.`;
    case "T11":
      return `Transaction involves ${pick(COUNTRIES)}, currently flagged on the FATF grey/black list.`;
    case "T12":
      return `${randInt(45, 80)}% of counterparties this period were previously unseen, against a much lower prior-period rate.`;
    case "T13":
      return `Long-dormant account recorded near-zero activity for ${randInt(
        8,
        18
      )} months before this USD ${randInt(250, 900)},000 transfer.`;
    default:
      return `${TYPOLOGY_BY_ID[id]?.name ?? id} threshold breached for this transaction.`;
  }
}

function tierFromScore(score: number): RiskTier {
  if (score >= 82) return "CRITICAL";
  if (score >= 60) return "HIGH";
  if (score >= 35) return "MEDIUM";
  return "LOW";
}

function buildTypologySignals(): { signals: TypologySignal[]; strength: number } {
  // Weighted toward 0-3 triggers, occasionally more for a "convergence" demo moment.
  const triggerCount =
    Math.random() < 0.12 ? randInt(4, 6) : Math.random() < 0.55 ? randInt(1, 2) : randInt(0, 1);
  const shuffled = [...TYPOLOGIES].sort(() => Math.random() - 0.5);
  const triggeredIds = new Set(shuffled.slice(0, triggerCount).map((t) => t.id));

  const signals: TypologySignal[] = TYPOLOGIES.map((t) => {
    const triggered = triggeredIds.has(t.id);
    return {
      id: t.id,
      name: t.name,
      category: t.category,
      triggered,
      points: triggered ? t.points : 0,
      narrative: triggered ? narrativeFor(t.id) : null,
    };
  });

  const strength = Math.min(
    100,
    signals.reduce((sum, s) => sum + s.points, 0)
  );
  return { signals, strength };
}

function buildComponentScores(signals: TypologySignal[]): ComponentScores {
  const triggeredCats = new Set(signals.filter((s) => s.triggered).map((s) => s.category));
  const base = (categoryBoost: boolean) =>
    Math.round((categoryBoost ? rand(55, 97) : rand(5, 55)) * 10) / 10;

  return {
    amount_risk_score: base(triggeredCats.has("placement")),
    frequency_risk_score: base(triggeredCats.has("behavioural_deviation")),
    geography_risk_score: base(triggeredCats.has("geography") || triggeredCats.has("sanctions")),
    counterparty_risk_score: base(
      triggeredCats.has("structure") || triggeredCats.has("predicate_offence")
    ),
    pattern_risk_score: base(triggeredCats.has("layering")),
    velocity_risk_score: base(triggeredCats.has("behavioural_deviation")),
  };
}

function buildTopColumnScores(signals: TypologySignal[]): TopColumnScore[] {
  const triggered = signals.filter((s) => s.triggered);
  const generic = ["AMOUNT_ZSCORE", "COUNTERPARTY_NOVELTY_SCORE", "CORRIDOR_RISK_SCORE", "DORMANCY_GAP_DAYS"];
  const columns = [
    ...triggered.map((s) => `${s.id}_${s.category.toUpperCase().slice(0, 12)}_SCORE`),
    ...generic,
  ].slice(0, 4);

  const raw = columns.map(() => rand(0.05, 1));
  const total = raw.reduce((a, b) => a + b, 0) || 1;
  return columns
    .map((column, i) => ({ column, contribution_score: Math.round((raw[i] / total) * 100) / 100 }))
    .sort((a, b) => b.contribution_score - a.contribution_score);
}

function buildContextRows(): ContextRow[] {
  const count = randInt(2, 3);
  const rows: ContextRow[] = Array.from({ length: count }, () => ({
    reference_id: `${pick(["CASE", "ALERT"])}-2026-${String(randInt(1, 99999)).padStart(5, "0")}`,
    similarity_score: Math.round(rand(0.55, 0.97) * 100) / 100,
    outcome: pick(OUTCOMES),
  }));
  return rows.sort((a, b) => b.similarity_score - a.similarity_score);
}

function buildNarrativeText(signals: TypologySignal[], strength: number): string {
  const triggered = signals.filter((s) => s.triggered);
  if (triggered.length === 0) {
    return "No deterministic typology signals triggered; risk score is driven by ML component scores alone.";
  }
  const names = triggered.map((s) => `${s.name} (${s.id})`).join(", ");
  return `Flagged for ${names}; combined typology strength reached ${strength} point${
    strength === 1 ? "" : "s"
  }.`;
}

function freshGovernance(): GovernanceBlock {
  return { requires_human_review: true, review_status: "PENDING", reviewed_by: null };
}

export function createMockAssessment(): RiskAssessment {
  const { signals, strength } = buildTypologySignals();
  const overallRaw = strength * 0.55 + rand(0, 45);
  const overall = Math.min(100, Math.round(overallRaw * 10) / 10);
  const tier = tierFromScore(overall);
  const isAnomaly =
    (tier === "HIGH" || tier === "CRITICAL") ? Math.random() < 0.6 : Math.random() < 0.05;

  txnIdCounter += 1;

  return {
    transaction_id: txnIdCounter,
    transaction_uuid: uuid(),
    scored_at: new Date().toISOString(),
    model_version: "mock-generator-v1",
    risk: {
      overall_risk_score: overall,
      risk_tier: tier,
      model_confidence: Math.round(rand(0.35, 0.97) * 100) / 100,
      is_anomaly: isAnomaly,
      anomaly_type: isAnomaly ? pick(ANOMALY_TYPES) : null,
      component_scores: buildComponentScores(signals),
    },
    typology_signals: signals,
    exposure: {
      typology_strength_points: strength,
      exposure_factor: Math.round(strength * 0.2 * 10) / 10,
    },
    explanation: {
      top_column_scores: buildTopColumnScores(signals),
      top_relevant_context_rows: buildContextRows(),
      narrative_text: buildNarrativeText(signals, strength),
    },
    governance: freshGovernance(),
  };
}

/** Simulates a re-score of an existing transaction (ASSESSMENT_UPDATED). */
export function rescoreMockAssessment(existing: RiskAssessment): RiskAssessment {
  const next = createMockAssessment();
  return {
    ...next,
    transaction_id: existing.transaction_id,
    transaction_uuid: existing.transaction_uuid,
    governance: existing.governance,
  };
}

const TRANSITIONS: Record<ReviewStatus, ReviewStatus[]> = {
  PENDING: ["IN_REVIEW", "ESCALATED"],
  IN_REVIEW: ["ESCALATED", "CLEARED", "SAR_FILED"],
  ESCALATED: ["SAR_FILED", "CLEARED"],
  CLEARED: ["IN_REVIEW"],
  SAR_FILED: ["IN_REVIEW"],
};

export function nextMockReviewStatus(current: ReviewStatus): ReviewStatus {
  const options = TRANSITIONS[current] ?? ["IN_REVIEW"];
  return pick(options);
}

interface Pool {
  ids: number[];
  byId: Map<number, RiskAssessment>;
}

/** Maintains a small in-memory pool so UPDATED/REVIEW_STATUS_CHANGED events
 * have real prior transactions to reference. */
export class MockStream {
  private pool: Pool = { ids: [], byId: new Map() };

  seed(count: number): RiskAssessmentStreamEvent[] {
    const events: RiskAssessmentStreamEvent[] = [];
    for (let i = 0; i < count; i++) {
      const assessment = createMockAssessment();
      this.remember(assessment);
      events.push(this.envelope("ASSESSMENT_CREATED", assessment));
    }
    return events;
  }

  next(): RiskAssessmentStreamEvent {
    const r = Math.random();
    if (this.pool.ids.length > 3 && r < 0.18) {
      const id = pick(this.pool.ids);
      const existing = this.pool.byId.get(id)!;
      const newStatus = nextMockReviewStatus(existing.governance.review_status);
      const updated: RiskAssessment = {
        ...existing,
        governance: {
          requires_human_review: true,
          review_status: newStatus,
          reviewed_by: pick(ANALYSTS),
        },
      };
      this.remember(updated);
      return this.envelope("REVIEW_STATUS_CHANGED", updated);
    }

    if (this.pool.ids.length > 3 && r < 0.4) {
      const id = pick(this.pool.ids);
      const existing = this.pool.byId.get(id)!;
      const updated = rescoreMockAssessment(existing);
      this.remember(updated);
      return this.envelope("ASSESSMENT_UPDATED", updated);
    }

    const created = createMockAssessment();
    this.remember(created);
    return this.envelope("ASSESSMENT_CREATED", created);
  }

  private remember(assessment: RiskAssessment) {
    if (!this.pool.byId.has(assessment.transaction_id)) {
      this.pool.ids.push(assessment.transaction_id);
      if (this.pool.ids.length > MOCK_POOL_CAP) {
        const dropped = this.pool.ids.shift();
        if (dropped !== undefined) this.pool.byId.delete(dropped);
      }
    }
    this.pool.byId.set(assessment.transaction_id, assessment);
  }

  private envelope(
    eventType: RiskAssessmentStreamEvent["event_type"],
    assessment: RiskAssessment
  ): RiskAssessmentStreamEvent {
    sequenceCounter += 1;
    return {
      event_type: eventType,
      sequence: sequenceCounter,
      emitted_at: new Date().toISOString(),
      assessment,
    };
  }
}
