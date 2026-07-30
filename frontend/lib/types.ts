// Mirrors risk_assessment_schema.json and risk_assessment_stream_event_schema.json
// at the repo root. Keep in sync with those contracts.

export type RiskTier = "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";

export type ReviewStatus =
  | "PENDING"
  | "IN_REVIEW"
  | "ESCALATED"
  | "CLEARED"
  | "SAR_FILED";

export type TypologyCategory =
  | "placement"
  | "layering"
  | "structure"
  | "geography"
  | "behavioural_deviation"
  | "sanctions"
  | "predicate_offence";

export interface TypologySignal {
  id: string; // T01-T13
  name: string;
  category: TypologyCategory;
  triggered: boolean;
  points: number;
  narrative: string | null;
}

export interface ComponentScores {
  amount_risk_score?: number;
  frequency_risk_score?: number;
  geography_risk_score?: number;
  counterparty_risk_score?: number;
  pattern_risk_score?: number;
  velocity_risk_score?: number;
}

export interface RiskBlock {
  overall_risk_score: number;
  risk_tier: RiskTier;
  model_confidence: number;
  is_anomaly: boolean;
  anomaly_type?: string | null;
  component_scores?: ComponentScores;
}

export interface ExposureBlock {
  typology_strength_points: number;
  exposure_factor: number;
}

export interface TopColumnScore {
  column: string;
  contribution_score: number;
}

export interface ContextRow {
  reference_id: string;
  similarity_score: number;
  outcome?: string | null;
}

export interface ExplanationBlock {
  top_column_scores?: TopColumnScore[];
  top_relevant_context_rows?: ContextRow[];
  narrative_text?: string | null;
}

export interface GovernanceBlock {
  requires_human_review: true;
  review_status: ReviewStatus;
  reviewed_by?: string | null;
}

export interface RiskAssessment {
  transaction_id: number;
  transaction_uuid: string;
  scored_at: string;
  model_version: string;
  risk: RiskBlock;
  typology_signals: TypologySignal[];
  exposure: ExposureBlock;
  explanation: ExplanationBlock;
  governance: GovernanceBlock;
}

export type StreamEventType =
  | "ASSESSMENT_CREATED"
  | "ASSESSMENT_UPDATED"
  | "REVIEW_STATUS_CHANGED";

export interface RiskAssessmentStreamEvent {
  event_type: StreamEventType;
  sequence: number;
  emitted_at: string;
  assessment: RiskAssessment;
}

export type ConnectionStatus =
  | "connecting"
  | "connected"
  | "reconnecting"
  | "disconnected"
  | "error"
  | "mock";
