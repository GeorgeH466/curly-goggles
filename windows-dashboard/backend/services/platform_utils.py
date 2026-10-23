"""Cross-platform helpers for running commands and launching applications.

The dashboard is written for Windows but almost every helper degrades
gracefully on macOS/Linux so the project can be developed and tested anywhere.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from typing import Any, Sequence

IS_WINDOWS = os.name == "nt"
IS_MACOS = sys.platform == "darwin"

# Hide console windows for background commands on Windows.
CREATE_NO_WINDOW = 0x08000000 if IS_WINDOWS else 0
# Detach GUI apps so they survive the dashboard process.
DETACHED_PROCESS = 0x00000008 if IS_WINDOWS else 0
CREATE_NEW_PROCESS_GROUP = 0x00000200 if IS_WINDOWS else 0


class LaunchError(RuntimeError):
    """Raised when an application/path cannot be launched."""


# --------------------------------------------------------------------------- #
# Subprocess helpers
# --------------------------------------------------------------------------- #
def run(
    cmd: Sequence[str] | str,
    *,
    timeout: float = 20,
    cwd: str | Path | None = None,
    shell: bool = False,
    check: bool = False,
) -> subprocess.CompletedProcess:
    """Run a command, capturing text output.  Never raises on non-zero exit."""
    kwargs: dict[str, Any] = {
        "capture_output": True,
        "text": True,
        "errors": "replace",
        "timeout": timeout,
        "cwd": str(cwd) if cwd else None,
        "shell": shell,
    }
    if IS_WINDOWS:
        kwargs["creationflags"] = CREATE_NO_WINDOW
    return subprocess.run(cmd, check=check, **kwargs)


def run_powershell(script: str, timeout: float = 20) -> subprocess.CompletedProcess:
    """Run a PowerShell snippet non-interactively."""
    exe = shutil.which("pwsh") or shutil.which("powershell") or "powershell.exe"
    return run(
        [exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
        timeout=timeout,
    )


def run_shell_command(command: str, timeout: float = 60) -> dict[str, Any]:
    """Run a shell command and return a JSON-friendly result."""
    try:
        proc = run(command, timeout=timeout, shell=True)
        return {
            "ok": proc.returncode == 0,
            "code": proc.returncode,
            "stdout": (proc.stdout or "").strip(),
            "stderr": (proc.stderr or "").strip(),
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "code": None, "stdout": "", "stderr": f"Timed out after {timeout}s"}
    except OSError as exc:
        return {"ok": False, "code": None, "stdout": "", "stderr": str(exc)}


def which(*names: str) -> str | None:
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


def expanded(path: str | os.PathLike[str]) -> str:
    """Expand ``%VARS%``, ``$VARS``, ``~`` and normalise separators."""
    text = str(path)
    if IS_WINDOWS:
        text = os.path.expandvars(text)
    text = os.path.expandvars(os.path.expanduser(text))
    return os.path.normpath(text) if text else text


def is_url(value: str) -> bool:
    lowered = value.strip().lower()
    return lowered.startswith(("http://", "https://", "mailto:"))


# --------------------------------------------------------------------------- #
# Application resolution
# --------------------------------------------------------------------------- #
# Well-known applications keyed by the "path" stored in the config.
_KNOWN_APPS: dict[str, list[str]] = {
    "code": [
        r"%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe",
        r"%ProgramFiles%\Microsoft VS Code\Code.exe",
        r"%ProgramFiles(x86)%\Microsoft VS Code\Code.exe",
    ],
    "code-insiders": [r"%LOCALAPPDATA%\Programs\Microsoft VS Code Insiders\Code - Insiders.exe"],
    "cursor": [r"%LOCALAPPDATA%\Programs\cursor\Cursor.exe"],
    "notepad++": [
        r"%ProgramFiles%\Notepad++\notepad++.exe",
        r"%ProgramFiles(x86)%\Notepad++\notepad++.exe",
    ],
    "chrome": [
        r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
        r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
        r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe",
    ],
    "edge": [
        r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
        r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
    ],
    "firefox": [
        r"%ProgramFiles%\Mozilla Firefox\firefox.exe",
        r"%ProgramFiles(x86)%\Mozilla Firefox\firefox.exe",
    ],
    "discord": [r"%LOCALAPPDATA%\Discord\Update.exe"],
    "spotify": [
        r"%APPDATA%\Spotify\Spotify.exe",
        r"%LOCALAPPDATA%\Microsoft\WindowsApps\Spotify.exe",
    ],
    "steam": [r"%ProgramFiles(x86)%\Steam\steam.exe", r"%ProgramFiles%\Steam\steam.exe"],
    "vlc": [r"%ProgramFiles%\VideoLAN\VLC\vlc.exe", r"%ProgramFiles(x86)%\VideoLAN\VLC\vlc.exe"],
    "obs": [r"%ProgramFiles%\obs-studio\bin\64bit\obs64.exe"],
    "wt.exe": [r"%LOCALAPPDATA%\Microsoft\WindowsApps\wt.exe"],
}

_KNOWN_ARGS: dict[str, list[str]] = {
    # Discord ships an updater stub that needs a flag to start the real client.
    "discord": ["--processStart", "Discord.exe"],
}


def resolve_app(path: str) -> dict[str, Any]:
    """Resolve a configured launcher path to a concrete command.

    Returns ``{"kind": "url"|"folder"|"file"|"command", "target": str, "args": list[str]}``.
    Raises :class:`LaunchError` if nothing suitable is found.
    """
    raw = (path or "").strip()
    if not raw:
        raise LaunchError("No path configured")

    if is_url(raw):
        return {"kind": "url", "target": raw, "args": []}

    # If the user configured an absolute path that exists, use it directly.
    candidate = Path(expanded(raw))
    if candidate.exists():
        kind = "folder" if candidate.is_dir() else "file"
        return {"kind": kind, "target": str(candidate), "args": []}

    key = raw.lower().removeprefix("start ").strip()
    key = os.path.basename(key)
    aliases = {
        "vs code": "code",
        "vscode": "code",
        "visual studio code": "code",
        "explorer": "explorer.exe",
        "file explorer": "explorer.exe",
        "browser": "chrome",
        "notepad++": "notepad++",
        "terminal": "wt.exe",
        "windows terminal": "wt.exe",
        "task manager": "taskmgr.exe",
    }
    key = aliases.get(key, key)

    # 1) PATH lookup (works for code.cmd, wt.exe, explorer.exe, ...)
    found = which(key)
    if found:
        return {"kind": "file", "target": found, "args": list(_KNOWN_ARGS.get(key, []))}

    # 2) Well-known install paths
    if IS_WINDOWS:
        for template in _KNOWN_APPS.get(key, []):
            expanded_template = expanded(template)
            if Path(expanded_template).exists():
                args = list(_KNOWN_ARGS.get(key, []))
                if key == "discord":
                    # Update.exe --processStart Discord.exe
                    return {"kind": "file", "target": expanded_template, "args": args}
                return {"kind": "file", "target": expanded_template, "args": args}
        # Windows "app execution aliases" and store apps.
        if key in {"taskmgr.exe", "explorer.exe", "cmd.exe", "powershell.exe"}:
            return {"kind": "command", "target": key, "args": []}

    # 3) Browser shortcuts fall back to any installed browser, then the web.
    if key in {"chrome", "browser", "msedge", "edge", "firefox", "brave"}:
        for other in ("msedge", "chrome", "firefox"):
            for template in _KNOWN_APPS.get(other, []):
                candidate_path = Path(expanded(template))
                if candidate_path.exists():
                    return {"kind": "file", "target": str(candidate_path), "args": []}
        # No browser binary found: hand the request to the OS default browser.
        return {"kind": "url", "target": "https://www.google.com", "args": []}

    # 4) Best effort: let the OS resolve it (file associations, app aliases).
    if IS_WINDOWS:
        return {"kind": "command", "target": raw, "args": []}
    raise LaunchError(f"Could not find '{raw}'. Set an absolute path in the launcher settings.")


def _popen_detached(args: list[str], cwd: str | None = None) -> None:
    kwargs: dict[str, Any] = {
        "cwd": cwd,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if IS_WINDOWS:
        kwargs["creationflags"] = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(args, **kwargs)


def launch(path: str, args: str | list[str] | None = None, cwd: str | None = None) -> dict[str, Any]:
    """Launch a file, folder, URL or command.  Returns a result dictionary."""
    resolved = resolve_app(path)
    if isinstance(args, str):
        extra = [a for a in args.split() if a]
    else:
        extra = list(args or [])

    kind = resolved["kind"]
    target = resolved["target"]
    cmd_args = resolved["args"] + extra

    try:
        if kind == "url":
            if not webbrowser.open(target):
                raise LaunchError("No default browser available")
        elif kind == "folder":
            open_path(target)
        elif kind == "file" and IS_WINDOWS:
            if cmd_args:
                _popen_detached([target, *cmd_args], cwd=cwd)
            else:
                os.startfile(target)  # type: ignore[attr-defined]
        elif kind == "file":
            _popen_detached([target, *cmd_args], cwd=cwd)
        else:  # command
            if IS_WINDOWS:
                if cmd_args:
                    # `start` handles store-app aliases and file associations.
                    _popen_detached(["cmd.exe", "/c", "start", "", target, *cmd_args], cwd=cwd)
                else:
                    os.startfile(target)  # type: ignore[attr-defined]
            else:
                exe = which(target)
                if not exe:
                    raise LaunchError(f"Command '{target}' was not found on PATH")
                _popen_detached([exe, *cmd_args], cwd=cwd)
    except FileNotFoundError as exc:
        raise LaunchError(f"Could not launch {target}: {exc.strerror or exc}") from exc
    except OSError as exc:
        raise LaunchError(f"Could not launch {target}: {exc}") from exc

    pretty = target if not cmd_args else " ".join([target, *cmd_args])
    return {"ok": True, "launched": pretty, "kind": kind}


# --------------------------------------------------------------------------- #
# Explorer / file-manager helpers
# --------------------------------------------------------------------------- #
def open_path(path: str | Path) -> None:
    """Open a file or folder with its default application."""
    target = expanded(path)
    if not Path(target).exists():
        raise LaunchError(f"Path does not exist: {target}")
    if IS_WINDOWS:
        os.startfile(target)  # type: ignore[attr-defined]
    elif IS_MACOS:
        subprocess.Popen(["open", target])
    else:
        opener = which("xdg-open", "gio")
        if not opener:
            raise LaunchError("No file manager helper available")
        if Path(opener).name == "gio":
            subprocess.Popen([opener, "open", target])
        else:
            subprocess.Popen([opener, target])


def reveal_in_file_manager(path: str | Path) -> None:
    """Open the containing folder and select the item when possible."""
    target = Path(expanded(path))
    if not target.exists():
        raise LaunchError(f"Path does not exist: {target}")
    if IS_WINDOWS:
        if target.is_dir():
            subprocess.Popen(["explorer.exe", str(target)])
        else:
            subprocess.Popen(["explorer.exe", "/select,", str(target)])
    elif IS_MACOS:
        subprocess.Popen(["open", "-R", str(target)])
    else:
        folder = target if target.is_dir() else target.parent
        subprocess.Popen([which("xdg-open") or "xdg-open", str(folder)])


def open_in_vscode(path: str | Path) -> dict[str, Any]:
    """Open a folder in VS Code using either the CLI or a known install path."""
    target = Path(expanded(path))
    if not target.exists():
        raise LaunchError(f"Path does not exist: {target}")

    code_cmd = None
    try:
        from services.config import config  # local import avoids cyclic imports

        code_cmd = (config.get("paths.vscode") or "").strip() or None
    except Exception:  # pragma: no cover - config always available in practice
        code_cmd = None

    candidates: list[str] = []
    if code_cmd and Path(expanded(code_cmd)).exists():
        candidates.append(expanded(code_cmd))
    for name in ("code", "code.cmd", "code-insiders"):
        found = which(name)
        if found:
            candidates.append(found)
    if IS_WINDOWS:
        for template in _KNOWN_APPS["code"]:
            if Path(expanded(template)).exists():
                candidates.append(expanded(template))

    if not candidates:
        raise LaunchError("VS Code was not found. Install it or set paths.vscode in Settings.")

    exe = candidates[0]
    if IS_WINDOWS and exe.lower().endswith((".cmd", ".bat")):
        _popen_detached(["cmd.exe", "/c", exe, str(target)])
    else:
        _popen_detached([exe, str(target)])
    return {"ok": True, "launched": f"{exe} {target}"}


def find_github_desktop() -> str | None:
    """Locate the GitHub Desktop executable, preferring the newest version."""
    try:
        from services.config import config

        configured = (config.get("paths.github_desktop") or "").strip()
        if configured and Path(expanded(configured)).exists():
            return expanded(configured)
    except Exception:
        pass

    if not IS_WINDOWS:
        return None

    candidates: list[Path] = []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        base = Path(local) / "GitHubDesktop"
        if base.is_dir():
            # Install dirs look like app-3.4.3; sort by version-ish name.
            candidates.extend(sorted(base.glob("app-*/GitHubDesktop.exe"), reverse=True))
        candidates.append(base / "GitHubDesktop.exe")
    for template in (
        r"%ProgramFiles%\GitHub Desktop\GitHub Desktop.exe",
        r"%ProgramFiles(x86)%\GitHub Desktop\GitHub Desktop.exe",
    ):
        candidates.append(Path(expanded(template)))
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return None


def launch_github_desktop() -> dict[str, Any]:
    exe = find_github_desktop()
    if exe:
        _popen_detached([exe])
        return {"ok": True, "launched": exe}
    if IS_WINDOWS:
        # Last resort: the registered URI scheme.
        try:
            os.startfile("github-desktop://")  # type: ignore[attr-defined]
            return {"ok": True, "launched": "github-desktop://"}
        except OSError as exc:
            raise LaunchError(
                "GitHub Desktop was not found. Install it or set its path in Settings."
            ) from exc
    raise LaunchError("GitHub Desktop is only available on Windows")


def terminate_tree(pid: int) -> None:
    """Terminate a process and its children (used by the terminal sessions).

    Every step is best-effort: a tab must always close, even if the shell has
    already exited or psutil cannot read the process table.
    """
    import psutil

    proc = None
    try:
        proc = psutil.Process(pid)
    except (psutil.Error, OSError, IndexError, ValueError):
        return

    children: list = []
    try:
        # psutil can raise plain IndexError/OSError if a process disappears on a
        # POSIX box half-way through reading /proc; never let that escape.
        children = proc.children(recursive=True)
    except (psutil.Error, OSError, IndexError, ValueError):
        children = []

    for child in children:
        try:
            child.terminate()
        except (psutil.Error, OSError):
            pass
    try:
        proc.terminate()
    except (psutil.Error, OSError):
        pass

    try:
        _, alive = psutil.wait_procs([proc, *children], timeout=1.5)
    except (psutil.Error, OSError, IndexError, ValueError):
        alive = []
    for leftover in alive:
        try:
            leftover.kill()
        except (psutil.Error, OSError):
            pass


def nice_path(path: str | Path) -> str:
    """Shorten a path for display in the UI."""
    try:
        return str(Path(path))
    except (OSError, ValueError):
        return str(path)


def timestamp() -> float:
    return time.time()


# --------------------------------------------------------------------------- #
# Windows shell helpers
# --------------------------------------------------------------------------- #
# Known Folder IDs (stable across Windows versions, honour OneDrive redirection).
_KNOWN_FOLDER_GUIDS = {
    "Desktop": "B4BFCC3A-DB2C-424C-B029-7FE99A87C641",
    "Documents": "FDD39AD0-238F-46AF-ADB4-6C85480369C7",
    "Downloads": "374DE290-123F-4565-9164-39C4925E467B",
    "Pictures": "33E28130-4E1E-4676-835A-98395C3BC3BB",
    "Music": "4BD8D571-6D19-48D3-BE97-422220080E43",
    "Videos": "18989B1D-99B5-455B-841C-AB7C74E4DDFC",
    "Home": "5E6C858F-0E22-4760-9AFE-EA3317B67173",
}


def known_folders() -> dict[str, str]:
    """Return the user's special folders, using the Windows shell API when possible."""
    home = Path.home()
    folders: dict[str, str] = {
        "Home": str(home),
        "Desktop": str(home / "Desktop"),
        "Documents": str(home / "Documents"),
        "Downloads": str(home / "Downloads"),
        "Pictures": str(home / "Pictures"),
        "Music": str(home / "Music"),
        "Videos": str(home / "Videos"),
    }
    if IS_WINDOWS:
        try:
            import ctypes
            import uuid

            shell32 = ctypes.windll.shell32  # type: ignore[attr-defined]
            ole32 = ctypes.windll.ole32  # type: ignore[attr-defined]
            ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
            class GUID(ctypes.Structure):
                _fields_ = [
                    ("Data1", ctypes.c_ulong),
                    ("Data2", ctypes.c_ushort),
                    ("Data3", ctypes.c_ushort),
                    ("Data4", ctypes.c_ubyte * 8),
                ]

            shell32.SHGetKnownFolderPath.restype = ctypes.c_long
            for name, guid in _KNOWN_FOLDER_GUIDS.items():
                folder_guid = GUID.from_buffer_copy(uuid.UUID(guid).bytes_le)
                ptr = ctypes.c_wchar_p()
                result = shell32.SHGetKnownFolderPath(
                    ctypes.byref(folder_guid), 0, None, ctypes.byref(ptr)
                )
                if result == 0 and ptr.value:
                    folders[name] = ptr.value
                if ptr:
                    ole32.CoTaskMemFree(ptr)
        except Exception:  # noqa: BLE001 - keep the fallbacks
            pass
    return {k: v for k, v in folders.items() if Path(v).exists()}


def list_drives() -> list[dict[str, str]]:
    """List mounted volumes for the file explorer sidebar."""
    import psutil

    drives: list[dict[str, str]] = []
    try:
        for part in psutil.disk_partitions(all=False):
            if part.fstype.lower() in ("", "squashfs", "tmpfs", "overlay", "iso9660") and not IS_WINDOWS:
                continue
            label = part.device.rstrip("\\/")
            drives.append({"name": label or part.mountpoint, "path": part.mountpoint,
                           "fstype": part.fstype})
    except (OSError, AttributeError):
        pass
    if IS_WINDOWS:
        # Include network/removable drives psutil skips.
        try:
            import string

            for letter in string.ascii_uppercase:
                root = f"{letter}:\\"
                if os.path.exists(root) and not any(d["path"].lower() == root.lower() for d in drives):
                    drives.append({"name": f"{letter}:", "path": root, "fstype": ""})
        except OSError:
            pass
    if not drives:
        drives.append({"name": "/", "path": "/", "fstype": ""})
    return drives


def send_to_recycle_bin(path: str | Path) -> None:
    """Move a file/folder to the Recycle Bin (Windows) or Trash (macOS/Linux)."""
    target = str(Path(expanded(path)))
    if not os.path.exists(target):
        raise FileNotFoundError(target)
    if IS_WINDOWS:
        import ctypes
        from ctypes import wintypes

        class SHFILEOPSTRUCTW(ctypes.Structure):
            _fields_ = [
                ("hwnd", wintypes.HWND),
                ("wFunc", wintypes.UINT),
                ("pFrom", wintypes.LPCWSTR),
                ("pTo", wintypes.LPCWSTR),
                ("fFlags", wintypes.WORD),
                ("fAnyOperationsAborted", wintypes.BOOL),
                ("hNameMappings", wintypes.LPVOID),
                ("lpszProgressTitle", wintypes.LPCWSTR),
            ]

        FO_DELETE = 3
        FOF_ALLOWUNDO = 0x0040
        FOF_NOCONFIRMATION = 0x0010
        FOF_NOERRORUI = 0x0400
        FOF_SILENT = 0x0004
        op = SHFILEOPSTRUCTW()
        op.wFunc = FO_DELETE
        # SHFileOperation needs a double-NUL terminated path list.
        op.pFrom = target + "\0"
        op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_NOERRORUI | FOF_SILENT
        result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))  # type: ignore[attr-defined]
        if result != 0:
            raise OSError(result, f"Could not move to the Recycle Bin (code {result})")
        return
    if IS_MACOS:
        trash = Path.home() / ".Trash"
        trash.mkdir(exist_ok=True)
        os.replace(target, trash / Path(target).name)
        return
    # Linux: prefer `gio trash` (freedesktop), else move to ~/.local/share/Trash.
    gio = which("gio")
    if gio:
        proc = run([gio, "trash", "--", target], timeout=10)
        if proc.returncode == 0:
            return
    import shutil as _shutil

    trash_dir = Path.home() / ".local" / "share" / "Trash" / "files"
    trash_dir.mkdir(parents=True, exist_ok=True)
    _shutil.move(target, trash_dir / (Path(target).name or "item"))


def is_admin() -> bool:
    """True when the process has elevated privileges."""
    try:
        if IS_WINDOWS:
            import ctypes

            return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
        return os.geteuid() == 0  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        return False
