"""Interactive terminal sessions backed by a real pseudo-terminal.

On Windows the recommended backend is ``pywinpty`` (ConPTY).  If it is not
installed the service falls back to plain pipes, which still lets you run
``cmd``/PowerShell commands but loses colours and full-screen TUI programs.
On macOS/Linux the standard ``pty`` module is used.

Each session keeps a scrollback buffer so that reopening the dashboard (or
switching tabs) restores recent output, plus a subscriber list that the
websocket endpoint attaches to.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import threading
import time
import uuid
from collections import deque
from typing import Any, Deque

from services.config import config
from services.platform_utils import IS_WINDOWS, terminate_tree

try:  # POSIX PTY support
    import fcntl
    import pty
    import struct
    import termios
except ImportError:  # pragma: no cover - Windows
    pty = None  # type: ignore[assignment]
    fcntl = None  # type: ignore[assignment]
    termios = None  # type: ignore[assignment]
    struct = None  # type: ignore[assignment]

try:  # ConPTY support on Windows
    from winpty import PtyProcess  # type: ignore

    HAS_PYWINPTY = True
except Exception:  # pragma: no cover - optional dependency
    PtyProcess = None  # type: ignore[assignment]
    HAS_PYWINPTY = False

SCROLLBACK_LIMIT = 200_000  # bytes kept per session
HISTORY_LIMIT = 100


def _safe_put(queue: asyncio.Queue, data: bytes) -> None:
    """Push to a subscriber queue, dropping frames if the client is slow."""
    try:
        queue.put_nowait(data)
    except asyncio.QueueFull:
        pass


def available_shells() -> dict[str, Any]:
    """Describe which shells can be started on this machine."""
    shells: list[dict[str, str]] = []
    if IS_WINDOWS:
        system_root = os.environ.get("SystemRoot", r"C:\Windows")
        cmd = os.path.join(system_root, "System32", "cmd.exe")
        if os.path.exists(cmd):
            shells.append({"id": "cmd", "name": "Command Prompt", "path": cmd})
        pwsh = shutil.which("pwsh")
        if pwsh:
            shells.append({"id": "pwsh", "name": "PowerShell 7", "path": pwsh})
        powershell = os.path.join(system_root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
        if os.path.exists(powershell):
            shells.append({"id": "powershell", "name": "Windows PowerShell", "path": powershell})
        git_bash = shutil.which("bash")
        if git_bash:
            shells.append({"id": "bash", "name": "Git Bash", "path": git_bash})
    else:
        for sid, name in (("bash", "Bash"), ("zsh", "Zsh"), ("fish", "Fish"), ("sh", "Shell")):
            path = shutil.which(sid)
            if path:
                shells.append({"id": sid, "name": name, "path": path})
    return {
        "shells": shells,
        "default": config.get("terminal.default_shell", "auto"),
        "pty": "conpty" if (IS_WINDOWS and HAS_PYWINPTY) else ("pty" if pty else "pipe"),
        "conpty": HAS_PYWINPTY,
    }


def _resolve_shell(shell_id: str | None) -> tuple[str, str]:
    """Return (shell_id, executable) for a shell identifier."""
    info = available_shells()
    available = {s["id"]: s for s in info["shells"]}
    wanted = (shell_id or "").strip()
    if not wanted or wanted == "auto":
        preferred = str(config.get("terminal.default_shell", "auto"))
        wanted = preferred if preferred in available else ""

    if wanted in available:
        return wanted, available[wanted]["path"]

    # Sensible fallback order: PowerShell 7 → Windows PowerShell → CMD → first.
    preferred = ["pwsh", "powershell", "cmd", "bash", "zsh", "sh"] if IS_WINDOWS else [
        "bash", "zsh", "fish", "sh"
    ]
    for shell_id in preferred:
        if shell_id in available:
            return shell_id, available[shell_id]["path"]
    if installed := next(iter(available.values()), None):
        return installed["id"], installed["path"]
    raise RuntimeError("No usable shell was found on this system")


class TerminalSession:
    """A single interactive shell process."""

    def __init__(self, shell_id: str, exe: str, *, cwd: str | None = None,
                 cols: int = 120, rows: int = 30) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.shell_id = shell_id
        self.shell_path = exe
        self.cwd = cwd or os.path.expanduser("~")
        self.cols = max(int(cols), 20)
        self.rows = max(int(rows), 5)
        self.created_at = time.time()
        self.title = f"{shell_id}"
        self.alive = False
        self.mode = "pipe"
        self._lock = threading.RLock()
        self._scrollback: Deque[bytes] = deque()
        self._scrollback_size = 0
        self._subscribers: list[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = []
        self._reader_thread: threading.Thread | None = None
        self._proc: subprocess.Popen | None = None
        self._pty_proc: Any = None
        self._master_fd: int | None = None
        self._input_buffer = ""
        self.history: list[str] = []

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    def start(self) -> None:
        mode_used = False
        if IS_WINDOWS and HAS_PYWINPTY:
            try:
                self._start_conpty()
                mode_used = True
            except Exception as exc:  # noqa: BLE001 - fall back to pipes
                self._emit(f"\r\n[dashboard] ConPTY unavailable ({exc}); using pipe mode\r\n".encode())
        elif pty is not None:
            try:
                self._start_posix_pty()
                mode_used = True
            except Exception as exc:  # noqa: BLE001
                self._emit(f"\r\n[dashboard] PTY unavailable ({exc}); using pipe mode\r\n".encode())
        if not mode_used:
            self._start_pipes()
        self.alive = True

    def _start_conpty(self) -> None:
        env = dict(os.environ)
        env.setdefault("TERM", "xterm-256color")
        proc = PtyProcess.spawn(
            self.shell_path,
            cwd=self.cwd,
            env=env,
            dimensions=(self.rows, self.cols),
        )
        self._pty_proc = proc
        self.mode = "conpty"
        self._reader_thread = threading.Thread(target=self._read_conpty, daemon=True,
                                              name=f"term-{self.id}")
        self._reader_thread.start()

    def _read_conpty(self) -> None:
        proc = self._pty_proc
        while True:
            try:
                data = proc.read(4096)
            except EOFError:
                break
            except Exception:  # noqa: BLE001 - process died
                break
            if not data:
                break
            self._emit(data.encode("utf-8", errors="replace") if isinstance(data, str) else data)
        self._emit(b"\r\n[dashboard] session ended\r\n")
        self.alive = False

    def _start_posix_pty(self) -> None:
        assert pty is not None and fcntl is not None and termios is not None and struct is not None
        master_fd, slave_fd = pty.openpty()
        fcntl.ioctl(slave_fd, termios.TIOCSWINSZ,
                    struct.pack("HHHH", self.rows, self.cols, 0, 0))
        env = dict(os.environ)
        env["TERM"] = "xterm-256color"
        env.setdefault("COLORTERM", "truecolor")
        self._proc = subprocess.Popen(
            [self.shell_path],
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            cwd=self.cwd,
            env=env,
            preexec_fn=os.setsid,
            close_fds=True,
        )
        os.close(slave_fd)
        self._master_fd = master_fd
        self.mode = "pty"
        self._reader_thread = threading.Thread(target=self._read_fd, daemon=True,
                                              name=f"term-{self.id}")
        self._reader_thread.start()

    def _read_fd(self) -> None:
        fd = self._master_fd
        while True:
            try:
                data = os.read(fd, 65536)
            except OSError:
                break
            if not data:
                break
            self._emit(data)
        self._emit(b"\r\n[dashboard] session ended\r\n")
        self.alive = False

    def _start_pipes(self) -> None:
        env = dict(os.environ)
        env.setdefault("TERM", "dumb")
        cmd = [self.shell_path]
        if IS_WINDOWS and self.shell_id in ("powershell", "pwsh"):
            cmd += ["-NoLogo"]
        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=self.cwd,
            env=env,
            bufsize=0,
        )
        self.mode = "pipe"
        self._reader_thread = threading.Thread(target=self._read_pipe, daemon=True,
                                              name=f"term-{self.id}")
        self._reader_thread.start()

    def _read_pipe(self) -> None:
        proc = self._proc
        assert proc is not None and proc.stdout is not None
        while True:
            try:
                chunk = proc.stdout.read(4096)
            except (OSError, ValueError):
                break
            if not chunk:
                break
            self._emit(chunk)
        self._emit(b"\r\n[dashboard] session ended\r\n")
        self.alive = False

    def close(self) -> None:
        self.alive = False
        if self._pty_proc is not None:
            try:
                self._pty_proc.terminate(force=True)
            except Exception:  # noqa: BLE001
                pass
            self._pty_proc = None
        if self._master_fd is not None:
            try:
                os.close(self._master_fd)
            except OSError:
                pass
            self._master_fd = None
        if self._proc is not None:
            try:
                terminate_tree(self._proc.pid)
            except Exception:  # noqa: BLE001 - closing a tab must never fail
                pass
            self._proc = None
        self._detach_all()

    # ------------------------------------------------------------------ #
    # I/O
    # ------------------------------------------------------------------ #
    def _emit(self, data: bytes) -> None:
        if not data:
            return
        with self._lock:
            self._scrollback.append(data)
            self._scrollback_size += len(data)
            while self._scrollback_size > SCROLLBACK_LIMIT and len(self._scrollback) > 1:
                removed = self._scrollback.popleft()
                self._scrollback_size -= len(removed)
            subscribers = list(self._subscribers)
        for loop, queue in subscribers:
            try:
                loop.call_soon_threadsafe(_safe_put, queue, data)
            except RuntimeError:
                # Event loop closed - drop this subscriber.
                self.unsubscribe(loop, queue)

    def write(self, data: str) -> None:
        if not data:
            return
        self._track_history(data)
        if self._pty_proc is not None:
            try:
                self._pty_proc.write(data)
            except Exception:  # noqa: BLE001
                self.alive = False
        elif self._master_fd is not None:
            try:
                os.write(self._master_fd, data.encode("utf-8", errors="replace"))
            except OSError:
                self.alive = False
        elif self._proc is not None and self._proc.stdin is not None:
            try:
                self._proc.stdin.write(data.encode("utf-8", errors="replace"))
                self._proc.stdin.flush()
            except (OSError, ValueError):
                self.alive = False

    def _track_history(self, data: str) -> None:
        """Best-effort capture of typed commands (used by the history sidebar)."""
        for char in data:
            if char in ("\r", "\n"):
                command = self._input_buffer.strip()
                if command and (not self.history or self.history[-1] != command):
                    self.history.append(command)
                    del self.history[:-HISTORY_LIMIT]
                self._input_buffer = ""
            elif char == "\x7f":  # backspace
                self._input_buffer = self._input_buffer[:-1]
            elif char == "\x03":  # ctrl+c
                self._input_buffer = ""
            elif char == "\t":
                continue
            elif char.isprintable():
                self._input_buffer += char

    def resize(self, cols: int, rows: int) -> None:
        self.cols = max(int(cols), 20)
        self.rows = max(int(rows), 5)
        if self._pty_proc is not None:
            try:
                self._pty_proc.setwinsize(self.rows, self.cols)
            except Exception:  # noqa: BLE001
                pass
        elif self._master_fd is not None and fcntl is not None and termios is not None and struct is not None:
            try:
                fcntl.ioctl(self._master_fd, termios.TIOCSWINSZ,
                            struct.pack("HHHH", self.rows, self.cols, 0, 0))
            except OSError:
                pass

    # ------------------------------------------------------------------ #
    # Subscribers (websocket connections)
    # ------------------------------------------------------------------ #
    def subscribe(self, loop: asyncio.AbstractEventLoop) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=512)
        with self._lock:
            self._subscribers.append((loop, queue))
        return queue

    def unsubscribe(self, loop: asyncio.AbstractEventLoop, queue: asyncio.Queue) -> None:
        with self._lock:
            self._subscribers = [(loop_, q) for (loop_, q) in self._subscribers if q is not queue]

    def _detach_all(self) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
            self._subscribers.clear()
        for loop, queue in subscribers:
            try:
                loop.call_soon_threadsafe(_safe_put, queue, b"")
            except RuntimeError:
                pass

    def scrollback(self) -> bytes:
        with self._lock:
            return b"".join(self._scrollback)

    def info(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "shell": self.shell_id,
            "shell_path": self.shell_path,
            "title": self.title,
            "cwd": self.cwd,
            "mode": self.mode,
            "alive": self.alive and self.is_alive(),
            "created_at": self.created_at,
            "cols": self.cols,
            "rows": self.rows,
        }

    def is_alive(self) -> bool:
        if self._pty_proc is not None:
            try:
                return bool(self._pty_proc.isalive())
            except Exception:  # noqa: BLE001
                return False
        if self._proc is not None:
            return self._proc.poll() is None
        return False


class TerminalManager:
    """Registry of terminal sessions."""

    def __init__(self) -> None:
        self._sessions: dict[str, TerminalSession] = {}
        self._lock = threading.RLock()

    def create(self, shell_id: str | None = None, *, cwd: str | None = None,
               cols: int = 120, rows: int = 30) -> TerminalSession:
        resolved_id, exe = _resolve_shell(shell_id)
        session = TerminalSession(resolved_id, exe, cwd=cwd, cols=cols, rows=rows)
        session.start()
        with self._lock:
            self._sessions[session.id] = session
        return session

    def get(self, session_id: str) -> TerminalSession | None:
        with self._lock:
            return self._sessions.get(session_id)

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            sessions = list(self._sessions.values())
        return [s.info() for s in sessions]

    def close(self, session_id: str) -> bool:
        with self._lock:
            session = self._sessions.pop(session_id, None)
        if not session:
            return False
        session.close()
        return True

    def close_all(self) -> None:
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            session.close()

    def history(self) -> list[str]:
        """Merge command history from all live sessions, newest first."""
        with self._lock:
            sessions = list(self._sessions.values())
        merged: list[str] = []
        for session in sessions:
            for command in reversed(session.history):
                if command not in merged:
                    merged.append(command)
        return merged[:HISTORY_LIMIT]


terminal_manager = TerminalManager()
