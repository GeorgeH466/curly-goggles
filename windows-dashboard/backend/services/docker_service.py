"""Docker integration (optional panel).

Everything is best-effort: if the Docker CLI or daemon is missing, the panel
is hidden in the UI instead of raising errors.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
import time
from typing import Any

from services.platform_utils import CREATE_NO_WINDOW, IS_WINDOWS

DOCKER_TIMEOUT = 10
_available_cache: dict[str, Any] = {"value": None, "checked": 0.0}
_lock = threading.Lock()
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,127}$")


def _docker_exe() -> str | None:
    return shutil.which("docker")


def _run(args: list[str], timeout: float = DOCKER_TIMEOUT) -> subprocess.CompletedProcess:
    exe = _docker_exe()
    if not exe:
        raise FileNotFoundError("Docker CLI not found")
    kwargs: dict[str, Any] = {
        "capture_output": True,
        "text": True,
        "errors": "replace",
        "timeout": timeout,
        "encoding": "utf-8",
    }
    if IS_WINDOWS:
        kwargs["creationflags"] = CREATE_NO_WINDOW
    return subprocess.run([exe, *args], **kwargs)


def docker_available(force: bool = False) -> bool:
    """True when the Docker CLI exists and the daemon answers."""
    with _lock:
        if not force and _available_cache["value"] is not None:
            if time.time() - _available_cache["checked"] < 30:
                return bool(_available_cache["value"])
    value = False
    if _docker_exe():
        try:
            proc = _run(["version", "--format", "{{.Server.Version}}"], timeout=6)
            value = proc.returncode == 0
        except (OSError, subprocess.SubprocessError):
            value = False
    with _lock:
        _available_cache.update({"value": value, "checked": time.time()})
    return value


def _format_lines(output: str) -> list[dict[str, Any]]:
    items = []
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            items.append(json.loads(line))
        except ValueError:
            continue
    return items


def list_containers(all_containers: bool = True) -> list[dict[str, Any]]:
    args = ["ps", "--format", "{{json .}}"]
    if all_containers:
        args.insert(1, "-a")
    proc = _run(args)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "docker ps failed").strip())
    containers = []
    for item in _format_lines(proc.stdout):
        containers.append(
            {
                "id": (item.get("ID") or "")[:12],
                "name": item.get("Names") or "",
                "image": item.get("Image") or "",
                "state": (item.get("State") or "").lower(),
                "status": item.get("Status") or "",
                "ports": item.get("Ports") or "",
                "created": item.get("CreatedAt") or "",
                "running_for": item.get("RunningFor") or "",
                "labels": item.get("Labels") or "",
            }
        )
    return containers


def container_stats() -> dict[str, dict[str, Any]]:
    """One-shot resource usage for running containers."""
    try:
        proc = _run(["stats", "--no-stream", "--format", "{{json .}}"], timeout=20)
    except (OSError, subprocess.SubprocessError):
        return {}
    if proc.returncode != 0:
        return {}
    stats: dict[str, dict[str, Any]] = {}
    for item in _format_lines(proc.stdout):
        key = item.get("Name") or item.get("Container") or item.get("ID") or ""
        stats[key] = {
            "cpu": item.get("CPUPerc") or "",
            "memory": item.get("MemPerc") or "",
            "memory_usage": item.get("MemUsage") or "",
            "net": item.get("NetIO") or "",
            "block": item.get("BlockIO") or "",
            "pids": item.get("PIDs") or "",
        }
    return stats


def overview() -> dict[str, Any]:
    """Everything the Docker panel renders in one call."""
    if not docker_available():
        return {"available": False, "containers": [], "images": 0, "version": None}

    version = None
    try:
        proc = _run(["version", "--format", "{{.Server.Version}}"], timeout=6)
        if proc.returncode == 0:
            version = proc.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass

    try:
        containers = list_containers(all_containers=True)
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        return {"available": True, "error": str(exc), "containers": [], "images": 0,
                "version": version}

    stats = container_stats()
    for container in containers:
        container["stats"] = stats.get(container["name"]) or stats.get(container["id"])

    images = 0
    try:
        proc = _run(["images", "-q"], timeout=8)
        if proc.returncode == 0:
            images = len([line for line in proc.stdout.splitlines() if line.strip()])
    except (OSError, subprocess.SubprocessError):
        pass

    running = sum(1 for c in containers if c["state"] == "running")
    return {
        "available": True,
        "version": version,
        "containers": containers,
        "running": running,
        "total": len(containers),
        "images": images,
    }


def container_action(name: str, action: str) -> dict[str, Any]:
    if not NAME_RE.match(name):
        raise ValueError("Invalid container name")
    if action not in ("start", "stop", "restart", "pause", "unpause", "remove"):
        raise ValueError("Unsupported action")
    args = ["rm", "-f", name] if action == "remove" else [action, name]
    proc = _run(args, timeout=30)
    output = ((proc.stdout or "") + (proc.stderr or "")).strip()
    return {"ok": proc.returncode == 0, "output": output or f"{action} requested"}


def container_logs(name: str, lines: int = 80) -> str:
    if not NAME_RE.match(name):
        raise ValueError("Invalid container name")
    proc = _run(["logs", "--tail", str(max(1, min(lines, 500))), name], timeout=15)
    return ((proc.stdout or "") + (proc.stderr or "")).strip()


def container_images() -> list[dict[str, Any]]:
    try:
        proc = _run(["images", "--format", "{{json .}}"], timeout=10)
    except (OSError, subprocess.SubprocessError):
        return []
    if proc.returncode != 0:
        return []
    images = []
    for item in _format_lines(proc.stdout):
        images.append(
            {
                "repository": item.get("Repository") or "",
                "tag": item.get("Tag") or "",
                "size": item.get("Size") or "",
                "id": (item.get("ID") or "")[:12],
            }
        )
    return images
