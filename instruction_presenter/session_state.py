"""Ephemeral per-session page index, media clock, session registry, and subject presence.

All of this lives in-process and resets when the worker restarts.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from dataclasses import dataclass


# Clients wait this long after server_time so a broadcast can reach them before start.
PLAY_LEAD_SECONDS = 0.4

_lock = threading.Lock()
_pages: dict[str, int] = {}
_clocks: dict[str, 'MediaClock'] = {}
_sessions: dict[str, float] = {}
_subjects: dict[str, dict[str, 'SubjectLink']] = defaultdict(dict)


def _key(session_id: str) -> str:
    return str(session_id)


@dataclass(frozen=True)
class SubjectLink:
    player_key: str
    connected_at: float


@dataclass(frozen=True)
class MediaClock:
    """Media position at anchor_server_time. Playing clocks advance from that anchor."""

    playing: bool
    position: float
    anchor_server_time: float


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
        _clocks.pop(sid, None)


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
        if changed:
            _clocks.pop(sid, None)
        return new_page, changed


async def reset_page(session_id: str) -> None:
    with _lock:
        sid = _key(session_id)
        _pages[sid] = 1
        _clocks.pop(sid, None)


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


def _media_position_at(clock: MediaClock, moment: float) -> float:
    if clock.playing:
        return max(0.0, clock.position + (moment - clock.anchor_server_time))
    return max(0.0, clock.position)


def clear_media_clock(session_id: str) -> None:
    with _lock:
        _clocks.pop(_key(session_id), None)


def get_media_clock(session_id: str) -> MediaClock | None:
    with _lock:
        return _clocks.get(_key(session_id))


def begin_playback(session_id: str, now: float | None = None) -> dict:
    """Arm a shared play. Position is the media time clients should show at play_at."""
    moment = time.time() if now is None else now
    play_at = moment + PLAY_LEAD_SECONDS
    sid = _key(session_id)
    with _lock:
        clock = _clocks.get(sid)
        if clock is None:
            position = 0.0
        elif clock.playing:
            position = _media_position_at(clock, play_at)
        else:
            position = max(0.0, clock.position)
        _clocks[sid] = MediaClock(
            playing=True,
            position=position,
            anchor_server_time=play_at,
        )
    return {
        'type': 'media_command',
        'command': 'play',
        'position': position,
        'server_time': moment,
        'play_at': play_at,
    }


def pause_playback(session_id: str, now: float | None = None) -> dict:
    """Stop the shared clock and report the media time at this instant."""
    moment = time.time() if now is None else now
    sid = _key(session_id)
    with _lock:
        clock = _clocks.get(sid)
        position = 0.0 if clock is None else _media_position_at(clock, moment)
        _clocks[sid] = MediaClock(
            playing=False,
            position=position,
            anchor_server_time=moment,
        )
    return {
        'type': 'media_command',
        'command': 'pause',
        'position': position,
        'server_time': moment,
    }


def media_command_for_join(session_id: str, now: float | None = None) -> dict | None:
    """Snapshot for a client that connects while a video page has transport state.

    Playing joins target the media time at a fresh play_at so they meet the session.
    A pause at the start has no snapshot. A later pause seeks the joiner to that frame.
    """
    moment = time.time() if now is None else now
    with _lock:
        clock = _clocks.get(_key(session_id))
        if clock is None:
            return None
        if clock.playing:
            play_at = moment + PLAY_LEAD_SECONDS
            return {
                'type': 'media_command',
                'command': 'play',
                'position': _media_position_at(clock, play_at),
                'server_time': moment,
                'play_at': play_at,
            }
        if clock.position <= 0:
            return None
        return {
            'type': 'media_command',
            'command': 'pause',
            'position': clock.position,
            'server_time': moment,
        }


def clear_ephemeral_state() -> None:
    """Reset in-process session data. Used by tests."""
    with _lock:
        _pages.clear()
        _clocks.clear()
        _sessions.clear()
        _subjects.clear()
