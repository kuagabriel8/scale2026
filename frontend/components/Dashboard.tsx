"use client";

import { useCallback, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useRiskStream } from "@/lib/useRiskStream";
import { bySeverityDesc, computeSeverity, tierRank } from "@/lib/severity";
import { emptyFilterState, matchesFilters } from "@/lib/filters";
import type { FilterState } from "@/lib/filters";
import type { RiskAssessment } from "@/lib/types";
import { RiskTable } from "./RiskTable";
import { Filters } from "./Filters";
import { DataSourceStatus } from "./DataSourceStatus";
import { ThemeToggle } from "./ThemeToggle";
import { DetailDrawer } from "./DetailDrawer";
import styles from "./Dashboard.module.css";

function sortAssessments(list: RiskAssessment[], sortKey: FilterState["sortKey"]) {
  const copy = [...list];
  switch (sortKey) {
    case "tier":
      return copy.sort((a, b) => {
        const t = tierRank(b.risk.risk_tier) - tierRank(a.risk.risk_tier);
        return t !== 0 ? t : bySeverityDesc(a, b);
      });
    case "typology_strength":
      return copy.sort((a, b) => {
        const d = b.exposure.typology_strength_points - a.exposure.typology_strength_points;
        return d !== 0 ? d : bySeverityDesc(a, b);
      });
    case "recent":
      return copy.sort(
        (a, b) => new Date(b.scored_at).getTime() - new Date(a.scored_at).getTime()
      );
    case "severity":
    default:
      return copy.sort(bySeverityDesc);
  }
}

export function Dashboard() {
  const { assessments, status, dataSource, eventCount, recordReviewDecision } = useRiskStream();
  const [filters, setFilters] = useState<FilterState>(emptyFilterState);
  const router = useRouter();
  const searchParams = useSearchParams();
  const selectedIdParam = searchParams.get("tx");
  const selectedId = selectedIdParam ? Number(selectedIdParam) : null;

  const selectAssessment = useCallback(
    (transactionId: number | null) => {
      const params = new URLSearchParams(searchParams.toString());
      if (transactionId === null) params.delete("tx");
      else params.set("tx", String(transactionId));
      router.replace(params.size ? `/?${params.toString()}` : "/", { scroll: false });
    },
    [router, searchParams]
  );

  const filtered = useMemo(
    () => assessments.filter((a) => matchesFilters(a, filters)),
    [assessments, filters]
  );
  const sorted = useMemo(() => sortAssessments(filtered, filters.sortKey), [filtered, filters.sortKey]);
  const selected = useMemo(
    () => assessments.find((a) => a.transaction_id === selectedId) ?? null,
    [assessments, selectedId]
  );

  const critical = assessments.filter((a) => a.risk.risk_tier === "CRITICAL").length;
  const pendingReview = assessments.filter(
    (a) => a.governance.review_status === "PENDING"
  ).length;
  const avgSeverity =
    assessments.length > 0
      ? Math.round(
          (assessments.reduce((sum, a) => sum + computeSeverity(a), 0) / assessments.length) * 10
        ) / 10
      : 0;

  return (
    <div className={styles.page}>
      <header className={styles.header}>
        <div>
          <h1 className={styles.title}>SightLine</h1>
          <p className={styles.subtitle}>AML / fraud-triage - severity-ranked transaction review</p>
        </div>
        <div className={styles.headerRight}>
          <DataSourceStatus dataSource={dataSource} status={status} eventCount={eventCount} />
          <ThemeToggle />
        </div>
      </header>

      <section className={styles.stats}>
        <div className={styles.stat}>
          <span className={styles.statValue}>{assessments.length}</span>
          <span className={styles.statLabel}>tracked assessments</span>
        </div>
        <div className={styles.stat}>
          <span className={styles.statValue}>{critical}</span>
          <span className={styles.statLabel}>critical tier</span>
        </div>
        <div className={styles.stat}>
          <span className={styles.statValue}>{pendingReview}</span>
          <span className={styles.statLabel}>pending human review</span>
        </div>
        <div className={styles.stat}>
          <span className={styles.statValue}>{avgSeverity}</span>
          <span className={styles.statLabel}>avg. severity score</span>
        </div>
      </section>

      <Filters filters={filters} onChange={setFilters} onReset={() => setFilters(emptyFilterState())} />

      <RiskTable assessments={sorted} onSelect={(id) => selectAssessment(id)} selectedId={selectedId} />

      <DetailDrawer
        assessment={selected}
        onClose={() => selectAssessment(null)}
        onRecordReview={recordReviewDecision}
      />
    </div>
  );
}
