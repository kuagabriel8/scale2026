from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..dependencies import get_connection_manager, get_data_store

router = APIRouter(tags=["streaming"])
logger = logging.getLogger("sightline.ws")


@router.websocket("/ws/risk-stream")
async def risk_stream(websocket: WebSocket) -> None:
    """Live push channel. Emits risk_assessment_stream_event_schema.json
    envelopes: ASSESSMENT_CREATED as the background simulator "ingests" new
    transactions, ASSESSMENT_UPDATED on simulated re-scores, and
    REVIEW_STATUS_CHANGED whenever an analyst PATCHes a review status.
    Supports multiple concurrent clients - each connection just receives the
    same broadcast stream from ConnectionManager."""
    manager = get_connection_manager(websocket)  # type: ignore[arg-type]
    data_store = get_data_store(websocket)  # type: ignore[arg-type]
    await manager.connect(websocket)
    try:
        while True:
            # This endpoint is push-only; we still read from the socket so
            # we notice client-initiated disconnects promptly. A client may
            # optionally send {"type": "get", "transaction_id": N} to pull a
            # single assessment on demand (handy for reconnect/backfill).
            data = await websocket.receive_json()
            if isinstance(data, dict) and data.get("type") == "get":
                tid = data.get("transaction_id")
                # Offload: get_assessment() can invoke a blocking RPTScorer
                # HTTP call, which must not block the event loop (and thus
                # every other WebSocket/REST client) while it runs.
                assessment = (
                    await asyncio.to_thread(data_store.get_assessment, int(tid))
                    if tid is not None
                    else None
                )
                if assessment is not None:
                    event = manager.build_event("ASSESSMENT_UPDATED", assessment)
                    await websocket.send_json(event)
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("Error on /ws/risk-stream connection")
    finally:
        await manager.disconnect(websocket)
