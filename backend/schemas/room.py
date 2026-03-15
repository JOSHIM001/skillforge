"""
schemas/room.py — Request/response schemas for /rooms/* and WebSocket messages.

WebSocket message envelopes use a discriminated union on the `type` field
so the frontend can switch on message type without checking raw strings.
"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


# ── REST: Room management ─────────────────────────────────────────────────────

class CreateRoomRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)


class RoomMemberOut(BaseModel):
    user_id: uuid.UUID
    username: str
    avatar_url: str | None
    # Skill snapshot — top skill and overall score for the member card
    top_skill: str | None
    overall_score: float
    # Live presence is tracked in-memory, not from DB
    is_online: bool = False

    model_config = {"from_attributes": True}


class RoomOut(BaseModel):
    room_id: str
    name: str
    created_by: uuid.UUID | None
    created_at: datetime
    member_count: int
    members: list[RoomMemberOut]


class RoomStateOut(BaseModel):
    """Full room state — returned on GET /rooms/{id} and broadcast on join/leave."""
    room_id: str
    name: str
    members: list[RoomMemberOut]
    # Aggregated team skill matrix: {skill_name: average_score}
    team_matrix: dict[str, float]
    # AI-generated role gap analysis
    role_gaps: list["RoleGapOut"]
    online_count: int


class RoleGapOut(BaseModel):
    role: str
    required_skills: list[str]
    covered: bool
    # Which member covers this role (None if gap)
    covered_by: str | None = None
    coverage_score: float  # 0.0 → 1.0


# ── WebSocket message envelopes ───────────────────────────────────────────────
# All WS messages share a `type` discriminator.
# The frontend switches on `data.type`.

class WsJoinMessage(BaseModel):
    type: Literal["join"] = "join"
    user_id: str
    username: str


class WsLeaveMessage(BaseModel):
    type: Literal["leave"] = "leave"
    user_id: str
    username: str


class WsChatMessage(BaseModel):
    type: Literal["chat"] = "chat"
    user_id: str
    username: str
    text: str = Field(..., min_length=1, max_length=1000)


class WsRoomUpdateMessage(BaseModel):
    """Broadcast to all room members when state changes."""
    type: Literal["room_update"] = "room_update"
    payload: RoomStateOut


class WsPingMessage(BaseModel):
    type: Literal["ping"] = "ping"


class WsPongMessage(BaseModel):
    type: Literal["pong"] = "pong"


class WsErrorMessage(BaseModel):
    type: Literal["error"] = "error"
    detail: str


# ── Team analysis ─────────────────────────────────────────────────────────────

class TeamSkillSummary(BaseModel):
    """Input to the AI team gap analyser."""
    members: dict[str, dict[str, float]]   # {username: {skill: score}}
    team_matrix: dict[str, float]           # aggregated averages


# Forward reference resolution
RoomStateOut.model_rebuild()