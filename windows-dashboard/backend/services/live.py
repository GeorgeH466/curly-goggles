"""Live stats broadcast hub.

A single background task samples the :class:`Monitor` every couple of seconds
and fans the snapshot out to every connected websocket.  Sampling once keeps
CPU cost constant regardless of how many dashboard tabs are open, and keeps
the history buffer evenly spaced.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Set

from fastapi import WebSocket

from services.config import config
from services.monitor import monitor

log = logging.getLogger("dashboard.live")


class LiveHub:
    def __init__(self) -> None:
        self._clients: Set[WebSocket] = set()
        self._task: asyncio.Task | None = None
        self._wake = asyncio.Event()
        self._last: dict[str, Any] | None = None

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="live-stats")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        for ws in list(self._clients):
            try:
                await ws.close()
            except Exception:  # noqa: BLE001
                pass
        self._clients.clear()

    @property
    def client_count(self) -> int:
        return len(self._clients)

    # ------------------------------------------------------------------ #
    # Connection handling
    # ------------------------------------------------------------------ #
    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._clients.add(ws)
        interval = self._interval()
        payload = self._last or await asyncio.to_thread(monitor.summary)
        await self._safe_send(ws, {"type": "hello", "interval": interval, "payload": payload})

    def disconnect(self, ws: WebSocket) -> None:
        self._clients.discard(ws)

    async def push_now(self) -> dict[str, Any]:
        """Force an immediate sample and broadcast (used by the refresh button)."""
        payload = await asyncio.to_thread(monitor.summary)
        self._last = payload
        await self._broadcast({"type": "stats", "payload": payload})
        return payload

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    @staticmethod
    def _interval() -> float:
        try:
            value = float(config.get("app.refresh_seconds", 2) or 2)
        except (TypeError, ValueError):
            value = 2.0
        return max(1.0, min(value, 30.0))

    async def _safe_send(self, ws: WebSocket, message: dict[str, Any]) -> bool:
        try:
            await ws.send_json(message)
            return True
        except Exception:  # noqa: BLE001 - client vanished
            return False

    async def _broadcast(self, message: dict[str, Any]) -> None:
        dead: list[WebSocket] = []
        for ws in list(self._clients):
            if not await self._safe_send(ws, message):
                dead.append(ws)
        for ws in dead:
            self._clients.discard(ws)

    async def _run(self) -> None:
        log.info("live stats hub started")
        while True:
            try:
                payload = await asyncio.to_thread(monitor.summary)
                self._last = payload
                if self._clients:
                    await self._broadcast({"type": "stats", "payload": payload})
                interval = self._interval()
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=interval)
                    self._wake.clear()
                except asyncio.TimeoutError:
                    pass
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - never let the hub die
                log.exception("live stats loop error")
                await asyncio.sleep(2)


hub = LiveHub()
