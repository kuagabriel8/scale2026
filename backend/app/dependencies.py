"""FastAPI dependency accessors for the app-lifetime singletons (data store,
websocket connection manager) created during the lifespan startup hook in
main.py and stashed on `app.state`.
"""
from __future__ import annotations

from fastapi import Request

from .data_store import DataStore
from .streaming import ConnectionManager


def get_data_store(request: Request) -> DataStore:
    return request.app.state.data_store


def get_connection_manager(request: Request) -> ConnectionManager:
    return request.app.state.ws_manager
