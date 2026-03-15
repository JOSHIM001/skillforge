"""
websocket/ws_router.py — WebSocket endpoint for real-time room collaboration.

Endpoint: WS /ws/room/{room_id}?token=<jwt>

Auth: JWT passed as query param (browsers can't set headers on WebSocket connections).
The token is validated on connect — invalid tokens get a close code 4001.

Message protocol (JSON envelope with `type` discriminator):

  Client → Server:
    {"type": "chat",  "text": "Hello!"}
    {"type": "ping"}

  Server → Client:
    {"type": "room_update", "payload": RoomStateOut}
    {"type": "chat",  "user_id": "...", "username": "...", "text": "..."}
    {"type": "join",  "user_id": "...", "username": "..."}
    {"type": "leave", "user_id": "...", "username": "..."}
    {"type": "pong"}
    {"type": "error", "detail": "..."}
"""

import json
from typing import Annotated

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect, status
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db, AsyncSessionLocal
from services.auth_service import decode_access_token, get_user_by_id
from services.room_service import build_room_state, get_room, join_room
from websocket.manager import manager

router = APIRouter()

# Maximum chat message length accepted over WebSocket
_MAX_CHAT_LEN = 1000


async def _authenticate_ws(token: str | None) -> str | None:
    """
    Decode the JWT query param. Returns user_id string or None on failure.
    We don't use the FastAPI dependency system here because WebSocket
    dependencies that raise exceptions close the socket before accepting,
    which gives the client no useful error code.
    """
    if not token:
        return None
    try:
        user_id = decode_access_token(token)
        return str(user_id)
    except JWTError:
        return None


@router.websocket("/ws/room/{room_id}")
async def websocket_room(
    websocket: WebSocket,
    room_id: str,
    token: Annotated[str | None, Query()] = None,
) -> None:
    """
    WebSocket endpoint for a collaboration room.

    Flow:
      1. Validate JWT → close 4001 if invalid
      2. Load user + room from DB → close 4004 if not found
      3. Auto-join the room (idempotent)
      4. Register connection in RoomManager
      5. Broadcast join event + full room state to all members
      6. Message loop: route incoming messages
      7. On disconnect: broadcast leave event, clean up
    """
    # ── 1. Auth ───────────────────────────────────────────────
    user_id = await _authenticate_ws(token)
    if not user_id:
        await websocket.close(code=4001, reason="Invalid or missing token")
        return

    # ── 2. Load user + room ───────────────────────────────────
    async with AsyncSessionLocal() as db:
        user = await get_user_by_id(user_id, db)  # type: ignore[arg-type]
        if not user:
            await websocket.close(code=4001, reason="User not found")
            return

        room = await get_room(room_id, db)
        if not room:
            await websocket.close(code=4004, reason="Room not found")
            return

        # ── 3. Auto-join ──────────────────────────────────────
        try:
            await join_room(room_id, user.id, db)
            await db.commit()
        except Exception:
            await db.rollback()

    # ── 4. Register connection ────────────────────────────────
    await manager.connect(room_id, user_id, websocket)

    # ── 5. Broadcast join + room state ────────────────────────
    await manager.broadcast(room_id, {
        "type": "join",
        "user_id": user_id,
        "username": user.username,
    })

    # Send full room state to ALL members so their UI updates
    await _broadcast_room_state(room_id)

    # ── 6. Message loop ───────────────────────────────────────
    try:
        while True:
            raw = await websocket.receive_text()

            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json({
                    "type": "error",
                    "detail": "Invalid JSON",
                })
                continue

            msg_type = msg.get("type", "")

            if msg_type == "chat":
                text = str(msg.get("text", "")).strip()
                if not text:
                    continue
                if len(text) > _MAX_CHAT_LEN:
                    await websocket.send_json({
                        "type": "error",
                        "detail": f"Message too long (max {_MAX_CHAT_LEN} chars)",
                    })
                    continue
                # Broadcast chat to all room members
                await manager.broadcast(room_id, {
                    "type": "chat",
                    "user_id": user_id,
                    "username": user.username,
                    "text": text,
                })

            elif msg_type == "ping":
                # Respond directly to this client only — don't broadcast pings
                await websocket.send_json({"type": "pong"})

            elif msg_type == "request_state":
                # Client explicitly requests a fresh room state broadcast
                await _broadcast_room_state(room_id)

            else:
                await websocket.send_json({
                    "type": "error",
                    "detail": f"Unknown message type: {msg_type!r}",
                })

    # ── 7. Disconnect ─────────────────────────────────────────
    except WebSocketDisconnect:
        pass  # Normal closure — no error logging needed
    except Exception as exc:
        # Unexpected error — log it but don't crash the worker
        from loguru import logger
        logger.exception("WebSocket handler error", room_id=room_id, user_id=user_id, error=str(exc))
    finally:
        await manager.disconnect(room_id, user_id)

        # Broadcast leave event so other clients update their presence UI
        await manager.broadcast(room_id, {
            "type": "leave",
            "user_id": user_id,
            "username": user.username,
        })

        # Broadcast updated room state (member count changed)
        await _broadcast_room_state(room_id)


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _broadcast_room_state(room_id: str) -> None:
    """
    Load current room state from DB and broadcast to all room members.
    Uses AI gap analysis with Redis caching — not blocking.
    """
    try:
        online = manager.online_users(room_id)
        async with AsyncSessionLocal() as db:
            room = await get_room(room_id, db)
            if not room:
                return
            state = await build_room_state(
                room=room,
                db=db,
                online_user_ids=online,
                use_ai_analysis=True,
            )

        await manager.broadcast(room_id, {
            "type": "room_update",
            "payload": state.model_dump(mode="json"),
        })
    except Exception as exc:
        from loguru import logger
        logger.error("Failed to broadcast room state", room_id=room_id, error=str(exc))