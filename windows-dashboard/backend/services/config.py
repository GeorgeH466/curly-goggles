"""Configuration manager.

All user configuration lives in `<project root>/config.json`.  The file is
created with sensible defaults on first launch, merged with the defaults when
it is loaded (so upgrades that add new keys keep working) and written back
atomically so a crash can never leave a half-written config behind.
"""

from __future__ import annotations

import copy
import json
import os
import threading
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
# config.py -> services -> backend -> windows-dashboard
BASE_DIR: Path = Path(__file__).resolve().parents[2]
BACKEND_DIR: Path = BASE_DIR / "backend"
FRONTEND_DIR: Path = BASE_DIR / "frontend"
CONFIG_PATH: Path = BASE_DIR / "config.json"
NOTES_DIR: Path = BASE_DIR / "notes"
LOG_DIR: Path = BASE_DIR / "logs"
VERSION = "1.0.0"


def _default_shortcuts() -> list[dict[str, Any]]:
    """Starter shortcuts for the quick-launch grid.

    ``path`` may be an executable name (resolved through PATH and the usual
    per-user install locations), an absolute path, a folder or a URL.
    """
    return [
        {"id": "vscode", "name": "VS Code", "icon": "code", "path": "code", "args": "", "color": "#3ba4f7"},
        {"id": "explorer", "name": "File Explorer", "icon": "folder", "path": "explorer.exe", "args": "", "color": "#f5c542"},
        {"id": "taskmgr", "name": "Task Manager", "icon": "activity", "path": "taskmgr.exe", "args": "", "color": "#5ce1e6"},
        {"id": "notepadpp", "name": "Notepad++", "icon": "edit", "path": "notepad++", "args": "", "color": "#8fd14f"},
        {"id": "browser", "name": "Browser", "icon": "globe", "path": "chrome", "args": "", "color": "#ff5f56"},
        {"id": "discord", "name": "Discord", "icon": "message", "path": "discord", "args": "", "color": "#7289da"},
        {"id": "spotify", "name": "Spotify", "icon": "music", "path": "spotify", "args": "", "color": "#1ed760"},
        {"id": "terminal", "name": "Windows Terminal", "icon": "terminal", "path": "wt.exe", "args": "", "color": "#a78bfa"},
    ]


def default_config() -> dict[str, Any]:
    """Return a fresh copy of the default configuration tree."""
    # "~" is expanded at runtime, so the shipped config works on any machine.
    scan_paths = [
        "~/Documents/GitHub",
        "~/GitHub",
        "~/repos",
        "~/Repositories",
        "~/projects",
        "~/Projects",
        "~/source/repos",
        "~/dev",
    ]
    return {
        "version": 1,
        "app": {
            "host": "127.0.0.1",
            "port": 8420,
            "open_browser": True,
            "refresh_seconds": 2,
        },
        "ui": {
            "theme": "dark",
            "accent": "cyan",
            "compact": False,
            "card_order": [
                "system",
                "gpu",
                "network",
                "weather",
                "storage",
                "repos",
                "launcher",
                "notes",
                "docker",
                "controls",
            ],
            "card_spans": {},
            "hidden_cards": [],
            "active_view": "overview",
        },
        "launcher": {
            "shortcuts": _default_shortcuts(),
        },
        "repos": {
            "scan_paths": scan_paths,
            "extra_paths": [],
            "max_depth": 3,
            "max_repos": 200,
            "cache_seconds": 20,
            "notifications": {
                "enabled": False,
                "token": "",
                "refresh_seconds": 120,
            },
        },
        "paths": {
            "vscode": "code",
            "github_desktop": "",
        },
        "terminal": {
            "default_shell": "auto",
            "font_size": 13,
            "font_family": "Cascadia Code, Consolas, 'Courier New', monospace",
        },
        "files": {
            "show_hidden": False,
            "delete_to_recycle_bin": True,
            "start_path": "",
        },
        "weather": {
            "enabled": True,
            "location": "",
            "units": "metric",
            "refresh_minutes": 15,
        },
        "docker": {
            "enabled": True,
        },
        "network": {
            "speedtest": True,
        },
    }


# --------------------------------------------------------------------------- #
# Deep helpers
# --------------------------------------------------------------------------- #
def _deep_merge(base: Any, override: Any) -> Any:
    """Recursively merge *override* into *base* (returns base)."""
    if isinstance(base, dict) and isinstance(override, dict):
        for key, value in override.items():
            if key in base:
                base[key] = _deep_merge(base[key], value)
            else:
                base[key] = copy.deepcopy(value)
        return base
    return copy.deepcopy(override)


class ConfigManager:
    """Thread-safe JSON configuration store."""

    def __init__(self, path: Path | str = CONFIG_PATH) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._data: dict[str, Any] = {}
        self.load()

    # ------------------------------------------------------------------ #
    # Persistence
    # ------------------------------------------------------------------ #
    def load(self) -> dict[str, Any]:
        with self._lock:
            data = default_config()
            if self.path.exists():
                try:
                    raw = json.loads(self.path.read_text(encoding="utf-8"))
                    if isinstance(raw, dict):
                        data = _deep_merge(data, raw)
                    else:
                        raise ValueError("config root must be a JSON object")
                except (json.JSONDecodeError, ValueError, OSError) as exc:
                    # Keep the broken file around for debugging, then fall back.
                    print(f"[config] could not read {self.path}: {exc}; using defaults")
                    try:
                        backup = self.path.with_suffix(".broken.json")
                        self.path.replace(backup)
                    except OSError:
                        pass
            self._data = data
            self._save_unlocked()
            return copy.deepcopy(self._data)

    def save(self) -> None:
        with self._lock:
            self._save_unlocked()

    def _save_unlocked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        payload = json.dumps(self._data, indent=2, ensure_ascii=False) + "\n"
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, self.path)

    # ------------------------------------------------------------------ #
    # Accessors
    # ------------------------------------------------------------------ #
    def as_dict(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._data)

    def get(self, dotted: str, default: Any = None) -> Any:
        with self._lock:
            node: Any = self._data
            for part in dotted.split("."):
                if not isinstance(node, dict) or part not in node:
                    return default
                node = node[part]
            return copy.deepcopy(node)

    def set(self, dotted: str, value: Any, *, save: bool = True) -> None:
        with self._lock:
            parts = dotted.split(".")
            node = self._data
            for part in parts[:-1]:
                nxt = node.get(part)
                if not isinstance(nxt, dict):
                    nxt = {}
                    node[part] = nxt
                node = nxt
            node[parts[-1]] = copy.deepcopy(value)
            if save:
                self._save_unlocked()

    def update(self, patch: dict[str, Any], *, save: bool = True) -> dict[str, Any]:
        """Deep-merge a patch into the config and persist it."""
        with self._lock:
            self._data = _deep_merge(self._data, patch)
            if save:
                self._save_unlocked()
            return copy.deepcopy(self._data)

    def reset(self) -> dict[str, Any]:
        with self._lock:
            self._data = default_config()
            self._save_unlocked()
            return copy.deepcopy(self._data)

    # ------------------------------------------------------------------ #
    # Convenience
    # ------------------------------------------------------------------ #
    def ensure_dirs(self) -> None:
        NOTES_DIR.mkdir(parents=True, exist_ok=True)
        LOG_DIR.mkdir(parents=True, exist_ok=True)

    def notes_dir(self) -> Path:
        NOTES_DIR.mkdir(parents=True, exist_ok=True)
        return NOTES_DIR


# A single shared instance used by the FastAPI app.
config = ConfigManager()
