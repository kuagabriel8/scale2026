import type { TypologyCategory } from "@/lib/types";
import { CATEGORY_LABEL } from "@/lib/typologies";
import { DotIcon } from "./icons";
import styles from "./CategoryChip.module.css";

// Fixed id -> CSS variable lookup. Never derived from array index / filtered
// position, so hiding categories in a filter never repaints the rest.
const CATEGORY_VAR: Record<TypologyCategory, string> = {
  placement: "var(--cat-placement)",
  layering: "var(--cat-layering)",
  structure: "var(--cat-structure)",
  geography: "var(--cat-geography)",
  behavioural_deviation: "var(--cat-behavioural_deviation)",
  sanctions: "var(--cat-sanctions)",
  predicate_offence: "var(--cat-predicate_offence)",
};

export function categoryColor(category: TypologyCategory): string {
  return CATEGORY_VAR[category] ?? "var(--ink-secondary)";
}

export function CategoryChip({
  category,
  compact = false,
}: {
  category: TypologyCategory;
  compact?: boolean;
}) {
  const color = categoryColor(category);
  return (
    <span className={`${styles.chip} ${compact ? styles.compact : ""}`} style={{ color }}>
      <DotIcon size={compact ? 7 : 8} />
      <span>{CATEGORY_LABEL[category]}</span>
    </span>
  );
}
