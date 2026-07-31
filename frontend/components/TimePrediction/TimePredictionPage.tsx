"use client";

import { useState } from "react";
import { ThemeToggle } from "@/components/ThemeToggle";
import { AnalystPicker } from "./AnalystPicker";
import { AnalystEtaView } from "./AnalystEtaView";
import styles from "./TimePredictionPage.module.css";

/**
 * Top-level content for /time-prediction. Fully separate feature/backend
 * concern from the risk-assessment dashboard at / - no mock mode, no
 * shared schema, always hits the real backend directly.
 */
export function TimePredictionPage() {
  const [selectedAnalyst, setSelectedAnalyst] = useState<string | null>(null);

  return (
    <div className={styles.page}>
      <header className={styles.header}>
        <div>
          <h1 className={styles.title}>Case-time prediction</h1>
          <p className={styles.subtitle}>
            Predicted time for an analyst to clear their open-case backlog, from a live
            SAP-RPT-backed model.
          </p>
        </div>
        <ThemeToggle />
      </header>

      <div className={styles.body}>
        <AnalystPicker selected={selectedAnalyst} onSelect={setSelectedAnalyst} />
        <AnalystEtaView analyst={selectedAnalyst} />
      </div>
    </div>
  );
}
