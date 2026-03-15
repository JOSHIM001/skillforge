"""
websocket/manager.py — RoomManager: Redis pub/sub + in-memory connection tracking.

Architecture:
  - Each FastAPI worker process holds a dict of active WebSocket connections
    for rooms that have at least one user on THAT worker.
  - When a message is broadcast, it's published to Redis channel `room:{room_id}`.
  - A per-room asyncio subscriber task forwards Redis messages to all local WebSockets.
  - This means the broadcast reaches users on ALL worker processes, not just
    the one that published — required for multi-worker deployments.

Connection lifecycle:
  connect()     → accept WS, register in self.connections, start Redis subscriber
  disconnect()  → remove from self.connections, unsubscribe if room is now empty
  broadcast()   → publish JSON to Redis channel
  _subscribe()  → background task: reads Redis, fans out to local WebSockets

Presence tracking:
  - self.connections[room_id] = {user_id: WebSocket}
  - online_users(room_id) → set[str] of user_ids online in that room
    This is LOCAL presence only — only users on THIS worker.
    For global presence across workers, publish presence events via Redis.
"""

import asyncio
import json
import logging
from typing import Any

from fastapi import WebSocket
from loguru import logger

from redis_client import get_pubsub, redis


class RoomManager:
    def __init__(self):
        # connections[room_id][user_id] = WebSocket
        self._connections: dict[str, dict[str, WebSocket]] = {}
        # subscriber tasks — one per room per worker process
        self._subscriber_tasks: dict[str, asyncio.Task] = {}

    # ── Connection management ─────────────────────────────────────────────────

    async def connect(
        self,
        room_id: str,
        user_id: str,
        websocket: WebSocket,
    ) -> None:
        await websocket.accept()

        if room_id not in self._connections:
            self._connections[room_id] = {}
            # Start a Redis subscriber task for this room
            task = asyncio.create_task(
                self._subscribe(room_id),
                name=f"ws-subscriber-{room_id}",
            )
            self._subscriber_tasks[room_id] = task

        self._connections[room_id][user_id] = websocket
        logger.debug("WebSocket connected", room_id=room_id, user_id=user_id)

    async def disconnect(
        self,
        room_id: str,
        user_id: str,
    ) -> None:
        room_connections = self._connections.get(room_id, {})
        room_connections.pop(user_id, None)

        if not room_connections:
            # No more users in this room on this worker — clean up
            self._connections.pop(room_id, None)
            task = self._subscriber_tasks.pop(room_id, None)
            if task and not task.done():
                task.cancel()

        logger.debug("WebSocket disconnected", room_id=room_id, user_id=user_id)

    # ── Messaging ─────────────────────────────────────────────────────────────

    async def broadcast(self, room_id: str, message: dict[str, Any]) -> None:
        """
        Publish a message to ALL workers via Redis pub/sub.
        The _subscribe task on each worker will forward it to local WebSockets.
        """
        await redis.publish(f"room:{room_id}", json.dumps(message))

    async def send_personal(
        self,
        room_id: str,
        user_id: str,
        message: dict[str, Any],
    ) -> None:
        """Send a message to one specific user if they are on this worker."""
        ws = self._connections.get(room_id, {}).get(user_id)
        if ws:
            try:
                await ws.send_json(message)
            except Exception:
                await self.disconnect(room_id, user_id)

    # ── Presence ──────────────────────────────────────────────────────────────

    def online_users(self, room_id: str) -> set[str]:
        """Return user_ids currently connected on THIS worker for a room."""
        return set(self._connections.get(room_id, {}).keys())

    def is_online(self, room_id: str, user_id: str) -> bool:
        return user_id in self._connections.get(room_id, {})

    def connection_count(self, room_id: str) -> int:
        return len(self._connections.get(room_id, {}))

    # ── Redis subscriber ──────────────────────────────────────────────────────

    async def _subscribe(self, room_id: str) -> None:
        """
        Long-running background task per room.
        Listens on Redis channel `room:{room_id}` and forwards
        every message to all locally connected WebSockets.

        Handles:
          - JSON parse errors (log and skip)
          - Individual WebSocket send failures (disconnect that client)
          - Redis disconnection (exit loop — task will be restarted on next connect)
        """
        pubsub = get_pubsub()
        channel = f"room:{room_id}"

        try:
            await pubsub.subscribe(channel)
            logger.debug("Redis subscriber started", channel=channel)

            async for raw_msg in pubsub.listen():
                if raw_msg["type"] != "message":
                    continue

                try:
                    data = json.loads(raw_msg["data"])
                except (json.JSONDecodeError, TypeError) as exc:
                    logger.warning("Invalid JSON in Redis channel", channel=channel, error=str(exc))
                    continue

                # Fan out to all local WebSocket connections for this room
                dead_connections: list[str] = []
                for uid, ws in list(self._connections.get(room_id, {}).items()):
                    try:
                        await ws.send_json(data)
                    except Exception:
                        dead_connections.append(uid)

                # Clean up any WebSockets that errored during send
                for uid in dead_connections:
                    await self.disconnect(room_id, uid)

        except asyncio.CancelledError:
            logger.debug("Redis subscriber cancelled", channel=channel)
        except Exception as exc:
            logger.error("Redis subscriber crashed", channel=channel, error=str(exc))
        finally:
            try:
                await pubsub.unsubscribe(channel)
                await pubsub.aclose()
            except Exception:
                pass


# ── Singleton ─────────────────────────────────────────────────────────────────
# One RoomManager per worker process. Import this everywhere.
manager = RoomManager()