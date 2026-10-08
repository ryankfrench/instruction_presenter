"""Read models for the staff index and the per-session status page."""

from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlencode

from django.urls import reverse

from instruction_presenter.manifest import (
    InstructionManifest,
    get_cached_manifest,
    get_subject_completion_url,
    media_kind_for_filename,
)
from instruction_presenter.session_state import (
    get_current_page_sync,
    list_subject_groups,
    subject_connection_count,
)


def format_timestamp(timestamp: float) -> str:
    moment = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    return moment.strftime('%Y-%m-%d %H:%M:%S UTC')


def subject_pattern_path(session_id: str, manifest: InstructionManifest) -> str:
    path = f'/instructions/{session_id}/subject/{{player_key}}/'
    if manifest.complete_url:
        return f'{path}?{urlencode([("complete_url", manifest.complete_url)])}'
    return path


def subject_join_path(session_id: str, manifest: InstructionManifest) -> str:
    """Shared subject link. Each visit is assigned a new player key."""
    path = reverse('instruction_presenter:subject_join', args=[session_id])
    if manifest.complete_url:
        return f'{path}?{urlencode([("complete_url", manifest.complete_url)])}'
    return path


def subject_instructions_path(session_id: str, player_key: str, complete_url: str) -> str:
    """Subject page for one player, including a completion overlay when one applies."""
    path = reverse(
        'instruction_presenter:subject_home',
        args=[session_id, player_key],
    )
    if complete_url:
        return f'{path}?{urlencode([("complete_url", complete_url)])}'
    return path


def index_rows() -> list[dict]:
    """Saved instruction sessions, newest first."""
    from instruction_presenter.models import InstructionSession

    rows = []
    for session in InstructionSession.objects.order_by('-created_at'):
        manifest = get_cached_manifest(str(session.id))
        if manifest is None or manifest.total_pages == 0:
            continue
        session_id = str(session.id)
        registered_at = session.created_at.timestamp()
        page = min(max(session.current_page, 1), manifest.total_pages)
        rows.append(
            {
                'session_id': session_id,
                'registered_at': registered_at,
                'created_label': format_timestamp(registered_at),
                'current_page': page,
                'total_pages': manifest.total_pages,
                'subject_count': subject_connection_count(session_id),
                'presenter_path': reverse(
                    'instruction_presenter:staff_home', args=[session_id]
                ),
                'status_path': reverse(
                    'instruction_presenter:staff_status', args=[session_id]
                ),
                'subject_pattern_path': subject_pattern_path(session_id, manifest),
                'subject_join_path': subject_join_path(session_id, manifest),
                'base_url': manifest.base_url,
            }
        )
    return rows


def build_status_payload(session_id: str) -> dict | None:
    manifest = get_cached_manifest(session_id)
    if manifest is None or manifest.total_pages == 0:
        return None
    page = min(max(get_current_page_sync(session_id), 1), manifest.total_pages)
    subjects = []
    for row in list_subject_groups(session_id):
        completion = get_subject_completion_url(session_id, row['player_key']) or ''
        subjects.append(
            {
                'player_key': row['player_key'],
                'connection_count': row['connection_count'],
                'connected_label': format_timestamp(row['connected_at']),
                'completion_url': completion,
                'instructions_url': subject_instructions_path(
                    session_id,
                    row['player_key'],
                    completion or manifest.complete_url,
                ),
            }
        )
    current_file = manifest.files[page - 1]
    return {
        'session_id': session_id,
        'current_page': page,
        'total_pages': manifest.total_pages,
        'current_file': current_file,
        'base_url': manifest.base_url,
        'files': list(manifest.files),
        'has_video': any(media_kind_for_filename(name) == 'video' for name in manifest.files),
        'complete_url': manifest.complete_url,
        'staff_complete_url': manifest.staff_complete_url or '',
        'subject_join_path': subject_join_path(session_id, manifest),
        'subjects': subjects,
    }
