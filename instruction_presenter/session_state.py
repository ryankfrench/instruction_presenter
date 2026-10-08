"""Per-session page index and media clock in the database, plus live subject presence.

Page, playhead, and the session row survive a process restart. Subject presence
is connection-scoped and stays in this process.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone


# Clients wait this long after server_time so a broadcast can reach them before start.
PLAY_LEAD_SECONDS = 0.4

_lock = threading.Lock()
_subjects: dict[str, dict[str, 'SubjectLink']] = defaultdict(dict)


def _key(session_id: str) -> str:
    return str(session_id)


def _get_session(session_id: str):
    from instruction_presenter.models import InstructionSession

    try:
        return InstructionSession.objects.get(pk=_key(session_id))
    except (InstructionSession.DoesNotExist, ValidationError, ValueError):
        return None


def _clock_from_session(session) -> MediaClock | None:
    if session.media_anchor is None:
        return None
    return MediaClock(
        playing=session.media_playing,
        position=session.media_position,
        anchor_server_time=session.media_anchor,
    )


def _write_clock(session, clock: MediaClock | None) -> None:
    if clock is None:
        session.media_playing = False
        session.media_position = 0
        session.media_anchor = None
    else:
        session.media_playing = clock.playing
        session.media_position = clock.position
        session.media_anchor = clock.anchor_server_time
    session.save(
        update_fields=[
            'media_playing',
            'media_position',
            'media_anchor',
            'updated_at',
        ]
    )


@dataclass(frozen=True)
class SubjectLink:
    player_key: str
    connected_at: float
    video_enabled: bool = False


@dataclass(frozen=True)
class MediaClock:
    """Media position at anchor_server_time. Playing clocks advance from that anchor."""

    playing: bool
    position: float
    anchor_server_time: float


def registered_sessions() -> list[tuple[str, float]]:
    """Return (session_id, created_at) pairs, oldest first."""
    from instruction_presenter.models import InstructionSession

    return [
        (str(session.id), session.created_at.timestamp())
        for session in InstructionSession.objects.order_by('created_at')
    ]


def reset_session_for_reuse(session_id: str) -> None:
    """Return a finished session to page 1 so the same presenter URL can run again."""
    from instruction_presenter.models import InstructionSession, SubjectCompletion

    sid = _key(session_id)
    with transaction.atomic():
        try:
            session = InstructionSession.objects.select_for_update().get(pk=sid)
        except (InstructionSession.DoesNotExist, ValidationError, ValueError):
            return
        session.current_page = 1
        session.media_playing = False
        session.media_position = 0
        session.media_anchor = None
        session.save(
            update_fields=[
                'current_page',
                'media_playing',
                'media_position',
                'media_anchor',
                'updated_at',
            ]
        )
        SubjectCompletion.objects.filter(session=session).delete()


def get_current_page(session_id: str) -> int:
    session = _get_session(session_id)
    if session is None:
        return 1
    return session.current_page


def set_current_page(session_id: str, page: int) -> None:
    from instruction_presenter.models import InstructionSession

    InstructionSession.objects.filter(pk=_key(session_id)).update(
        current_page=page,
        updated_at=timezone.now(),
    )


def adjust_page(session_id: str, delta: int, total_pages: int) -> tuple[int, bool]:
    """Apply delta and clamp to [1, total_pages]. Returns (new_page, changed)."""
    from instruction_presenter.models import InstructionSession

    with transaction.atomic():
        session = InstructionSession.objects.select_for_update().get(pk=_key(session_id))
        new_page = max(1, min(total_pages, session.current_page + delta))
        changed = new_page != session.current_page
        session.current_page = new_page
        update_fields = ['current_page', 'updated_at']
        if changed:
            _write_clock(session, None)
            update_fields.extend(['media_playing', 'media_position', 'media_anchor'])
        session.save(update_fields=update_fields)
        return new_page, changed


def reset_page(session_id: str) -> None:
    from instruction_presenter.models import InstructionSession

    with transaction.atomic():
        session = InstructionSession.objects.select_for_update().get(pk=_key(session_id))
        session.current_page = 1
        _write_clock(session, None)
        session.save(
            update_fields=[
                'current_page',
                'media_playing',
                'media_position',
                'media_anchor',
                'updated_at',
            ]
        )


def get_current_page_sync(session_id: str) -> int:
    """Read last known page for HTTP views."""
    return get_current_page(session_id)


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


def mark_subject_video_enabled(session_id: str, channel_name: str) -> bool:
    """Mark one subject screen ready to play. Returns False if that screen is gone."""
    with _lock:
        links = _subjects.get(_key(session_id))
        if not links or channel_name not in links:
            return False
        link = links[channel_name]
        links[channel_name] = SubjectLink(
            player_key=link.player_key,
            connected_at=link.connected_at,
            video_enabled=True,
        )
        return True


def video_ready_counts(session_id: str) -> tuple[int, int]:
    """Return (ready screens, connected subject screens)."""
    with _lock:
        links = list(_subjects.get(_key(session_id), {}).values())
    return sum(1 for link in links if link.video_enabled), len(links)


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
    session = _get_session(session_id)
    if session is None:
        return
    _write_clock(session, None)


def get_media_clock(session_id: str) -> MediaClock | None:
    session = _get_session(session_id)
    if session is None:
        return None
    return _clock_from_session(session)


def begin_playback(session_id: str, now: float | None = None) -> dict:
    """Arm a shared play. Position is the media time clients should show at play_at."""
    from instruction_presenter.models import InstructionSession

    moment = time.time() if now is None else now
    play_at = moment + PLAY_LEAD_SECONDS
    with transaction.atomic():
        session = InstructionSession.objects.select_for_update().get(pk=_key(session_id))
        clock = _clock_from_session(session)
        if clock is None:
            position = 0.0
        elif clock.playing:
            position = _media_position_at(clock, play_at)
        else:
            position = max(0.0, clock.position)
        _write_clock(
            session,
            MediaClock(
                playing=True,
                position=position,
                anchor_server_time=play_at,
            ),
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
    from instruction_presenter.models import InstructionSession

    moment = time.time() if now is None else now
    with transaction.atomic():
        session = InstructionSession.objects.select_for_update().get(pk=_key(session_id))
        clock = _clock_from_session(session)
        position = 0.0 if clock is None else _media_position_at(clock, moment)
        _write_clock(
            session,
            MediaClock(
                playing=False,
                position=position,
                anchor_server_time=moment,
            ),
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
    clock = get_media_clock(session_id)
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
    """Reset in-process subject presence. Used by tests."""
    with _lock:
        _subjects.clear()
