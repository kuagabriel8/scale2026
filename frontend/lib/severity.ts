import type { RiskAssessment, RiskTier } from "./types";

/**
 * Severity formula (documented in frontend/README.md):
 *
 *   severity = 0.65 * risk.overall_risk_score
 *            + 0.35 * exposure.typology_strength_points
 *
 * Both inputs are already normalized to a 0-100 range by the contract, so no
 * extra scaling is needed. The 65/35 weighting favors the ML/RPT score
 * (risk.overall_risk_score) as the primary driver while still letting a
 * transaction with heavy deterministic typology exposure (exposure.typology_
 * strength_points) but a not-yet-scored/low ML confidence still surface high
 * in the list - important since model_version can be "rule-engine-only"
 * (no ML score yet) and the rule layer is always populated.
 *
 * This is computed client-side unconditionally (not just as a fallback) so
 * ranking behaves identically whether the backend is reachable or not.
 */
export const SEVERITY_WEIGHT_RISK_SCORE = 0.65;
export const SEVERITY_WEIGHT_TYPOLOGY_STRENGTH = 0.35;

export function computeSeverity(assessment: RiskAssessment): number {
  const riskScore = assessment.risk?.overall_risk_score ?? 0;
  const strength = assessment.exposure?.typology_strength_points ?? 0;
  const severity =
    SEVERITY_WEIGHT_RISK_SCORE * riskScore +
    SEVERITY_WEIGHT_TYPOLOGY_STRENGTH * strength;
  return Math.round(severity * 10) / 10;
}

const TIER_RANK: Record<RiskTier, number> = {
  CRITICAL: 3,
  HIGH: 2,
  MEDIUM: 1,
  LOW: 0,
};

export function tierRank(tier: RiskTier): number {
  return TIER_RANK[tier] ?? -1;
}

/** Sort comparator: highest severity first, tie-broken by tier then recency. */
export function bySeverityDesc(a: RiskAssessment, b: RiskAssessment): number {
  const sa = computeSeverity(a);
  const sb = computeSeverity(b);
  if (sb !== sa) return sb - sa;
  const ta = tierRank(a.risk?.risk_tier);
  const tb = tierRank(b.risk?.risk_tier);
  if (tb !== ta) return tb - ta;
  return (
    new Date(b.scored_at).getTime() - new Date(a.scored_at).getTime()
  );
}
