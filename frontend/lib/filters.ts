import type { ReviewStatus, RiskAssessment, RiskTier, TypologyCategory } from "./types";

export type SortKey = "severity" | "tier" | "recent" | "typology_strength";

export interface FilterState {
  tiers: Set<RiskTier>;
  categories: Set<TypologyCategory>;
  reviewStatuses: Set<ReviewStatus>;
  sortKey: SortKey;
}

export function emptyFilterState(): FilterState {
  return {
    tiers: new Set(),
    categories: new Set(),
    reviewStatuses: new Set(),
    sortKey: "severity",
  };
}

export function assessmentCategories(a: RiskAssessment): Set<TypologyCategory> {
  const set = new Set<TypologyCategory>();
  for (const s of a.typology_signals ?? []) {
    if (s.triggered) set.add(s.category);
  }
  return set;
}

export function matchesFilters(a: RiskAssessment, filters: FilterState): boolean {
  if (filters.tiers.size > 0 && !filters.tiers.has(a.risk?.risk_tier)) return false;
  if (
    filters.reviewStatuses.size > 0 &&
    !filters.reviewStatuses.has(a.governance?.review_status)
  ) {
    return false;
  }
  if (filters.categories.size > 0) {
    const cats = assessmentCategories(a);
    let hit = false;
    for (const c of filters.categories) {
      if (cats.has(c)) {
        hit = true;
        break;
      }
    }
    if (!hit) return false;
  }
  return true;
}

export function toggleInSet<T>(set: Set<T>, value: T): Set<T> {
  const next = new Set(set);
  if (next.has(value)) next.delete(value);
  else next.add(value);
  return next;
}
