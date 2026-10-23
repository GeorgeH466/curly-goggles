"""Markdown scratchpad stored as files in ``notes/``."""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services.config import config

router = APIRouter(prefix="/api/notes", tags=["notes"])

NAME_RE = re.compile(r"^[A-Za-z0-9 _.\-()]{1,80}$")
MAX_NOTE_BYTES = 2 * 1024 * 1024


def _dir() -> Path:
    return config.notes_dir()


def _safe_name(name: str) -> str:
    name = name.strip()
    stem = name[:-3] if name.lower().endswith(".md") else name
    if not NAME_RE.match(stem) or stem in (".", ".."):
        raise HTTPException(status_code=400, detail="Invalid note name")
    return f"{stem}.md"


def _path(name: str) -> Path:
    folder = _dir()
    target = (folder / _safe_name(name)).resolve()
    if folder.resolve() not in target.parents:
        raise HTTPException(status_code=400, detail="Invalid note path")
    return target


class NoteBody(BaseModel):
    content: str = Field(default="", max_length=MAX_NOTE_BYTES)


class CreateNote(BaseModel):
    name: str = Field(default="", max_length=80)
    content: str = Field(default="", max_length=MAX_NOTE_BYTES)


class RenameNote(BaseModel):
    new_name: str = Field(min_length=1, max_length=80)


@router.get("")
def list_notes() -> dict[str, Any]:
    notes = []
    for entry in sorted(_dir().glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            stat = entry.stat()
            text = entry.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        first_line = next((line.strip("# ").strip() for line in text.splitlines() if line.strip()), "")
        notes.append(
            {
                "name": entry.name,
                "title": first_line[:80] or entry.stem,
                "size": stat.st_size,
                "modified": stat.st_mtime,
                "preview": text[:200],
            }
        )
    return {"notes": notes, "dir": str(_dir())}


@router.get("/{name}")
def read_note(name: str) -> dict[str, Any]:
    path = _path(name)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Note not found")
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"name": path.name, "content": content, "modified": path.stat().st_mtime}


@router.put("/{name}")
def save_note(name: str, body: NoteBody) -> dict[str, Any]:
    path = _path(name)
    payload = body.content.encode("utf-8")
    if len(payload) > MAX_NOTE_BYTES:
        raise HTTPException(status_code=413, detail="Note is too large")
    try:
        path.write_bytes(payload)
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"Could not save note: {exc}") from exc
    return {"ok": True, "name": path.name, "modified": time.time(), "size": len(payload)}


@router.post("")
def create_note(body: CreateNote) -> dict[str, Any]:
    stem = body.name.strip() or time.strftime("Note %Y-%m-%d %H-%M")
    path = _path(stem)
    if path.exists():
        raise HTTPException(status_code=409, detail="A note with that name already exists")
    try:
        path.write_text(body.content, encoding="utf-8")
    except OSError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "name": path.name, "content": body.content, "modified": time.time()}


@router.post("/{name}/rename")
def rename_note(name: str, body: RenameNote) -> dict[str, Any]:
    source = _path(name)
    target = _path(body.new_name)
    if not source.exists():
        raise HTTPException(status_code=404, detail="Note not found")
    if target.exists() and target != source:
        raise HTTPException(status_code=409, detail="A note with that name already exists")
    try:
        source.rename(target)
    except OSError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "name": target.name}


@router.delete("/{name}")
def delete_note(name: str) -> dict[str, Any]:
    path = _path(name)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Note not found")
    try:
        path.unlink()
    except OSError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}
