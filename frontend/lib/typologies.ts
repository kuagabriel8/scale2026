import type { TypologyCategory } from "./types";

/**
 * Local, read-only copy of the typology metadata from the repo-root
 * `topfraudandtables.json` (id/name/category/typology_points/narrative
 * template), trimmed to what the UI needs for labels, tooltips, and the
 * mock generator. Source of truth remains the root file - update this in
 * lockstep if that ever changes.
 */
export interface TypologyDef {
  id: string;
  name: string;
  category: TypologyCategory;
  points: number;
}

export const TYPOLOGIES: TypologyDef[] = [
  { id: "T01", name: "Structuring / Smurfing", category: "placement", points: 25 },
  { id: "T02", name: "Velocity Spike", category: "behavioural_deviation", points: 20 },
  { id: "T03", name: "Pass-through / Funnel Account", category: "layering", points: 20 },
  { id: "T04", name: "Round-tripping / Circular Flow", category: "layering", points: 30 },
  { id: "T05", name: "Shell Company Layering", category: "structure", points: 20 },
  { id: "T06", name: "Nominee / Shared-Owner Network", category: "structure", points: 25 },
  { id: "T07", name: "Ownership Opacity", category: "structure", points: 15 },
  { id: "T08", name: "Corruption / PEP Proceeds", category: "predicate_offence", points: 30 },
  { id: "T09", name: "Sanctions Evasion via Alias", category: "sanctions", points: 40 },
  { id: "T10", name: "Jurisdiction Hopping / U-turn", category: "geography", points: 20 },
  { id: "T11", name: "High-risk Corridor Exposure", category: "geography", points: 20 },
  { id: "T12", name: "New-counterparty Flooding", category: "behavioural_deviation", points: 15 },
  { id: "T13", name: "Dormant Account Reactivation", category: "behavioural_deviation", points: 20 },
];

export const TYPOLOGY_BY_ID: Record<string, TypologyDef> = Object.fromEntries(
  TYPOLOGIES.map((t) => [t.id, t])
);

/** Fixed order - the categorical palette below must line up with this. */
export const CATEGORY_ORDER: { id: TypologyCategory; label: string }[] = [
  { id: "placement", label: "Placement" },
  { id: "layering", label: "Layering" },
  { id: "structure", label: "Structure" },
  { id: "geography", label: "Geography" },
  { id: "behavioural_deviation", label: "Behavioural Deviation" },
  { id: "sanctions", label: "Sanctions" },
  { id: "predicate_offence", label: "Predicate Offence" },
];

export const CATEGORY_LABEL: Record<TypologyCategory, string> =
  Object.fromEntries(CATEGORY_ORDER.map((c) => [c.id, c.label])) as Record<
    TypologyCategory,
    string
  >;

export const REVIEW_STATUSES = [
  "PENDING",
  "IN_REVIEW",
  "ESCALATED",
  "CLEARED",
  "SAR_FILED",
] as const;

export const RISK_TIERS = ["LOW", "MEDIUM", "HIGH", "CRITICAL"] as const;
