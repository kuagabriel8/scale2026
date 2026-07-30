"""Loads typology metadata (id/name/category/typology_points/narrative_template)
from topfraudandtables.json - the single source of truth for T01-T13 naming,
shared with build_fraud_features.py.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache

from .config import settings


@dataclass(frozen=True)
class TypologyDef:
    id: str
    name: str
    category: str
    typology_points: int
    narrative_template: str | None


@lru_cache(maxsize=1)
def load_typology_defs() -> dict[str, TypologyDef]:
    with open(settings.TYPOLOGY_DEFS_PATH, "r", encoding="utf-8") as f:
        raw = json.load(f)

    defs: dict[str, TypologyDef] = {}
    for t in raw["typologies"]:
        defs[t["id"]] = TypologyDef(
            id=t["id"],
            name=t["name"],
            category=t["category"],
            typology_points=t["typology_points"],
            narrative_template=t.get("narrative_template"),
        )
    return defs


# Ordered list T01..T13, matching topfraudandtables.json's declaration order.
@lru_cache(maxsize=1)
def ordered_typology_ids() -> list[str]:
    return list(load_typology_defs().keys())
