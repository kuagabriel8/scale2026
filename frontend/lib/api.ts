// Minimal typed REST client for the FastAPI backend. Mirrors the endpoints
// documented in ../backend/README.md and the shapes in ./types.ts.

import { API_BASE_URL } from "./config";
import type { ReviewStatus, RiskAssessment, RiskTier, TypologyCategory } from "./types";

export interface PaginatedAssessments {
  items: RiskAssessment[];
  total: number;
  page: number;
  page_size: number;
  sort: string;
}

export interface ListAssessmentsParams {
  page?: number;
  page_size?: number;
  sort?: "severity" | "overall_risk_score" | "typology_strength_points" | "transaction_id";
  order?: "asc" | "desc";
  risk_tier?: RiskTier;
  category?: TypologyCategory;
  review_status?: ReviewStatus;
}

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...init?.headers,
    },
  });
  if (!res.ok) {
    throw new Error(`API ${path} failed: ${res.status} ${res.statusText}`);
  }
  return res.json() as Promise<T>;
}

export function listRiskAssessments(
  params: ListAssessmentsParams = {}
): Promise<PaginatedAssessments> {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null) search.set(key, String(value));
  }
  const qs = search.toString();
  return apiFetch<PaginatedAssessments>(`/risk-assessments${qs ? `?${qs}` : ""}`);
}

export function getRiskAssessment(transactionId: number): Promise<RiskAssessment> {
  return apiFetch<RiskAssessment>(`/risk-assessments/${transactionId}`);
}

export function updateReviewStatus(
  transactionId: number,
  reviewStatus: ReviewStatus,
  reviewedBy: string | null
): Promise<RiskAssessment> {
  return apiFetch<RiskAssessment>(`/risk-assessments/${transactionId}/review`, {
    method: "PATCH",
    body: JSON.stringify({ review_status: reviewStatus, reviewed_by: reviewedBy }),
  });
}
