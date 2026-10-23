"""Terminal websocket endpoints.

Protocol (per session):

* server → client: binary frames containing raw PTY output
* client → server: binary frames are written to the shell as-is;
  text frames are JSON commands: ``{"type": "input"|"resize"|"ping", ...}``
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from services.platform_utils import expanded
from services.terminal_manager import available_shells, terminal_manager

router = APIRouter(prefix="/api/terminal", tags=["terminal"])


class NewSession(BaseModel):
    shell: str | None = None
    cwd: str | None = None
    cols: int = Field(default=120, ge=20, le=500)
    rows: int = Field(default=32, ge=5, le=200)


class ResizeRequest(BaseModel):
    cols: int = Field(ge=20, le=500)
    rows: int = Field(ge=5, le=200)


@router.get("/shells")
def shells() -> dict[str, Any]:
    return available_shells()


@router.get("/sessions")
def sessions() -> dict[str, Any]:
    return {"sessions": terminal_manager.list()}


@router.post("/sessions")
def create_session(body: NewSession) -> dict[str, Any]:
    cwd = None
    if body.cwd:
        candidate = Path(expanded(body.cwd))
        if not candidate.is_dir():
            raise HTTPException(status_code=400, detail="Working directory does not exist")
        cwd = str(candidate)
    try:
        session = terminal_manager.create(body.shell, cwd=cwd, cols=body.cols, rows=body.rows)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return session.info()


@router.delete("/sessions/{session_id}")
def close_session(session_id: str) -> dict[str, Any]:
    if not terminal_manager.close(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    return {"ok": True}


@router.post("/sessions/{session_id}/resize")
def resize_session(session_id: str, body: ResizeRequest) -> dict[str, Any]:
    session = terminal_manager.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    session.resize(body.cols, body.rows)
    return {"ok": True}


@router.get("/history")
def history() -> dict[str, Any]:
    return {"history": terminal_manager.history()}


@router.websocket("/ws/{session_id}")
async def terminal_ws(ws: WebSocket, session_id: str) -> None:
    from services.security import websocket_origin_ok

    if not websocket_origin_ok(ws):
        await ws.close(code=4403, reason="Origin not allowed")
        return

    session = terminal_manager.get(session_id)
    if not session:
        await ws.close(code=4404, reason="Unknown session")
        return

    await ws.accept()
    loop = asyncio.get_running_loop()
    queue = session.subscribe(loop)

    # Replay recent output so a reconnected tab restores context.
    try:
        scrollback = session.scrollback()
        if scrollback:
            await ws.send_bytes(scrollback)
    except RuntimeError:
        pass

    async def sender() -> None:
        while True:
            data = await queue.get()
            if data == b"":
                break
            try:
                await ws.send_bytes(data)
            except RuntimeError:
                break

    async def receiver() -> None:
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                return
            if (payload := message.get("bytes")) is not None:
                session.write(payload.decode("utf-8", errors="replace"))
                continue
            text = message.get("text")
            if not text:
                continue
            try:
                command = json.loads(text)
            except ValueError:
                session.write(text)
                continue
            kind = command.get("type")
            if kind == "input":
                session.write(str(command.get("data", "")))
            elif kind == "resize":
                try:
                    session.resize(int(command.get("cols", session.cols)),
                                   int(command.get("rows", session.rows)))
                except (TypeError, ValueError):
                    pass
            elif kind == "ping":
                await ws.send_json({"type": "pong"})

    sender_task = asyncio.create_task(sender())
    receiver_task = asyncio.create_task(receiver())
    try:
        done, pending = await asyncio.wait(
            {sender_task, receiver_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        # Surface task exceptions here instead of "never retrieved" warnings.
        await asyncio.gather(sender_task, receiver_task, return_exceptions=True)
    except WebSocketDisconnect:
        pass
    finally:
        session.unsubscribe(loop, queue)
        for task in (sender_task, receiver_task):
            task.cancel()
        try:
            await ws.close()
        except (WebSocketDisconnect, RuntimeError):
            # The client is already gone; nothing to close.
            pass
