"use client";

import type { FilterState, SortKey } from "@/lib/filters";
import { toggleInSet } from "@/lib/filters";
import { CATEGORY_ORDER, REVIEW_STATUSES, RISK_TIERS } from "@/lib/typologies";
import type { ReviewStatus, RiskTier, TypologyCategory } from "@/lib/types";
import { categoryColor } from "./CategoryChip";
import styles from "./Filters.module.css";

const TIER_COLOR: Record<RiskTier, string> = {
  LOW: "var(--tier-low)",
  MEDIUM: "var(--tier-medium)",
  HIGH: "var(--tier-high)",
  CRITICAL: "var(--tier-critical)",
};

const SORT_OPTIONS: { key: SortKey; label: string }[] = [
  { key: "severity", label: "Severity (default)" },
  { key: "tier", label: "Risk tier" },
  { key: "typology_strength", label: "Typology strength" },
  { key: "recent", label: "Most recently scored" },
];

export function Filters({
  filters,
  onChange,
  onReset,
}: {
  filters: FilterState;
  onChange: (next: FilterState) => void;
  onReset: () => void;
}) {
  const hasActive =
    filters.tiers.size + filters.categories.size + filters.reviewStatuses.size > 0;

  return (
    <div className={styles.wrap}>
      <div className={styles.group}>
        <span className={styles.groupLabel}>Sort by</span>
        <select
          className={styles.select}
          value={filters.sortKey}
          onChange={(e) => onChange({ ...filters, sortKey: e.target.value as SortKey })}
        >
          {SORT_OPTIONS.map((o) => (
            <option key={o.key} value={o.key}>
              {o.label}
            </option>
          ))}
        </select>
      </div>

      <div className={styles.group}>
        <span className={styles.groupLabel}>Risk tier</span>
        <div className={styles.pills}>
          {RISK_TIERS.map((tier) => (
            <button
              key={tier}
              type="button"
              className={styles.pill}
              data-active={filters.tiers.has(tier)}
              style={{ ["--pill-color" as string]: TIER_COLOR[tier] }}
              onClick={() => onChange({ ...filters, tiers: toggleInSet(filters.tiers, tier) })}
            >
              {tier}
            </button>
          ))}
        </div>
      </div>

      <div className={styles.group}>
        <span className={styles.groupLabel}>Typology category</span>
        <div className={styles.pills}>
          {CATEGORY_ORDER.map(({ id, label }) => (
            <button
              key={id}
              type="button"
              className={styles.pill}
              data-active={filters.categories.has(id)}
              style={{ ["--pill-color" as string]: categoryColor(id as TypologyCategory) }}
              onClick={() =>
                onChange({ ...filters, categories: toggleInSet(filters.categories, id) })
              }
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      <div className={styles.group}>
        <span className={styles.groupLabel}>Review status</span>
        <div className={styles.pills}>
          {REVIEW_STATUSES.map((status) => (
            <button
              key={status}
              type="button"
              className={styles.pill}
              data-active={filters.reviewStatuses.has(status)}
              style={{ ["--pill-color" as string]: "var(--ink-secondary)" }}
              onClick={() =>
                onChange({
                  ...filters,
                  reviewStatuses: toggleInSet<ReviewStatus>(filters.reviewStatuses, status),
                })
              }
            >
              {status.replace("_", " ")}
            </button>
          ))}
        </div>
      </div>

      {hasActive && (
        <button type="button" className={styles.reset} onClick={onReset}>
          Clear filters
        </button>
      )}
    </div>
  );
}
