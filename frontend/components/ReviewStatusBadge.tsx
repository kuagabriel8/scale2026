import type { ReviewStatus } from "@/lib/types";
import styles from "./ReviewStatusBadge.module.css";

const LABEL: Record<ReviewStatus, string> = {
  PENDING: "Pending review",
  IN_REVIEW: "In review",
  ESCALATED: "Escalated",
  CLEARED: "Cleared by analyst",
  SAR_FILED: "SAR filed",
};

export function ReviewStatusBadge({ status }: { status: ReviewStatus }) {
  return (
    <span className={styles.badge} data-status={status}>
      {LABEL[status] ?? status}
    </span>
  );
}
