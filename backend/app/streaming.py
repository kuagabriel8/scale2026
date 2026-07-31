"""WebSocket connection management + the simulated live-ingestion
background task.

There is no real live transaction feed in this environment, so ingestion is
simulated by walking TRANSACTION_FRAUD_FEATURES rows ordered by
INITIATED_AT and emitting one ASSESSMENT_CREATED event per "newly arrived"
row at a configurable pace (config.STREAM_EVENTS_PER_SECOND). Every
STREAM_RESCORE_EVERY_N-th tick, an already-seen row is re-emitted instead as
ASSESSMENT_UPDATED (simulating a re-score), via DataStore.bump_rescore()
which perturbs that transaction's mocked ML score deterministically.
"""
from __future__ import annotations

import asyncio
import itertools
import logging
from datetime import datetime, timezone

from fastapi import WebSocket

from .config import settings
from .data_store import DataStore
from .schema_validation import validate_stream_event

logger = logging.getLogger("sightline.streaming")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ConnectionManager:
    """Tracks connected dashboard WebSocket clients and broadcasts stream
    events to all of them. A monotonic `sequence` counter (per backend
    instance, per the schema's description) is owned here so both the
    background simulator and the REVIEW_STATUS_CHANGED path (triggered by
    the PATCH review endpoint) share one counter."""

    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()
        self._sequence = itertools.count(start=0)
        self._lock = asyncio.Lock()

    def next_sequence(self) -> int:
        return next(self._sequence)

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._connections.add(ws)
        logger.info("WebSocket connected (%d total)", len(self._connections))

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._connections.discard(ws)
        logger.info("WebSocket disconnected (%d total)", len(self._connections))

    async def broadcast(self, event: dict) -> None:
        async with self._lock:
            targets = list(self._connections)
        dead = []
        for ws in targets:
            try:
                await ws.send_json(event)
            except Exception:
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self._connections.discard(ws)

    def build_event(self, event_type: str, assessment: dict) -> dict:
        event = {
            "event_type": event_type,
            "sequence": self.next_sequence(),
            "emitted_at": _now_iso(),
            "assessment": assessment,
        }
        if settings.VALIDATE_RESPONSES:
            validate_stream_event(event)
        return event


async def run_stream_simulator(data_store: DataStore, manager: ConnectionManager) -> None:
    """Background task: emits ASSESSMENT_CREATED for each row in
    INITIATED_AT order at STREAM_EVENTS_PER_SECOND, occasionally re-emitting
    an already-seen row as ASSESSMENT_UPDATED. Runs until cancelled (app
    shutdown)."""
    order = data_store.transaction_ids_in_stream_order()
    if not order:
        logger.warning("No transactions loaded - stream simulator has nothing to emit")
        return

    interval = 1.0 / max(settings.STREAM_EVENTS_PER_SECOND, 0.001)
    seen: list[int] = []
    tick = 0

    try:
        while True:
            for transaction_id in order:
                tick += 1
                rescore = (
                    settings.STREAM_RESCORE_EVERY_N > 0
                    and tick % settings.STREAM_RESCORE_EVERY_N == 0
                    and seen
                )
                if rescore:
                    target_id = seen[tick % len(seen)]
                    # data_store.bump_rescore() can invoke a blocking
                    # RPTScorer HTTP call under the hood - offload to a
                    # worker thread so this doesn't stall the event loop
                    # (and therefore every WebSocket/REST request) for the
                    # duration of that network round-trip.
                    assessment = await asyncio.to_thread(data_store.bump_rescore, target_id)
                    event_type = "ASSESSMENT_UPDATED"
                else:
                    assessment = await asyncio.to_thread(data_store.get_assessment, transaction_id)
                    event_type = "ASSESSMENT_CREATED"
                    seen.append(transaction_id)

                if assessment is not None:
                    event = manager.build_event(event_type, assessment)
                    await manager.broadcast(event)

                await asyncio.sleep(interval)

            if not settings.STREAM_LOOP:
                logger.info("Stream simulator reached end of dataset - STREAM_LOOP is false, stopping")
                return
            logger.info("Stream simulator reached end of dataset - looping back to the start")
    except asyncio.CancelledError:
        logger.info("Stream simulator cancelled (shutdown)")
        raise
