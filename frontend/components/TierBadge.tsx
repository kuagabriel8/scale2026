import type { RiskTier } from "@/lib/types";
import {
  AlertDiamondIcon,
  CheckCircleIcon,
  SirenOctagonIcon,
  WarningTriangleIcon,
} from "./icons";
import styles from "./TierBadge.module.css";

const TIER_META: Record<
  RiskTier,
  { color: string; Icon: typeof CheckCircleIcon; label: string }
> = {
  LOW: { color: "var(--tier-low)", Icon: CheckCircleIcon, label: "Low" },
  MEDIUM: { color: "var(--tier-medium)", Icon: WarningTriangleIcon, label: "Medium" },
  HIGH: { color: "var(--tier-high)", Icon: AlertDiamondIcon, label: "High" },
  CRITICAL: { color: "var(--tier-critical)", Icon: SirenOctagonIcon, label: "Critical" },
};

export function TierBadge({ tier, compact = false }: { tier: RiskTier; compact?: boolean }) {
  const meta = TIER_META[tier] ?? TIER_META.LOW;
  const { Icon } = meta;
  return (
    <span
      className={`${styles.badge} ${compact ? styles.compact : ""}`}
      style={{ color: meta.color, borderColor: meta.color, background: `color-mix(in srgb, ${meta.color} 14%, transparent)` }}
    >
      <Icon size={compact ? 12 : 14} />
      <span>{meta.label}</span>
    </span>
  );
}
