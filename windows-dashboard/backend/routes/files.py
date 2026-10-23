"""File Explorer Lite endpoints.

Every path is normalised and must exist (except for creation targets).  Deletes
go to the Recycle Bin by default so the panel is hard to misuse.
"""

from __future__ import annotations

import mimetypes
import os
import shutil
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from services.config import config
from services.platform_utils import (
    LaunchError,
    expanded,
    known_folders,
    list_drives,
    open_path,
    reveal_in_file_manager,
    send_to_recycle_bin,
)

router = APIRouter(prefix="/api/files", tags=["files"])

TEXT_PREVIEW_LIMIT = 256_000  # bytes
MAX_ENTRIES = 3000


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class PathRequest(BaseModel):
    path: str = Field(min_length=1, max_length=4096)


class CreateFolder(PathRequest):
    name: str = Field(min_length=1, max_length=255)


class RenameRequest(PathRequest):
    new_name: str = Field(min_length=1, max_length=255)


class DeleteRequest(PathRequest):
    permanent: bool = False


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _resolve(path: str, *, must_exist: bool = True) -> Path:
    raw = expanded(path)
    if not raw:
        raise HTTPException(status_code=400, detail="Empty path")
    target = Path(raw)
    if not target.is_absolute():
        raise HTTPException(status_code=400, detail="Path must be absolute")
    if must_exist and not target.exists():
        raise HTTPException(status_code=404, detail=f"Path not found: {target}")
    return target


def _is_hidden(path: Path) -> bool:
    name = path.name
    if name.startswith("."):
        return True
    if os.name == "nt":
        try:
            import ctypes

            attrs = ctypes.windll.kernel32.GetFileAttributesW(str(path))  # type: ignore[attr-defined]
            if attrs != -1 and attrs & 0x2:  # FILE_ATTRIBUTE_HIDDEN
                return True
        except Exception:  # noqa: BLE001
            return False
    return False


def _entry(path: Path) -> dict[str, Any]:
    try:
        stat = path.stat()
        is_dir = path.is_dir()
        size = 0 if is_dir else stat.st_size
        mtime = stat.st_mtime
        item_count = None
        if is_dir:
            try:
                with os.scandir(path) as it:
                    item_count = sum(1 for _ in it)
            except OSError:
                item_count = None
        return {
            "name": path.name or str(path),
            "path": str(path),
            "is_dir": is_dir,
            "size": size,
            "modified": mtime,
            "created": stat.st_ctime,
            "hidden": _is_hidden(path),
            "ext": (path.suffix.lower().lstrip(".") if not is_dir else ""),
            "items": item_count,
            "readonly": not os.access(path, os.W_OK),
        }
    except OSError:
        return {
            "name": path.name,
            "path": str(path),
            "is_dir": path.is_dir(),
            "size": 0,
            "modified": None,
            "created": None,
            "hidden": False,
            "ext": "",
            "items": None,
            "readonly": True,
            "error": "permission denied",
        }


def _validate_name(name: str) -> str:
    name = name.strip().strip("\\/")
    if not name or name in (".", ".."):
        raise HTTPException(status_code=400, detail="Invalid name")
    if any(sep in name for sep in ("/", "\\", "\0")) or (os.name == "nt" and ":" in name):
        raise HTTPException(status_code=400, detail="Name cannot contain path separators")
    return name


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #
@router.get("/roots")
def roots() -> dict[str, Any]:
    """Drive list plus the pinned common folders shown in the sidebar."""
    home = Path.home()
    return {
        "home": str(home),
        "drives": list_drives(),
        "common": known_folders(),
        "start": str(config.get("files.start_path") or home),
        "show_hidden": bool(config.get("files.show_hidden")),
    }


@router.get("/list")
def list_dir(
    path: str | None = Query(None),
    hidden: bool | None = Query(None),
    sort: str = Query("name", pattern="^(name|size|modified|type)$"),
    dirs_first: bool = Query(True),
) -> dict[str, Any]:
    """List a directory's contents."""
    target = _resolve(path or str(Path.home()))
    if not target.is_dir():
        raise HTTPException(status_code=400, detail="Not a folder")
    show_hidden = bool(config.get("files.show_hidden")) if hidden is None else hidden

    entries: list[dict[str, Any]] = []
    try:
        with os.scandir(target) as it:
            for index, item in enumerate(it):
                if index >= MAX_ENTRIES:
                    break
                entries.append(_entry(Path(item.path)))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="Access denied to this folder") from exc
    except OSError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not show_hidden:
        entries = [e for e in entries if not e["hidden"]]

    key = {
        "name": lambda e: e["name"].lower(),
        "size": lambda e: e["size"],
        "modified": lambda e: e["modified"] or 0,
        "type": lambda e: (e["ext"], e["name"].lower()),
    }[sort]
    entries.sort(key=key)
    if dirs_first:
        entries.sort(key=lambda e: not e["is_dir"])

    parent = str(target.parent) if target.parent != target else None
    return {
        "path": str(target),
        "parent": parent,
        "entries": entries,
        "truncated": len(entries) >= MAX_ENTRIES,
        "breadcrumbs": _breadcrumbs(target),
        "writable": os.access(target, os.W_OK),
    }


def _breadcrumbs(path: Path) -> list[dict[str, str]]:
    """Build clickable path segments."""
    parts: list[dict[str, str]] = []
    current = path
    chain: list[Path] = []
    while True:
        chain.append(current)
        if current.parent == current:
            break
        current = current.parent
    for node in reversed(chain):
        label = node.name or str(node)
        parts.append({"name": label, "path": str(node)})
    return parts


@router.post("/open")
def open_item(body: PathRequest) -> dict[str, Any]:
    target = _resolve(body.path)
    try:
        open_path(target)
    except LaunchError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "opened": str(target)}


@router.post("/reveal")
def reveal(body: PathRequest) -> dict[str, Any]:
    target = _resolve(body.path)
    try:
        reveal_in_file_manager(target)
    except LaunchError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


@router.post("/mkdir")
def make_folder(body: CreateFolder) -> dict[str, Any]:
    parent = _resolve(body.path)
    if not parent.is_dir():
        raise HTTPException(status_code=400, detail="Parent is not a folder")
    target = parent / _validate_name(body.name)
    if target.exists():
        raise HTTPException(status_code=409, detail="A file or folder with that name already exists")
    try:
        target.mkdir()
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"Could not create folder: {exc}") from exc
    return {"ok": True, "path": str(target)}


@router.post("/rename")
def rename(body: RenameRequest) -> dict[str, Any]:
    source = _resolve(body.path)
    target = source.parent / _validate_name(body.new_name)
    if target.exists() and target != source:
        raise HTTPException(status_code=409, detail="A file or folder with that name already exists")
    try:
        source.rename(target)
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"Could not rename: {exc}") from exc
    return {"ok": True, "path": str(target)}


@router.post("/delete")
def delete(body: DeleteRequest) -> dict[str, Any]:
    target = _resolve(body.path)
    if target == Path(target.anchor):
        raise HTTPException(status_code=400, detail="Refusing to delete a drive root")
    if target == Path.home():
        raise HTTPException(status_code=400, detail="Refusing to delete the home folder")
    use_recycle = bool(config.get("files.delete_to_recycle_bin", True)) and not body.permanent
    try:
        if use_recycle:
            send_to_recycle_bin(target)
        elif target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        else:
            target.unlink()
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=f"Access denied: {exc}") from exc
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"Could not delete: {exc}") from exc
    return {"ok": True, "recycled": use_recycle, "path": str(target)}


@router.get("/preview")
def preview(path: str = Query(..., min_length=1)) -> dict[str, Any]:
    """Return text content for in-browser preview (size limited)."""
    target = _resolve(path)
    if target.is_dir():
        raise HTTPException(status_code=400, detail="Cannot preview a folder")
    size = target.stat().st_size
    if size > TEXT_PREVIEW_LIMIT:
        return {"ok": False, "reason": "too-large", "size": size}
    try:
        raw = target.read_bytes()
    except OSError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if b"\x00" in raw[:2048]:
        return {"ok": False, "reason": "binary", "size": size}
    try:
        text = raw.decode("utf-8")
        encoding = "utf-8"
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
        encoding = "latin-1"
    mime, _ = mimetypes.guess_type(str(target))
    return {
        "ok": True,
        "content": text[:400_000],
        "size": size,
        "encoding": encoding,
        "mime": mime,
        "modified": target.stat().st_mtime,
    }


@router.get("/download")
def download(path: str = Query(..., min_length=1)) -> FileResponse:
    target = _resolve(path)
    if target.is_dir():
        raise HTTPException(status_code=400, detail="Cannot download a folder")
    return FileResponse(str(target), filename=target.name)


@router.get("/search")
def search(
    path: str = Query(..., min_length=1),
    query: str = Query(..., min_length=1, max_length=200),
    limit: int = Query(120, ge=1, le=500),
) -> dict[str, Any]:
    """Case-insensitive filename search beneath a folder (depth limited)."""
    root = _resolve(path)
    needle = query.lower()
    results: list[dict[str, Any]] = []
    started = time.time()
    for dirpath, dirnames, filenames in os.walk(root):
        if time.time() - started > 6:
            break
        dirnames[:] = [d for d in dirnames if not d.startswith(".")][:60]
        for name in dirnames + filenames:
            if needle in name.lower():
                candidate = Path(dirpath) / name
                results.append(_entry(candidate))
                if len(results) >= limit:
                    return {"results": results, "truncated": True}
    return {"results": results, "truncated": False}


@router.post("/settings")
def settings(show_hidden: bool | None = None, start_path: str | None = None,
             delete_to_recycle_bin: bool | None = None) -> dict[str, Any]:
    patch: dict[str, Any] = {}
    if show_hidden is not None:
        patch["show_hidden"] = show_hidden
    if delete_to_recycle_bin is not None:
        patch["delete_to_recycle_bin"] = delete_to_recycle_bin
    if start_path:
        candidate = Path(expanded(start_path))
        if not candidate.is_dir():
            raise HTTPException(status_code=400, detail="Start path must be an existing folder")
        patch["start_path"] = str(candidate)
    if patch:
        config.update({"files": patch})
    return {"ok": True, **config.get("files")}
