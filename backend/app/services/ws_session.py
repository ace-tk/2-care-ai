"""
Per-session WebSocket turn control: barge-in cancellation and stale response guard.
"""

import asyncio
import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)

_active_tasks: Dict[str, asyncio.Task] = {}
_turn_generation: Dict[str, int] = {}
_barge_in_flags: Dict[str, bool] = {}


def get_generation(session_id: str) -> int:
    return _turn_generation.get(session_id, 0)


def bump_generation(session_id: str) -> int:
    _turn_generation[session_id] = _turn_generation.get(session_id, 0) + 1
    return _turn_generation[session_id]


def is_stale(session_id: str, generation: int) -> bool:
    return generation != get_generation(session_id)


async def cancel_active_turn(session_id: str, *, reason: str = "barge_in") -> int:
    """Cancel in-flight pipeline task; return new generation id."""
    task = _active_tasks.pop(session_id, None)
    if task and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.warning("[WS] Turn cancel await: %s", exc)
    gen = bump_generation(session_id)
    _barge_in_flags[session_id] = reason == "barge_in"
    logger.info("[BARGE IN] session=%s | generation=%s | reason=%s", session_id, gen, reason)
    return gen


def register_turn_task(session_id: str, task: asyncio.Task) -> None:
    _active_tasks[session_id] = task


def clear_turn_task(session_id: str, task: asyncio.Task) -> None:
    if _active_tasks.get(session_id) is task:
        _active_tasks.pop(session_id, None)


def consume_barge_in(session_id: str) -> bool:
    if _barge_in_flags.pop(session_id, False):
        return True
    return False


def cleanup_session(session_id: str) -> None:
    _active_tasks.pop(session_id, None)
    _turn_generation.pop(session_id, None)
    _barge_in_flags.pop(session_id, None)
