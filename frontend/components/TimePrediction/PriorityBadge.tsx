import styles from "./PriorityBadge.module.css";

// alert_priority is a free-form string from the time-prediction backend
// (not part of risk_assessment_schema.json's risk_tier enum) - we reuse the
// same LOW/MEDIUM/HIGH/CRITICAL status palette from globals.css where the
// value matches one of those tiers (case-insensitive) and fall back to a
// neutral badge for anything else, since the contract doesn't guarantee an
// exact enum.
const KNOWN_COLOR: Record<string, string> = {
  LOW: "var(--tier-low)",
  MEDIUM: "var(--tier-medium)",
  HIGH: "var(--tier-high)",
  CRITICAL: "var(--tier-critical)",
};

export function PriorityBadge({ priority }: { priority: string }) {
  const color = KNOWN_COLOR[priority.toUpperCase()] ?? "var(--ink-secondary)";
  return (
    <span
      className={styles.badge}
      style={{
        color,
        borderColor: color,
        background: `color-mix(in srgb, ${color} 14%, transparent)`,
      }}
    >
      {priority.toLowerCase()}
    </span>
  );
}
