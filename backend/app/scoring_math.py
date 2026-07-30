"""Deterministic pseudo-random helpers shared by the vectorized (whole
dataframe, for sort/filter) and per-row (single assessment detail) code
paths, so both always produce the *same* number for the same
(transaction_id, update_count) pair - the list view's severity/score must
never disagree with the detail view for the same transaction.

Uses the classic sin/mod hash trick (`frac(sin(x)*C)`) instead of a Python
`random.Random` seeded per row so it vectorizes over numpy arrays with zero
Python-level looping - important since the transaction table has 150k rows.
"""
from __future__ import annotations

import numpy as np

Number = "float | np.ndarray"


def _hash01(x, salt: float):
    """Deterministic pseudo-random value in [0, 1), works for scalars or
    numpy arrays alike."""
    return np.mod(np.sin(np.asarray(x, dtype=np.float64) * 12.9898 + salt) * 43758.5453, 1.0)


def signed_noise(transaction_id, update_count, salt: float):
    """Deterministic pseudo-random value in [-1, 1)."""
    seed_salt = salt + np.asarray(update_count, dtype=np.float64) * 78.233
    return _hash01(transaction_id, seed_salt) * 2.0 - 1.0


def overall_risk_score(total_typology_points, transaction_id, update_count=0):
    """Correlates with the deterministic rule-layer's TOTAL_TYPOLOGY_POINTS
    (weight ~0.65) plus bounded noise (+/-12), so the mocked ML score tracks
    the rule engine plausibly rather than being pure random. Base offset of
    15 keeps low-typology transactions from clustering at exactly 0."""
    noise = signed_noise(transaction_id, update_count, salt=17.0)
    score = 15.0 + np.asarray(total_typology_points, dtype=np.float64) * 0.65 + noise * 12.0
    return np.clip(score, 0.0, 100.0)


def model_confidence(total_typology_points, transaction_id, update_count=0):
    noise = signed_noise(transaction_id, update_count, salt=91.0)
    conf = 0.55 + np.asarray(total_typology_points, dtype=np.float64) / 250.0 + noise * 0.08
    return np.clip(conf, 0.0, 1.0)


def risk_tier(score):
    """Vectorized-safe tiering. score: scalar or numpy array in [0, 100]."""
    arr = np.asarray(score, dtype=np.float64)
    tiers = np.select(
        [arr >= 85.0, arr >= 60.0, arr >= 30.0],
        ["CRITICAL", "HIGH", "MEDIUM"],
        default="LOW",
    )
    if tiers.ndim == 0:
        return str(tiers)
    return tiers


def severity(risk_score, typology_strength_points):
    """Documented severity ranking: max(risk.overall_risk_score,
    exposure.typology_strength_points). Both are already on a 0-100 scale,
    so this rewards transactions that are extreme on *either* the ML layer
    or the deterministic rule layer, rather than requiring both to agree -
    appropriate for a triage queue where either signal alone should surface
    a transaction to an analyst."""
    return np.maximum(
        np.asarray(risk_score, dtype=np.float64),
        np.asarray(typology_strength_points, dtype=np.float64),
    )


def component_score(base_points, transaction_id, update_count, salt: float):
    """Sub-score (amount/frequency/geography/... ) derived from a category's
    summed typology points, same noise pattern as overall_risk_score."""
    noise = signed_noise(transaction_id, update_count, salt=salt)
    score = 12.0 + np.asarray(base_points, dtype=np.float64) * 1.05 + noise * 10.0
    return np.clip(score, 0.0, 100.0)
