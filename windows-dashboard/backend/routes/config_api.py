"""Dashboard configuration endpoints (settings modal, theme, app metadata)."""

from __future__ import annotations

import os
import platform
import sys
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services.config import BASE_DIR, CONFIG_PATH, VERSION, config
from services.platform_utils import IS_WINDOWS, is_admin

router = APIRouter(prefix="/api/config", tags=["config"])


class ConfigPatch(BaseModel):
    patch: dict[str, Any]


class ThemeUpdate(BaseModel):
    theme: str | None = None
    accent: str | None = None
    compact: bool | None = None


@router.get("")
def get_config() -> dict[str, Any]:
    """Full configuration plus environment metadata for the settings modal."""
    data = config.as_dict()
    # Never leak the GitHub token through the API; expose a boolean instead.
    notifications = data.get("repos", {}).get("notifications", {})
    notifications["has_token"] = bool(notifications.get("token"))
    notifications["token"] = ""
    return {
        "config": data,
        "meta": {
            "version": VERSION,
            "config_path": str(CONFIG_PATH),
            "base_dir": str(BASE_DIR),
            "python": platform.python_version(),
            "executable": sys.executable,
            "platform": platform.platform(),
            "windows": IS_WINDOWS,
            "admin": is_admin(),
            "pid": os.getpid(),
            "home": str(os.path.expanduser("~")),
        },
    }


@router.patch("")
def patch_config(body: ConfigPatch) -> dict[str, Any]:
    if not isinstance(body.patch, dict):
        raise HTTPException(status_code=400, detail="patch must be an object")
    updated = config.update(body.patch)
    notifications = updated.setdefault("repos", {}).setdefault("notifications", {})
    notifications["has_token"] = bool(notifications.get("token"))
    notifications["token"] = ""
    return {"ok": True, "config": updated}


@router.post("/theme")
def set_theme(body: ThemeUpdate) -> dict[str, Any]:
    patch: dict[str, Any] = {}
    if body.theme in ("dark", "light", "system"):
        patch["theme"] = body.theme
    if body.accent:
        patch["accent"] = body.accent
    if body.compact is not None:
        patch["compact"] = body.compact
    if patch:
        config.update({"ui": patch})
    return {"ok": True, "ui": config.get("ui")}


@router.post("/reset")
def reset_config(confirm: bool = False) -> dict[str, Any]:
    if not confirm:
        raise HTTPException(status_code=400, detail="Confirmation required")
    data = config.reset()
    return {"ok": True, "config": data}
