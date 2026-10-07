"""Ephemeral per-session page index, session registry, and subject presence.

All of this lives in-process and resets when the worker restarts.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from dataclasses import dataclass


_lock = threading.Lock()
_pages: dict[str, int] = {}
_sessions: dict[str, float] = {}
_subjects: dict[str, dict[str, 'SubjectLink']] = defaultdict(dict)


def _key(session_id: str) -> str:
    return str(session_id)


@dataclass(frozen=True)
class SubjectLink:
    player_key: str
    connected_at: float


def register_session(session_id: str) -> None:
    """Remember a session the first time its manifest is cached."""
    sid = _key(session_id)
    with _lock:
        _sessions.setdefault(sid, time.time())


def unregister_session(session_id: str) -> None:
    """Drop a session and any subject presence after the manifest is cleared."""
    sid = _key(session_id)
    with _lock:
        _sessions.pop(sid, None)
        _subjects.pop(sid, None)


def registered_sessions() -> list[tuple[str, float]]:
    """Return (session_id, registered_at) pairs, oldest first."""
    with _lock:
        return sorted(_sessions.items(), key=lambda item: item[1])


async def get_current_page(session_id: str) -> int:
    with _lock:
        return _pages.get(_key(session_id), 1)


async def set_current_page(session_id: str, page: int) -> None:
    with _lock:
        _pages[_key(session_id)] = page


async def adjust_page(session_id: str, delta: int, total_pages: int) -> tuple[int, bool]:
    """Apply delta and clamp to [1, total_pages]. Returns (new_page, changed)."""
    with _lock:
        sid = _key(session_id)
        current = _pages.get(sid, 1)
        new_page = max(1, min(total_pages, current + delta))
        changed = new_page != current
        _pages[sid] = new_page
        return new_page, changed


async def reset_page(session_id: str) -> None:
    with _lock:
        _pages[_key(session_id)] = 1


def get_current_page_sync(session_id: str) -> int:
    """Read last known page for HTTP views (best-effort, same worker only)."""
    with _lock:
        return _pages.get(_key(session_id), 1)


def register_subject(session_id: str, player_key: str, channel_name: str) -> None:
    with _lock:
        _subjects[_key(session_id)][channel_name] = SubjectLink(
            player_key=str(player_key),
            connected_at=time.time(),
        )


def unregister_subject(session_id: str, channel_name: str) -> None:
    with _lock:
        links = _subjects.get(_key(session_id))
        if not links:
            return
        links.pop(channel_name, None)
        if not links:
            _subjects.pop(_key(session_id), None)


def subject_connection_count(session_id: str) -> int:
    with _lock:
        return len(_subjects.get(_key(session_id), {}))


def list_subject_groups(session_id: str) -> list[dict]:
    """One row per player key. connection_count covers multiple tabs with the same key."""
    with _lock:
        links = list(_subjects.get(_key(session_id), {}).values())
    grouped: dict[str, list[SubjectLink]] = {}
    for link in links:
        grouped.setdefault(link.player_key, []).append(link)
    rows = []
    for player_key, items in grouped.items():
        rows.append(
            {
                'player_key': player_key,
                'connection_count': len(items),
                'connected_at': min(item.connected_at for item in items),
            }
        )
    rows.sort(key=lambda row: row['connected_at'])
    return rows


def clear_ephemeral_state() -> None:
    """Reset in-process session data. Used by tests."""
    with _lock:
        _pages.clear()
        _sessions.clear()
        _subjects.clear()
