"""
routers/rooms.py — REST endpoints for room lifecycle management.

  POST /rooms              → create room, returns room_id + initial state
  GET  /rooms/{id}         → get room state (members, team matrix, role gaps)
  POST /rooms/{id}/join    → join a room via REST (WS auto-joins too)
  POST /rooms/{id}/leave   → leave a room
  GET  /rooms/{id}/members → list members with skill snapshots
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from middleware.auth_middleware import CurrentUser
from schemas.room import CreateRoomRequest, RoomOut, RoomStateOut
from services.room_service import (
    build_room_state,
    create_room,
    get_room,
    join_room,
    leave_room,
)
from websocket.manager import manager

router = APIRouter()


@router.post(
    "",
    response_model=RoomOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new collaboration room",
)
async def create_collab_room(
    body: CreateRoomRequest,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> RoomOut:
    room = await create_room(
        name=body.name,
        created_by=current_user.id,
        db=db,
    )
    # Load member snapshots (just the creator at this point)
    online = manager.online_users(room.id)
    state = await build_room_state(
        room=room,
        db=db,
        online_user_ids=online,
        use_ai_analysis=False,  # No point running AI gap analysis for 1 person
    )
    return RoomOut(
        room_id=room.id,
        name=room.name,
        created_by=room.created_by,
        created_at=room.created_at,
        member_count=len(state.members),
        members=state.members,
    )


@router.get(
    "/{room_id}",
    response_model=RoomStateOut,
    summary="Get room state — members, team skill matrix, role gap analysis",
)
async def get_room_state(
    room_id: str,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> RoomStateOut:
    room = await get_room(room_id, db)
    if not room:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Room '{room_id}' not found")

    online = manager.online_users(room_id)
    return await build_room_state(
        room=room,
        db=db,
        online_user_ids=online,
        use_ai_analysis=True,
    )


@router.post(
    "/{room_id}/join",
    response_model=RoomStateOut,
    summary="Join a room by code (REST — WebSocket auto-joins on connect too)",
)
async def join_collab_room(
    room_id: str,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> RoomStateOut:
    try:
        room = await join_room(room_id, current_user.id, db)
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))

    online = manager.online_users(room_id)
    state = await build_room_state(room=room, db=db, online_user_ids=online)

    # Notify existing WebSocket members that someone joined via REST
    await manager.broadcast(room_id, {
        "type": "join",
        "user_id": str(current_user.id),
        "username": current_user.username,
    })
    await manager.broadcast(room_id, {
        "type": "room_update",
        "payload": state.model_dump(mode="json"),
    })

    return state


@router.post(
    "/{room_id}/leave",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Leave a room",
)
async def leave_collab_room(
    room_id: str,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> None:
    room = await get_room(room_id, db)
    if not room:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Room '{room_id}' not found")

    await leave_room(room_id, current_user.id, db)

    # Notify WebSocket members
    await manager.broadcast(room_id, {
        "type": "leave",
        "user_id": str(current_user.id),
        "username": current_user.username,
    })

    # Broadcast updated state (member count changed)
    online = manager.online_users(room_id)
    state = await build_room_state(room=room, db=db, online_user_ids=online)
    await manager.broadcast(room_id, {
        "type": "room_update",
        "payload": state.model_dump(mode="json"),
    })