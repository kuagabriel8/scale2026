"use client";

import { useEffect, useState } from "react";
import { getModels } from "@/lib/api";
import type { ModelInfo } from "@/lib/api";
import styles from "./ModelPicker.module.css";

/**
 * Lets the analyst pick which backend scorer (GET /models) is used for
 * fetched/refreshed data (the initial seed fetch and the detail-drawer's
 * per-transaction fetch). It cannot affect the live WS stream, which is one
 * shared broadcast always scored with the server's configured default -
 * see the caption below, which must stay honest about that limitation.
 */
export function ModelPicker({
  value,
  onChange,
  disabled = false,
}: {
  value: string | null;
  onChange: (modelId: string) => void;
  disabled?: boolean;
}) {
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [error, setError] = useState(false);

  useEffect(() => {
    if (disabled) return;
    let cancelled = false;
    getModels()
      .then((list) => {
        if (cancelled) return;
        setModels(list);
        if (list.length > 0 && !value) onChange(list[0].id);
      })
      .catch(() => {
        if (!cancelled) setError(true);
      });
    return () => {
      cancelled = true;
    };
    // Only fetch once on mount (or once un-disabled).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [disabled]);

  if (disabled) {
    return (
      <div className={styles.wrap} data-disabled="true">
        <span className={styles.groupLabel}>Scoring model</span>
        <span className={styles.disabledNote} title="Model selection requires the live backend">
          n/a in mock mode
        </span>
      </div>
    );
  }

  if (error) {
    return (
      <div className={styles.wrap}>
        <span className={styles.groupLabel}>Scoring model</span>
        <span className={styles.disabledNote}>unavailable</span>
      </div>
    );
  }

  return (
    <div className={styles.wrap}>
      <span className={styles.groupLabel}>Scoring model</span>
      <div className={styles.segmented} role="radiogroup" aria-label="Scoring model">
        {models.map((m) => (
          <button
            key={m.id}
            type="button"
            role="radio"
            aria-checked={value === m.id}
            className={styles.segment}
            data-active={value === m.id}
            title={m.description}
            onClick={() => onChange(m.id)}
          >
            {m.label}
          </button>
        ))}
      </div>
      <span className={styles.caption}>
        Applies to fetched/refreshed data only - the live stream always scores with the
        server&apos;s default model.
      </span>
    </div>
  );
}
