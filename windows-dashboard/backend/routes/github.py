"""Git repository endpoints (GitHub Desktop panel)."""

from __future__ import annotations

import threading
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from services.config import config
from services.git_scanner import scanner
from services.platform_utils import LaunchError, launch_github_desktop

router = APIRouter(prefix="/api/github", tags=["github"])

# Serialise repository mutations so two pulls can't run against one repo.
_git_lock = threading.Lock()


class RepoPath(BaseModel):
    path: str = Field(min_length=1, max_length=4096)


class AddRepo(RepoPath):
    persist: bool = True


@router.get("/environment")
def environment() -> dict[str, Any]:
    return scanner.environment()


@router.get("/repos")
def list_repos(refresh: bool = Query(False)) -> dict[str, Any]:
    """Scan the configured folders for Git repositories."""
    data = scanner.scan(force=refresh)
    data["environment"] = scanner.environment()
    return data


@router.get("/repo")
def get_repo(path: str = Query(..., min_length=1)) -> dict[str, Any]:
    info = scanner.repo_info(path)
    if not info:
        raise HTTPException(status_code=404, detail="Path not found")
    return info


@router.post("/repos")
def add_repo(body: AddRepo) -> dict[str, Any]:
    try:
        info = scanner.add_path(body.path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not body.persist:
        scanner.remove_path(body.path)
    return info


@router.delete("/repos")
def remove_repo(path: str = Query(..., min_length=1)) -> dict[str, Any]:
    scanner.remove_path(path)
    return {"ok": True}


@router.post("/scan")
def scan() -> dict[str, Any]:
    return scanner.scan(force=True)


@router.post("/pull")
def pull(body: RepoPath) -> dict[str, Any]:
    with _git_lock:
        result = scanner.pull(body.path)
    return result


@router.post("/fetch")
def fetch(body: RepoPath) -> dict[str, Any]:
    with _git_lock:
        result = scanner.fetch(body.path)
    return result


@router.post("/open-folder")
def open_folder(body: RepoPath) -> dict[str, Any]:
    try:
        scanner.open_folder(body.path)
    except LaunchError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


@router.post("/open-code")
def open_code(body: RepoPath) -> dict[str, Any]:
    try:
        return scanner.open_in_vscode(body.path)
    except LaunchError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/open-remote")
def open_remote(body: RepoPath) -> dict[str, Any]:
    try:
        return scanner.open_remote(body.path)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/open-desktop")
def open_desktop() -> dict[str, Any]:
    """Launch GitHub Desktop (located automatically from its install folder)."""
    try:
        return launch_github_desktop()
    except LaunchError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/notifications")
def notifications(force: bool = Query(False)) -> dict[str, Any]:
    return scanner.notifications(force=force)


@router.post("/notifications/settings")
def notification_settings(enabled: bool | None = None, token: str | None = None) -> dict[str, Any]:
    patch: dict[str, Any] = {}
    if enabled is not None:
        patch["enabled"] = enabled
    if token is not None:
        patch["token"] = token.strip()
    if patch:
        config.update({"repos": {"notifications": patch}})
    return {"ok": True, "enabled": config.get("repos.notifications.enabled"),
            "has_token": bool(config.get("repos.notifications.token"))}
