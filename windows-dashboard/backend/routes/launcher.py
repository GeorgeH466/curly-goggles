"""Quick-launch endpoints (application shortcut grid)."""

from __future__ import annotations

import time
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from services.config import config
from services.platform_utils import (
    LaunchError,
    launch,
    resolve_app,
)

router = APIRouter(prefix="/api/launcher", tags=["launcher"])

ALLOWED_ICONS = {
    "code", "folder", "activity", "edit", "globe", "message", "music",
    "terminal", "game", "spotify", "chrome", "firefox", "steam", "discord",
    "settings", "camera", "video", "file", "app",
}


def _shortcuts() -> list[dict[str, Any]]:
    data = config.get("launcher.shortcuts", []) or []
    return [s for s in data if isinstance(s, dict)]


def _save(shortcuts: list[dict[str, Any]]) -> None:
    config.set("launcher.shortcuts", shortcuts)


class Shortcut(BaseModel):
    id: str | None = None
    name: str = Field(min_length=1, max_length=60)
    path: str = Field(min_length=1, max_length=1024)
    args: str = Field(default="", max_length=512)
    icon: str = "app"
    color: str = "#5ce1e6"

    @field_validator("icon")
    @classmethod
    def _icon(cls, value: str) -> str:
        return value if value in ALLOWED_ICONS else "app"

    @field_validator("color")
    @classmethod
    def _color(cls, value: str) -> str:
        return value if value.startswith("#") and len(value) in (4, 7) else "#5ce1e6"


class LaunchRequest(BaseModel):
    id: str | None = None
    path: str | None = None
    args: str | None = None


class ReorderRequest(BaseModel):
    ids: list[str]


@router.get("/shortcuts")
def list_shortcuts() -> dict[str, Any]:
    return {"shortcuts": _shortcuts(), "recent": config.get("launcher.recent", [])}


@router.post("/shortcuts")
def create_shortcut(body: Shortcut) -> dict[str, Any]:
    shortcuts = _shortcuts()
    item = body.model_dump()
    item["id"] = item.get("id") or uuid.uuid4().hex[:8]
    if any(s.get("id") == item["id"] for s in shortcuts):
        raise HTTPException(status_code=409, detail="A shortcut with that id already exists")
    if len(shortcuts) >= 60:
        raise HTTPException(status_code=400, detail="Shortcut limit reached (60)")
    shortcuts.append(item)
    _save(shortcuts)
    return item


@router.put("/shortcuts/{shortcut_id}")
def update_shortcut(shortcut_id: str, body: Shortcut) -> dict[str, Any]:
    shortcuts = _shortcuts()
    for index, existing in enumerate(shortcuts):
        if existing.get("id") == shortcut_id:
            updated = body.model_dump()
            updated["id"] = shortcut_id
            shortcuts[index] = updated
            _save(shortcuts)
            return updated
    raise HTTPException(status_code=404, detail="Shortcut not found")


@router.delete("/shortcuts/{shortcut_id}")
def delete_shortcut(shortcut_id: str) -> dict[str, Any]:
    shortcuts = _shortcuts()
    remaining = [s for s in shortcuts if s.get("id") != shortcut_id]
    if len(remaining) == len(shortcuts):
        raise HTTPException(status_code=404, detail="Shortcut not found")
    _save(remaining)
    return {"ok": True, "deleted": shortcut_id}


@router.post("/reorder")
def reorder(body: ReorderRequest) -> dict[str, Any]:
    shortcuts = _shortcuts()
    by_id = {s.get("id"): s for s in shortcuts}
    ordered = [by_id[i] for i in body.ids if i in by_id]
    ordered += [s for s in shortcuts if s.get("id") not in body.ids]
    _save(ordered)
    return {"ok": True, "shortcuts": ordered}


@router.post("/launch")
def launch_shortcut(body: LaunchRequest) -> dict[str, Any]:
    shortcut: dict[str, Any] | None = None
    if body.id:
        shortcut = next((s for s in _shortcuts() if s.get("id") == body.id), None)
        if not shortcut:
            raise HTTPException(status_code=404, detail="Shortcut not found")
        path = shortcut.get("path", "")
        args = body.args if body.args is not None else shortcut.get("args", "")
    elif body.path:
        path = body.path
        args = body.args or ""
    else:
        raise HTTPException(status_code=400, detail="Provide a shortcut id or a path")

    try:
        result = launch(path, args)
    except LaunchError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Track recent launches (newest first, deduplicated).
    recent = [r for r in (config.get("launcher.recent", []) or []) if r.get("id") != (body.id or path)]
    recent.insert(
        0,
        {
            "id": shortcut.get("id") if shortcut else path,
            "name": shortcut.get("name") if shortcut else path,
            "icon": shortcut.get("icon", "app") if shortcut else "app",
            "at": time.time(),
        },
    )
    config.set("launcher.recent", recent[:8])
    return result


@router.get("/detect")
def detect_apps() -> dict[str, Any]:
    """Check which of the well-known applications are installed."""
    known = [
        ("code", "VS Code", "code"),
        ("cursor", "Cursor", "code"),
        ("chrome", "Google Chrome", "chrome"),
        ("edge", "Microsoft Edge", "globe"),
        ("firefox", "Firefox", "firefox"),
        ("notepad++", "Notepad++", "edit"),
        ("discord", "Discord", "discord"),
        ("spotify", "Spotify", "spotify"),
        ("steam", "Steam", "steam"),
        ("vlc", "VLC", "video"),
        ("obs", "OBS Studio", "camera"),
        ("wt.exe", "Windows Terminal", "terminal"),
    ]
    found = []
    for path, name, icon in known:
        try:
            resolved = resolve_app(path)
        except LaunchError:
            continue
        if resolved["kind"] == "url":
            # The browser fallback always "resolves"; only list real binaries.
            continue
        found.append({"path": path, "name": name, "icon": icon, "resolved": resolved["target"]})
    return {"apps": found}
