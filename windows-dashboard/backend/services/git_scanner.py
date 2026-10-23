"""Git repository discovery and inspection.

* Scans a configurable list of folders (``~/Documents/GitHub``, ``~/repos`` …)
  for Git working copies, without following symlinks or descending into
  ``node_modules`` / virtualenvs.
* Uses GitPython to read branch, HEAD commit, remotes and status.
* Performs pull/fetch through GitPython but captures the user-facing output.
* Optionally polls the GitHub REST API for unread notifications.
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from typing import Any

try:  # GitPython is optional at import time so the app can still boot.
    import git
    from git import GitCommandError, InvalidGitRepositoryError, NoSuchPathError
except Exception:  # pragma: no cover - handled at call sites
    git = None  # type: ignore[assignment]

    class GitCommandError(Exception):  # type: ignore[no-redef]
        pass

    class InvalidGitRepositoryError(Exception):  # type: ignore[no-redef]
        pass

    class NoSuchPathError(Exception):  # type: ignore[no-redef]
        pass

from services.config import config
from services.platform_utils import expanded, open_in_vscode, reveal_in_file_manager, which

# Folders that never contain repositories we care about (and are huge).
PRUNE_DIRS = {
    "node_modules", ".venv", "venv", "env", "__pycache__", ".cache", ".npm", ".git",
    "AppData", "Windows", "Program Files", "Program Files (x86)", "$Recycle.Bin",
    "System Volume Information", "dist", "build", "target", ".next", ".tox",
}

MAX_SCAN_SECONDS = 12.0


class GitScanner:
    """Discovers repositories and serves cached metadata about them."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._repos: list[dict[str, Any]] = []
        self._scanned_at: float = 0.0
        self._extra_paths: list[str] = []
        self._notifications: dict[str, Any] = {"items": [], "fetched_at": 0, "error": None, "login": ""}
        self._github_lock = threading.Lock()
        self._load_extra_paths()

    # ------------------------------------------------------------------ #
    # Settings helpers
    # ------------------------------------------------------------------ #
    def _load_extra_paths(self) -> None:
        stored = config.get("repos.extra_paths", []) or []
        self._extra_paths = [p for p in stored if isinstance(p, str)]

    def scan_paths(self) -> list[str]:
        paths = list(config.get("repos.scan_paths", []) or []) + self._extra_paths
        seen: set[str] = set()
        result: list[str] = []
        for raw in paths:
            if not raw:
                continue
            path = expanded(raw)
            key = os.path.normcase(path)
            if key in seen or not os.path.isdir(path):
                continue
            seen.add(key)
            result.append(path)
        return result

    # ------------------------------------------------------------------ #
    # Discovery
    # ------------------------------------------------------------------ #
    def _discover(self) -> list[str]:
        """Return absolute paths of Git working copies found on disk."""
        roots = self.scan_paths()
        max_depth = int(config.get("repos.max_depth", 3) or 3)
        max_repos = int(config.get("repos.max_repos", 200) or 200)
        found: list[str] = []
        seen: set[str] = set()
        deadline = time.time() + MAX_SCAN_SECONDS

        def add(path: str) -> None:
            key = os.path.normcase(os.path.abspath(path))
            if key not in seen:
                seen.add(key)
                found.append(os.path.abspath(path))

        for root in roots:
            if time.time() > deadline or len(found) >= max_repos:
                break
            # The root itself may be a repository.
            if os.path.isdir(os.path.join(root, ".git")) or os.path.isfile(os.path.join(root, ".git")):
                add(root)
            base_depth = root.rstrip("\\/").count(os.sep)
            for dirpath, dirnames, _filenames in os.walk(root, followlinks=False):
                if time.time() > deadline or len(found) >= max_repos:
                    break
                depth = dirpath.count(os.sep) - base_depth
                # Never descend into a repository (covers nested worktrees).
                if ".git" in dirnames or os.path.isfile(os.path.join(dirpath, ".git")):
                    if os.path.normcase(os.path.abspath(dirpath)) != os.path.normcase(os.path.abspath(root)):
                        add(dirpath)
                    dirnames[:] = []
                    continue
                if depth >= max_depth:
                    dirnames[:] = []
                    continue
                dirnames[:] = [
                    d for d in dirnames
                    if d not in PRUNE_DIRS and not d.startswith(".") and not d.endswith(".git")
                ]
        return found

    # ------------------------------------------------------------------ #
    # Repository info
    # ------------------------------------------------------------------ #
    def repo_info(self, path: str, *, status: bool = True) -> dict[str, Any] | None:
        """Read metadata for a single working copy."""
        full = expanded(path)
        if not os.path.isdir(full):
            return None
        info: dict[str, Any] = {
            "path": full,
            "name": os.path.basename(full.rstrip("\\/")) or full,
            "branch": None,
            "detached": False,
            "commit": None,
            "commit_message": "",
            "commit_author": "",
            "commit_date": None,
            "remote": "",
            "remote_name": "origin",
            "ahead": 0,
            "behind": 0,
            "dirty": False,
            "modified": 0,
            "untracked": 0,
            "staged": 0,
            "stash": 0,
            "error": None,
            "is_repo": True,
        }

        if git is None:
            info["error"] = "GitPython is not installed"
            info["is_repo"] = False
            return info

        try:
            repo = git.Repo(full, search_parent_directories=False)
        except (InvalidGitRepositoryError, NoSuchPathError, ValueError, OSError):
            info["is_repo"] = False
            info["error"] = "Not a Git repository"
            return info

        try:
            if repo.head.is_detached:
                info["detached"] = True
                info["branch"] = "detached HEAD"
            else:
                try:
                    info["branch"] = repo.active_branch.name
                except (TypeError, ValueError):
                    info["branch"] = repo.git.rev_parse("--abbrev-ref", "HEAD")

            if repo.head.is_valid():
                commit = repo.head.commit
                info["commit"] = commit.hexsha[:10]
                info["commit_message"] = (commit.message or "").strip().splitlines()[0][:200] if commit.message else ""
                info["commit_author"] = f"{commit.author.name}"
                info["commit_date"] = commit.committed_date

            try:
                remotes = list(repo.remotes)
                if remotes:
                    origin = next((r for r in remotes if r.name == "origin"), remotes[0])
                    info["remote_name"] = origin.name
                    url = next(iter(origin.urls), "")
                    info["remote"] = self._mask_credentials(url)
            except (GitCommandError, OSError, ValueError):
                pass

            if status:
                self._read_status(repo, info)
        except (GitCommandError, OSError, ValueError) as exc:
            info["error"] = str(exc).strip().splitlines()[0][:200] if str(exc).strip() else "Git error"
        finally:
            try:
                repo.close()
            except Exception:
                pass
        return info

    @staticmethod
    def _mask_credentials(url: str) -> str:
        """Hide tokens embedded in remote URLs before sending them to the UI."""
        if "://" in url and "@" in url:
            scheme, rest = url.split("://", 1)
            creds, _, host = rest.rpartition("@")
            if ":" in creds:
                user = creds.split(":", 1)[0]
                return f"{scheme}://{user}:***@{host}"
            return f"{scheme}://***@{host}"
        return url

    @staticmethod
    def _read_status(repo: Any, info: dict[str, Any]) -> None:
        """Parse one porcelain status call (branch header + file states)."""
        try:
            raw = repo.git.status("--porcelain=v1", "--branch", kill_after_timeout=8)
        except (GitCommandError, OSError):
            return
        modified = untracked = staged = 0
        for line in raw.splitlines():
            if line.startswith("## "):
                header = line[3:]
                if "..." in header:
                    local, _, remote = header.partition("...")
                    info["branch_from_status"] = local.split()[0] if local.split() else ""
                    if "[" in remote and "]" in remote:
                        flags = remote[remote.index("[") + 1: remote.index("]")]
                        for part in flags.split(","):
                            part = part.strip()
                            if part.startswith("ahead "):
                                try:
                                    info["ahead"] = int(part.split()[1])
                                except (IndexError, ValueError):
                                    pass
                            elif part.startswith("behind "):
                                try:
                                    info["behind"] = int(part.split()[1])
                                except (IndexError, ValueError):
                                    pass
                elif "No commits yet" in header:
                    info["branch_from_status"] = header.split(" ")[0]
                continue
            if len(line) < 3:
                continue
            code = line[:2]
            if code == "??":
                untracked += 1
            else:
                if code[0] not in (" ", "?"):
                    staged += 1
                if code[1] not in (" ", "?"):
                    modified += 1
        info["modified"] = modified
        info["untracked"] = untracked
        info["staged"] = staged
        info["dirty"] = bool(modified or untracked or staged)
        if info.get("branch_from_status") and not info.get("branch"):
            info["branch"] = info["branch_from_status"]
        info.pop("branch_from_status", None)

    # ------------------------------------------------------------------ #
    # Scanning + cache
    # ------------------------------------------------------------------ #
    def scan(self, force: bool = False) -> dict[str, Any]:
        """Scan for repositories, returning cached results when fresh."""
        cache_seconds = float(config.get("repos.cache_seconds", 20) or 0)
        with self._lock:
            if not force and self._repos and (time.time() - self._scanned_at) < cache_seconds:
                return {
                    "repos": [dict(r) for r in self._repos],
                    "scanned_at": self._scanned_at,
                    "cached": True,
                    "roots": self.scan_paths(),
                }

        started = time.time()
        paths = self._discover()
        repos: list[dict[str, Any]] = []
        for path in paths:
            info = self.repo_info(path)
            if info and info.get("is_repo"):
                repos.append(info)
        repos.sort(key=lambda r: (r.get("name") or "").lower())

        with self._lock:
            self._repos = repos
            self._scanned_at = time.time()
        return {
            "repos": [dict(r) for r in repos],
            "scanned_at": self._scanned_at,
            "cached": False,
            "duration": round(time.time() - started, 2),
            "roots": self.scan_paths(),
        }

    def cached(self) -> dict[str, Any]:
        with self._lock:
            return {
                "repos": [dict(r) for r in self._repos],
                "scanned_at": self._scanned_at,
                "cached": True,
                "roots": self.scan_paths(),
            }

    def get(self, path: str) -> dict[str, Any] | None:
        target = os.path.normcase(expanded(path))
        with self._lock:
            for repo in self._repos:
                if os.path.normcase(repo["path"]) == target:
                    return dict(repo)
        return self.repo_info(path)

    def add_path(self, path: str) -> dict[str, Any]:
        """Register a folder to scan on every refresh."""
        full = expanded(path)
        if not os.path.isdir(full):
            raise FileNotFoundError(f"Folder not found: {full}")
        info = self.repo_info(full)
        if not info or not info.get("is_repo"):
            raise ValueError(f"{full} is not a Git repository")
        with self._lock:
            if full not in self._extra_paths:
                self._extra_paths.append(full)
                config.set("repos.extra_paths", self._extra_paths)
            existing = {os.path.normcase(r["path"]) for r in self._repos}
            if os.path.normcase(full) not in existing:
                self._repos.append(info)
                self._scanned_at = time.time()
        return info

    def remove_path(self, path: str) -> None:
        target = os.path.normcase(expanded(path))
        with self._lock:
            self._extra_paths = [p for p in self._extra_paths if os.path.normcase(p) != target]
            config.set("repos.extra_paths", self._extra_paths)
            self._repos = [r for r in self._repos if os.path.normcase(r["path"]) != target]

    # ------------------------------------------------------------------ #
    # Actions
    # ------------------------------------------------------------------ #
    def _git_cmd(self, path: str, *args: str, timeout: int = 90) -> dict[str, Any]:
        if git is None:
            return {"ok": False, "output": "GitPython is not installed"}
        try:
            repo = git.Repo(expanded(path), search_parent_directories=False)
        except (InvalidGitRepositoryError, NoSuchPathError, ValueError, OSError) as exc:
            return {"ok": False, "output": f"Not a Git repository: {exc}"}
        try:
            executable = getattr(repo.git, "GIT_PYTHON_GIT_EXECUTABLE", "git")
            result = repo.git.execute(
                [executable, *args],
                with_extended_output=True,
                with_exceptions=False,
                kill_after_timeout=timeout,
                env={
                    **os.environ,
                    "GIT_TERMINAL_PROMPT": "0",
                    "GIT_ASKPASS": "echo",
                    "LC_ALL": "C",
                },
            )
            status, out, err = result
            output = (out + "\n" + err).strip() if err else (out or "").strip()
            return {"ok": status == 0, "output": output or ("Done" if status == 0 else "Command failed")}
        except Exception as exc:  # noqa: BLE001 - report anything to the UI
            return {"ok": False, "output": str(exc)}
        finally:
            try:
                repo.close()
            except Exception:
                pass

    def pull(self, path: str) -> dict[str, Any]:
        result = self._git_cmd(path, "pull", "--ff-only", timeout=120)
        if not result["ok"] and "would be overwritten" in result["output"]:
            result["hint"] = "Commit or stash your changes first."
        self.invalidate()
        return result

    def fetch(self, path: str) -> dict[str, Any]:
        result = self._git_cmd(path, "fetch", "--all", "--prune", timeout=120)
        self.invalidate()
        return result

    def invalidate(self) -> None:
        with self._lock:
            self._scanned_at = 0.0

    def open_folder(self, path: str) -> None:
        reveal_in_file_manager(path)

    def open_in_vscode(self, path: str) -> None:
        open_in_vscode(path)

    def open_remote(self, path: str) -> dict[str, Any]:
        info = self.repo_info(path, status=False)
        remote = (info or {}).get("remote", "")
        if not remote:
            raise ValueError("This repository has no remote configured")
        url = remote
        if url.startswith("git@"):
            host, _, repo_path = url.partition(":")
            url = f"https://{host.split('@')[-1]}/{repo_path}"
        if url.endswith(".git"):
            url = url[:-4]
        if not url.startswith(("http://", "https://")):
            raise ValueError(f"Unsupported remote: {remote}")
        open_path_url(url)
        return {"ok": True, "url": url}

    # ------------------------------------------------------------------ #
    # GitHub notifications
    # ------------------------------------------------------------------ #
    def notifications(self, force: bool = False) -> dict[str, Any]:
        token = (config.get("repos.notifications.token") or "").strip()
        enabled = bool(config.get("repos.notifications.enabled"))
        if not enabled or not token:
            return {"enabled": False, "items": [], "error": None, "login": ""}

        refresh = float(config.get("repos.notifications.refresh_seconds", 120) or 120)
        with self._github_lock:
            cached = self._notifications
            if not force and cached["items"] and (time.time() - cached["fetched_at"]) < refresh:
                return {"enabled": True, **cached}

        def api(url: str) -> Any:
            request = urllib.request.Request(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "WindowsDashboard/1.0",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            with urllib.request.urlopen(request, timeout=12) as response:
                return json.loads(response.read().decode("utf-8"))

        items: list[dict[str, Any]] = []
        login = cached.get("login", "")
        error: str | None = None
        try:
            if not login:
                try:
                    login = str(api("https://api.github.com/user").get("login", ""))
                except (urllib.error.URLError, ValueError, OSError):
                    login = ""
            raw = api("https://api.github.com/notifications?all=false&per_page=25")
            for item in raw if isinstance(raw, list) else []:
                subject = item.get("subject") or {}
                repo = (item.get("repository") or {}).get("full_name", "")
                items.append(
                    {
                        "id": item.get("id"),
                        "title": subject.get("title", "Untitled"),
                        "type": subject.get("type", ""),
                        "reason": item.get("reason", ""),
                        "repo": repo,
                        "url": (subject.get("url") or "").replace("api.github.com/repos", "github.com"),
                        "html_url": (item.get("repository") or {}).get("html_url", ""),
                        "updated_at": item.get("updated_at"),
                        "unread": item.get("unread", True),
                    }
                )
        except urllib.error.HTTPError as exc:
            error = {
                401: "GitHub token is invalid or expired.",
                403: "GitHub API rate limit reached or token lacks scopes.",
                404: "GitHub API endpoint not found.",
            }.get(exc.code, f"GitHub API error {exc.code}")
        except (urllib.error.URLError, TimeoutError, OSError):
            error = "Could not reach GitHub (offline?)."
        except ValueError:
            error = "Unexpected response from GitHub."

        payload = {"items": items, "fetched_at": time.time(), "error": error, "login": login}
        with self._github_lock:
            self._notifications = payload
        return {"enabled": True, **payload}

    # ------------------------------------------------------------------ #
    # Environment
    # ------------------------------------------------------------------ #
    @staticmethod
    def environment() -> dict[str, Any]:
        git_exe = which("git")
        version = None
        if git_exe:
            try:
                import subprocess

                out = subprocess.run(
                    [git_exe, "--version"], capture_output=True, text=True, timeout=5
                )
                version = out.stdout.strip()
            except Exception:  # noqa: BLE001
                version = None
        from services.platform_utils import find_github_desktop

        desktop = find_github_desktop()
        return {
            "git": {"available": bool(git_exe), "path": git_exe, "version": version},
            "gitpython": {"available": git is not None},
            "github_desktop": {"available": bool(desktop), "path": desktop},
        }


def open_path_url(url: str) -> None:
    import webbrowser

    if not webbrowser.open(url):
        raise RuntimeError("Could not open the default browser")


scanner = GitScanner()
