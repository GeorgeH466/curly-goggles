"""System control endpoints.

Destructive actions all require an explicit ``confirm: true`` flag (the UI shows
a dialog first) and are written to ``logs/actions.log``.  Power actions use a
short delay so the user can abort from the dashboard.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services.config import LOG_DIR
from services.platform_utils import IS_WINDOWS, is_admin, run_powershell, run_shell_command

router = APIRouter(prefix="/api/controls", tags=["controls"])

ACTION_LOG = LOG_DIR / "actions.log"
POWER_DELAY = 30  # seconds before shutdown/restart, so it can be aborted

# Tracks power actions scheduled through the dashboard so the UI can offer Abort.
_pending: dict[str, Any] | None = None


class ActionRequest(BaseModel):
    action: str = Field(min_length=1, max_length=40)
    confirm: bool = False
    delay: int = Field(default=POWER_DELAY, ge=0, le=600)


def _log(action: str, detail: dict[str, Any]) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    entry = {"ts": time.time(), "at": time.strftime("%Y-%m-%d %H:%M:%S"), "action": action, **detail}
    try:
        with ACTION_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _recent_actions(limit: int = 20) -> list[dict[str, Any]]:
    if not ACTION_LOG.exists():
        return []
    try:
        lines = ACTION_LOG.read_text(encoding="utf-8", errors="replace").strip().splitlines()
    except OSError:
        return []
    items = []
    for line in reversed(lines[-limit:]):
        try:
            items.append(json.loads(line))
        except ValueError:
            continue
    return items


# --------------------------------------------------------------------------- #
# Power actions
# --------------------------------------------------------------------------- #
def _power(action: str, delay: int) -> dict[str, Any]:
    global _pending
    delay = max(delay, 0)
    if IS_WINDOWS:
        flags = {
            "shutdown": ["/s"],
            "restart": ["/r"],
            "sleep": [],
            "lock": [],
            "signout": ["/l"],
        }[action]
        if action in ("shutdown", "restart"):
            result = run_shell_command(
                f'shutdown {flags[0]} /t {delay} /c "Windows Dashboard requested this action"',
                timeout=15,
            )
        elif action == "signout":
            result = run_shell_command("shutdown /l", timeout=15)
        elif action == "lock":
            result = run_shell_command(
                "rundll32.exe user32.dll,LockWorkStation", timeout=15
            )
        else:  # sleep
            # 0,1,0 = suspend (hibernate flag off)
            result = run_shell_command(
                "rundll32.exe powrprof.dll,SetSuspendState 0,1,0", timeout=15
            )
    else:
        commands = {
            "shutdown": f"shutdown -h +{max(1, delay // 60)}" if delay else "shutdown -h now",
            "restart": "shutdown -r +1",
            "lock": "loginctl lock-session" if shutil.which("loginctl") else "",
            "sleep": "systemctl suspend",
            "signout": "loginctl terminate-user $USER" if shutil.which("loginctl") else "",
        }
        command = commands.get(action, "")
        if not command:
            raise HTTPException(status_code=400, detail=f"'{action}' is not supported on this platform")
        result = run_shell_command(command, timeout=15)

    ok = bool(result.get("ok"))
    if ok and action in ("shutdown", "restart"):
        _pending = {"action": action, "at": time.time(), "delay": delay}
    output = ((result.get("stdout") or "") + (result.get("stderr") or "")).strip()
    detail = {"delay": delay, "output": output}
    _log(action, detail)
    if not ok:
        message = detail["output"] or "The action failed"
        if not is_admin() and action in ("shutdown", "restart", "signout"):
            message += " (administrator rights may be required)"
        raise HTTPException(status_code=400, detail=message)
    return {
        "ok": True,
        "action": action,
        "scheduled": action in ("shutdown", "restart") and delay > 0,
        "delay": delay,
        "message": {
            "shutdown": f"Shutting down in {delay}s — run 'shutdown /a' or press Abort to cancel.",
            "restart": f"Restarting in {delay}s — press Abort to cancel.",
            "sleep": "Sending the PC to sleep…",
            "lock": "Locking the workstation…",
            "signout": "Signing out…",
        }[action],
    }


def _abort_power() -> dict[str, Any]:
    global _pending
    if IS_WINDOWS:
        result = run_shell_command("shutdown /a", timeout=10)
    else:
        result = run_shell_command("shutdown -c", timeout=10)
    _log("abort", {"ok": result.get("ok")})
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("stderr") or "Nothing to abort")
    _pending = None
    return {"ok": True, "message": "Pending shutdown/restart cancelled."}


# --------------------------------------------------------------------------- #
# Maintenance
# --------------------------------------------------------------------------- #
@router.post("/empty-recycle-bin")
def empty_recycle_bin(confirm: bool = False) -> dict[str, Any]:
    if not confirm:
        raise HTTPException(status_code=400, detail="Confirmation required")
    if not IS_WINDOWS:
        raise HTTPException(status_code=400, detail="Recycle Bin is only available on Windows")
    attempts: list[str] = []
    for command in (
        "Clear-RecycleBin -Force -ErrorAction Stop",
        "Get-ChildItem -Path \"$env:SystemDrive\\$Recycle.Bin\" -Force -ErrorAction SilentlyContinue | "
        "Remove-Item -Recurse -Force -ErrorAction Stop",
    ):
        result = run_powershell(command, timeout=60)
        attempts.append((result.stderr or result.stdout or "").strip())
        if result.returncode == 0:
            _log("empty-recycle-bin", {"ok": True})
            return {"ok": True, "message": "Recycle Bin emptied."}
    _log("empty-recycle-bin", {"ok": False, "output": attempts})
    raise HTTPException(
        status_code=400,
        detail="Could not empty the Recycle Bin: " + (" ".join(a for a in attempts if a)[:400] or "unknown error"),
    )


@router.post("/flush-dns")
def flush_dns(confirm: bool = False) -> dict[str, Any]:
    if not confirm:
        raise HTTPException(status_code=400, detail="Confirmation required")
    if IS_WINDOWS:
        result = run_shell_command("ipconfig /flushdns", timeout=20)
        output = (result.get("stdout") or "") + (result.get("stderr") or "")
        ok = result.get("ok") or "Successfully flushed" in output or "Flushed" in output
    else:
        result = run_shell_command(
            "resolvectl flush-caches" if shutil.which("resolvectl") else "nscd -i hosts" if shutil.which("nscd") else "",
            timeout=20,
        )
        output = (result.get("stdout") or "") + (result.get("stderr") or "")
        ok = result.get("ok")
    _log("flush-dns", {"ok": ok, "output": output.strip()[:300]})
    if not ok:
        raise HTTPException(status_code=400, detail=output.strip() or "DNS flush failed")
    return {"ok": True, "message": "DNS cache flushed.", "output": output.strip()[:300]}


@router.post("/clear-temp")
def clear_temp(confirm: bool = False, older_hours: int = 24) -> dict[str, Any]:
    """Delete files in the user's temp folder (and Windows\\Temp if elevated)."""
    if not confirm:
        raise HTTPException(status_code=400, detail="Confirmation required")
    older_hours = max(int(older_hours), 0)
    cutoff = time.time() - older_hours * 3600
    targets = {Path(tempfile.gettempdir())}
    local = os.environ.get("LOCALAPPDATA")
    if IS_WINDOWS:
        if local:
            targets.add(Path(local) / "Temp")
        system_root = os.environ.get("SystemRoot", r"C:\Windows")
        if is_admin():
            targets.add(Path(system_root) / "Temp")
        else:
            targets.add(Path(system_root) / "Temp")  # attempt anyway, skip failures

    removed = 0
    freed = 0
    failed = 0
    for folder in targets:
        if not folder.exists():
            continue
        for entry in folder.iterdir():
            # Never delete our own log/config files or folders in use.
            try:
                if entry.name.lower() in {".git", "config.json"}:
                    continue
                stat = entry.stat()
                if stat.st_mtime > cutoff:
                    continue
            except OSError:
                continue
            try:
                if entry.is_dir() and not entry.is_symlink():
                    size = sum(f.stat().st_size for f in entry.rglob("*") if f.is_file())
                    shutil.rmtree(entry)
                else:
                    size = entry.stat().st_size
                    entry.unlink()
                removed += 1
                freed += size
            except (OSError, PermissionError):
                failed += 1

    _log("clear-temp", {"removed": removed, "failed": failed, "freed": freed})
    return {
        "ok": True,
        "removed": removed,
        "failed": failed,
        "freed_bytes": freed,
        "message": f"Removed {removed} item(s), freed {freed / 1048576:.1f} MB"
                   + (f" ({failed} locked/skipped)." if failed else "."),
    }


@router.post("/restart-explorer")
def restart_explorer(confirm: bool = False) -> dict[str, Any]:
    """Restart Windows Explorer (useful after shell tweaks)."""
    if not confirm:
        raise HTTPException(status_code=400, detail="Confirmation required")
    if not IS_WINDOWS:
        raise HTTPException(status_code=400, detail="Only available on Windows")
    run_shell_command("taskkill /f /im explorer.exe", timeout=15)
    subprocess.Popen(["explorer.exe"], close_fds=True)
    _log("restart-explorer", {"ok": True})
    return {"ok": True, "message": "Explorer restarted."}


@router.post("/action")
def perform(body: ActionRequest) -> dict[str, Any]:
    """Single entry point used by the UI's control buttons."""
    action = body.action.strip().lower()
    if action == "abort":
        return _abort_power()
    if action in ("lock", "sleep", "restart", "shutdown", "signout"):
        if not body.confirm:
            raise HTTPException(status_code=400, detail="Confirmation required")
        return _power(action, body.delay)
    raise HTTPException(status_code=404, detail=f"Unknown action '{body.action}'")


@router.get("/status")
def status() -> dict[str, Any]:
    return {
        "admin": is_admin(),
        "platform": "windows" if IS_WINDOWS else os.name,
        "pending_shutdown": bool(_pending and (time.time() - _pending["at"]) < _pending["delay"] + 5),
        "pending": _pending,
        "power_delay": POWER_DELAY,
        "actions": _recent_actions(),
        "features": {
            "power": True,
            "lock": True,
            "recycle_bin": IS_WINDOWS,
            "flush_dns": True,
            "clear_temp": True,
            "restart_explorer": IS_WINDOWS,
        },
    }
