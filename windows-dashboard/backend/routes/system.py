"""System monitoring endpoints + the live stats websocket."""

from __future__ import annotations

from typing import Any

import psutil
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from services.config import VERSION, config
from services.monitor import monitor, quick_facts

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/stats")
def stats() -> dict[str, Any]:
    """One-shot snapshot (same payload the websocket pushes)."""
    snapshot = monitor.snapshot()
    snapshot["history"] = monitor.history()
    snapshot["static"] = monitor.static_info()
    return snapshot


@router.get("/info")
def info() -> dict[str, Any]:
    """Static machine information + feature availability for the UI."""
    static = monitor.static_info()
    gpus = monitor.gpu_info()
    temperatures = monitor.temperatures()
    battery = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
    return {
        "static": static,
        "gpus": gpus,
        "temperatures": temperatures,
        "battery_present": battery is not None,
        "uptime": quick_facts()["uptime"],
        "version": VERSION,
        "features": features(),
    }


@router.get("/features")
def features() -> dict[str, Any]:
    """Report optional capabilities so the frontend can hide panels gracefully."""
    from services.platform_utils import find_github_desktop
    from services.terminal_manager import available_shells

    from services.terminal_manager import HAS_PYWINPTY

    shells = available_shells()
    conpty = bool(HAS_PYWINPTY)

    docker_available = False
    try:
        from services.docker_service import docker_available as _docker

        docker_available = _docker()
    except Exception:  # noqa: BLE001
        docker_available = False

    return {
        "gpu": bool(monitor.gpu_info()),
        "nvidia_smi": bool(monitor._nvidia),  # noqa: SLF001 - internal but stable
        "battery": battery_available(),
        "wifi": wifi_available(),
        "docker": docker_available and bool(config.get("docker.enabled", True)),
        "weather": bool(config.get("weather.enabled", True)),
        "github_desktop": bool(find_github_desktop()),
        "terminal_pty": shells["pty"],
        "conpty": conpty,
        "shells": shells,
        "theme": config.get("ui.theme", "dark"),
    }


def battery_available() -> bool:
    try:
        return psutil.sensors_battery() is not None
    except Exception:  # noqa: BLE001
        return False


def wifi_available() -> bool:
    info = monitor._wifi()  # noqa: SLF001
    return info is not None


@router.get("/history")
def history() -> dict[str, Any]:
    return {"history": monitor.history()}


@router.get("/processes")
def processes(
    sort: str = Query("cpu", pattern="^(cpu|memory|name|pid)$"),
    limit: int = Query(30, ge=1, le=300),
) -> dict[str, Any]:
    """Process list for the "top processes" panel."""
    items: list[dict[str, Any]] = []
    try:
        for proc in psutil.process_iter(
            ["pid", "name", "username", "cpu_percent", "memory_percent", "create_time", "status"]
        ):
            try:
                data = proc.info
                items.append(
                    {
                        "pid": data["pid"],
                        "name": data.get("name") or "?",
                        "user": (data.get("username") or "").split("\\")[-1],
                        "cpu": round(data.get("cpu_percent") or 0.0, 1),
                        "memory": round(data.get("memory_percent") or 0.0, 1),
                        "status": data.get("status") or "",
                        "created": data.get("create_time"),
                    }
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except psutil.Error:
        pass

    key = {"cpu": lambda p: p["cpu"], "memory": lambda p: p["memory"],
           "name": lambda p: p["name"].lower(), "pid": lambda p: p["pid"]}[sort]
    items.sort(key=key, reverse=sort in ("cpu", "memory"))
    return {"processes": items[:limit], "total": len(items)}


@router.get("/threads")
def threads_info() -> dict[str, Any]:
    """Thermal/fan sensors that psutil can read (may be empty)."""
    return monitor.temperatures()


# --------------------------------------------------------------------------- #
# Live stats websocket
# --------------------------------------------------------------------------- #
@router.websocket("/ws")
async def stats_ws(ws: WebSocket) -> None:
    """Push a snapshot every few seconds; also answers ``refresh``/``ping``."""
    from services.live import hub
    from services.security import websocket_origin_ok

    if not websocket_origin_ok(ws):
        await ws.close(code=4403, reason="Origin not allowed")
        return

    await hub.connect(ws)
    try:
        while True:
            message = await ws.receive_text()
            command = (message or "").strip().lower()
            if command in ("refresh", "stats"):
                await hub.push_now()
            elif command == "ping":
                await ws.send_json({"type": "pong"})
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        hub.disconnect(ws)
