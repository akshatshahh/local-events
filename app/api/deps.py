"""Wiring. Singletons are built once at startup and stashed on app.state."""

from __future__ import annotations

from fastapi import Request

from app.services.discovery import DiscoveryService


def get_discovery(request: Request) -> DiscoveryService:
    return request.app.state.discovery
