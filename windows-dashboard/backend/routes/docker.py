"""Docker panel endpoints (optional feature)."""

from __future__ import annotations

import subprocess
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from services import docker_service
from services.config import config

router = APIRouter(prefix="/api/docker", tags=["docker"])


class ContainerAction(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    action: str = Field(min_length=1, max_length=20)


@router.get("")
def overview() -> dict[str, Any]:
    if not config.get("docker.enabled", True):
        return {"available": False, "disabled": True, "containers": []}
    try:
        return docker_service.overview()
    except (OSError, subprocess.SubprocessError) as exc:
        return {"available": False, "error": str(exc), "containers": []}


@router.get("/availability")
def availability(force: bool = Query(False)) -> dict[str, Any]:
    enabled = bool(config.get("docker.enabled", True))
    return {"enabled": enabled, "available": enabled and docker_service.docker_available(force)}


@router.post("/action")
def action(body: ContainerAction) -> dict[str, Any]:
    try:
        return docker_service.container_action(body.name, body.action)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise HTTPException(status_code=503, detail=f"Docker unavailable: {exc}") from exc


@router.get("/logs")
def logs(name: str = Query(..., min_length=1), lines: int = Query(80, ge=1, le=500)) -> dict[str, Any]:
    try:
        return {"name": name, "logs": docker_service.container_logs(name, lines)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise HTTPException(status_code=503, detail=f"Docker unavailable: {exc}") from exc


@router.get("/images")
def images() -> dict[str, Any]:
    return {"images": docker_service.container_images()}
