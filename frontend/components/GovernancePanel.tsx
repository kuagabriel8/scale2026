"use client";

import { useState } from "react";
import type { GovernanceBlock, ReviewStatus } from "@/lib/types";
import { REVIEW_STATUSES } from "@/lib/typologies";
import { ReviewStatusBadge } from "./ReviewStatusBadge";
import styles from "./GovernancePanel.module.css";

/**
 * Governance is a manual analyst action, never an automated one - there is
 * no autonomous blocking/clearing in this system. The copy here must always
 * read as "an analyst recorded a decision", not "system auto-cleared".
 */
export function GovernancePanel({
  governance,
  onRecord,
}: {
  governance: GovernanceBlock;
  onRecord: (status: ReviewStatus, reviewedBy: string) => void;
}) {
  const [status, setStatus] = useState<ReviewStatus>(governance.review_status);
  const [reviewedBy, setReviewedBy] = useState(governance.reviewed_by ?? "");
  const [justSaved, setJustSaved] = useState(false);

  const dirty = status !== governance.review_status || reviewedBy !== (governance.reviewed_by ?? "");

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    onRecord(status, reviewedBy.trim());
    setJustSaved(true);
    setTimeout(() => setJustSaved(false), 2000);
  }

  return (
    <div className={styles.wrap}>
      <h4 className={styles.title}>Governance - human review required</h4>
      <p className={styles.note}>
        This system never blocks or clears a transaction automatically. Every review status
        change below is a manual decision made and attributed to a named analyst.
      </p>

      <div className={styles.current}>
        <span className={styles.currentLabel}>Current status</span>
        <ReviewStatusBadge status={governance.review_status} />
        <span className={styles.reviewer}>
          {governance.reviewed_by
            ? `last recorded by ${governance.reviewed_by}`
            : "no analyst has recorded a decision yet"}
        </span>
      </div>

      <form className={styles.form} onSubmit={handleSubmit}>
        <label className={styles.field}>
          <span>Set review status</span>
          <select value={status} onChange={(e) => setStatus(e.target.value as ReviewStatus)}>
            {REVIEW_STATUSES.map((s) => (
              <option key={s} value={s}>
                {s.replace("_", " ")}
              </option>
            ))}
          </select>
        </label>

        <label className={styles.field}>
          <span>Analyst name</span>
          <input
            type="text"
            placeholder="e.g. J. Alvarez"
            value={reviewedBy}
            onChange={(e) => setReviewedBy(e.target.value)}
          />
        </label>

        <button type="submit" className={styles.submit} disabled={!dirty}>
          {justSaved ? "Decision recorded" : "Record analyst decision"}
        </button>
      </form>
    </div>
  );
}
