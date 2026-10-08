"""Instruction session manifest: file list, completion URLs, persistence helpers."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlsplit

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpRequest, QueryDict
from django.http.request import split_domain_port, validate_host
from django.utils.http import MAX_URL_LENGTH


def set_subject_completion_url(session_id: str, player_key: str, url: str) -> None:
    """Per-subject completion redirect; used when subjects open with complete_url overlay only."""
    from instruction_presenter.models import InstructionSession, SubjectCompletion

    try:
        session = InstructionSession.objects.get(pk=session_id)
    except (InstructionSession.DoesNotExist, ValidationError, ValueError):
        return
    SubjectCompletion.objects.update_or_create(
        session=session,
        player_key=player_key,
        defaults={'complete_url': url},
    )


def get_subject_completion_url(session_id: str, player_key: str) -> str | None:
    from instruction_presenter.models import SubjectCompletion

    try:
        row = SubjectCompletion.objects.get(session_id=session_id, player_key=player_key)
    except (SubjectCompletion.DoesNotExist, ValidationError, ValueError):
        return None
    return row.complete_url


def clear_subject_completion_url(session_id: str, player_key: str) -> None:
    from instruction_presenter.models import SubjectCompletion

    try:
        SubjectCompletion.objects.filter(
            session_id=session_id,
            player_key=player_key,
        ).delete()
    except (ValidationError, ValueError):
        return


def completion_urls_for_session(session_id: str) -> dict[str, str]:
    """player_key -> completion URL, snapshotted before a session is reset."""
    from instruction_presenter.models import SubjectCompletion

    try:
        rows = SubjectCompletion.objects.filter(session_id=session_id)
        return {str(row.player_key): row.complete_url for row in rows}
    except (ValidationError, ValueError):
        return {}


@dataclass(frozen=True)
class InstructionManifest:
    base_url: str
    files: tuple[str, ...]
    complete_url: str
    staff_complete_url: str | None

    @property
    def total_pages(self) -> int:
        return len(self.files)

    def file_url_for_page(self, page: int) -> str:
        """1-based page index into files."""
        if page < 1 or page > len(self.files):
            raise IndexError('page out of range')
        filename = self.files[page - 1]
        base = self.base_url if self.base_url.endswith('/') else self.base_url + '/'
        return urljoin(base, filename)

    def pdf_url_for_page(self, page: int) -> str:
        """1-based page index into files."""
        return self.file_url_for_page(page)

    def media_kind_for_page(self, page: int) -> str | None:
        """``pdf`` or ``video`` for a 1-based page, or None when the file type is unsupported."""
        if page < 1 or page > len(self.files):
            raise IndexError('page out of range')
        return media_kind_for_filename(self.files[page - 1])


def default_complete_url_from_base(base_url: str) -> str:
    """Subject completion default when complete_url is omitted: base + subject-home/ (like PDF paths)."""
    base = base_url if base_url.endswith('/') else base_url + '/'
    return urljoin(base, 'subject-home/')


def redirect_allowed_hosts() -> list[str]:
    hosts = getattr(settings, 'INSTRUCTION_REDIRECT_ALLOWED_HOSTS', None)
    if hosts is not None:
        return list(hosts)
    if settings.ALLOWED_HOSTS:
        return list(settings.ALLOWED_HOSTS)
    if settings.DEBUG:
        return ['localhost', '127.0.0.1', '[::1]']
    return []


def require_https_redirects() -> bool:
    return getattr(settings, 'INSTRUCTION_REDIRECT_REQUIRE_HTTPS', not settings.DEBUG)


def is_safe_redirect_url(url: str) -> bool:
    """
    Same as django.utils.http.url_has_allowed_host_and_scheme for open redirects,
    but validate the hostname with validate_host() so ports on complete_url do not
    have to be listed (netloc is e.g. localhost:8000; ALLOWED_HOSTS lists localhost).
    """
    if url is not None:
        url = url.strip()
    if not url:
        return False

    require_https = require_https_redirects()
    allowed = redirect_allowed_hosts()

    def _one(u: str) -> bool:
        if u.startswith('///') or len(u) > MAX_URL_LENGTH:
            return False
        try:
            url_info = urlsplit(u)
        except ValueError:
            return False
        if not url_info.netloc and url_info.scheme:
            return False
        if unicodedata.category(u[0])[0] == 'C':
            return False
        scheme = url_info.scheme
        if not url_info.scheme and url_info.netloc:
            scheme = 'http'
        valid = ['https'] if require_https else ['http', 'https']
        if scheme and scheme not in valid:
            return False
        if not url_info.netloc:
            return True
        domain, _port = split_domain_port(url_info.netloc)
        if not domain:
            return False
        return validate_host(domain, allowed)

    return _one(url) and _one(url.replace('\\', '/'))


# Canonical default for parameterized /demo/subject/ when complete_url is omitted.
DEMO_SUBJECT_DEFAULT_COMPLETE_URL = 'https://www.google.com/'


def subject_completion_overlay_allowed(url: str) -> bool:
    """Redirects from subject links: full validation, plus trusted demo fallback URL."""
    if url == DEMO_SUBJECT_DEFAULT_COMPLETE_URL:
        return True
    return is_safe_redirect_url(url)


def media_kind_for_filename(filename: str) -> str | None:
    """``pdf`` or ``video`` from the file suffix, or None when it is not a supported type."""
    name = (filename or '').strip()
    name = name.split('?', 1)[0].split('#', 1)[0]
    if '.' not in name:
        return None
    ext = name.rsplit('.', 1)[-1].lower()
    if ext == 'pdf':
        return 'pdf'
    if ext == 'mp4':
        return 'video'
    return None


def files_are_supported(files: tuple[str, ...] | list[str]) -> bool:
    return bool(files) and all(media_kind_for_filename(name) is not None for name in files)


def has_manifest_params(query: QueryDict) -> bool:
    """True when URL has base_url and at least one filename (f=). complete_url is optional on staff links."""
    return bool(query.get('base_url') and query.getlist('f'))


def _base_url_ok(base_url: str) -> bool:
    if base_url.startswith('//'):
        return False
    if base_url.startswith('/'):
        return True
    return base_url.startswith(('http://', 'https://'))


def build_manifest_from_query(
    query: QueryDict,
    *,
    allow_default_subject_complete_when_complete_missing: bool = True,
) -> InstructionManifest | None:
    """
    allow_default_subject_complete_when_complete_missing:
        When True (default), omitted complete_url is filled with base_url/subject-home/
        and must pass redirect host validation. Used for demo / shared links that may
        not pass an explicit complete_url.

        When False (staff manifest URL), omitted complete_url is stored as '' and not
        validated. Subjects may open without complete_url; end-of-instructions then
        closes the window. Staff omits staff_complete_url to mean window.close on end.
    """
    if not has_manifest_params(query):
        return None
    base_url = (query.get('base_url') or '').strip()
    files = tuple(query.getlist('f'))
    complete_raw = (query.get('complete_url') or '').strip()
    staff_complete = query.get('staff_complete_url')
    staff_complete = staff_complete.strip() if staff_complete else None

    if not base_url or not files or not _base_url_ok(base_url):
        return None
    if not files_are_supported(files):
        return None
    if complete_raw:
        if not is_safe_redirect_url(complete_raw):
            return None
        complete_url = complete_raw
    elif allow_default_subject_complete_when_complete_missing:
        complete_url = default_complete_url_from_base(base_url)
        if not is_safe_redirect_url(complete_url):
            return None
    else:
        complete_url = ''
    if staff_complete and not is_safe_redirect_url(staff_complete):
        return None
    return InstructionManifest(
        base_url=base_url,
        files=files,
        complete_url=complete_url,
        staff_complete_url=staff_complete,
    )


def manifest_to_dict(m: InstructionManifest) -> dict[str, Any]:
    return {
        'base_url': m.base_url,
        'files': list(m.files),
        'complete_url': m.complete_url,
        'staff_complete_url': m.staff_complete_url,
    }


def manifest_from_dict(d: dict[str, Any]) -> InstructionManifest | None:
    try:
        files = tuple(d['files'])
        return InstructionManifest(
            base_url=d['base_url'],
            files=files,
            complete_url=d['complete_url'],
            staff_complete_url=d.get('staff_complete_url'),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _manifest_from_session(session) -> InstructionManifest:
    return InstructionManifest(
        base_url=session.base_url,
        files=tuple(session.files),
        complete_url=session.complete_url or '',
        staff_complete_url=session.staff_complete_url or None,
    )


def get_cached_manifest(session_id: str) -> InstructionManifest | None:
    from instruction_presenter.models import InstructionSession

    try:
        session = InstructionSession.objects.get(pk=session_id)
    except (InstructionSession.DoesNotExist, ValidationError, ValueError):
        return None
    return _manifest_from_session(session)


def set_cached_manifest(session_id: str, manifest: InstructionManifest) -> None:
    from instruction_presenter.models import InstructionSession

    defaults = {
        'base_url': manifest.base_url,
        'files': list(manifest.files),
        'complete_url': manifest.complete_url,
        'staff_complete_url': manifest.staff_complete_url,
    }
    with transaction.atomic():
        session, created = InstructionSession.objects.get_or_create(
            pk=session_id,
            defaults=defaults,
        )
        if created:
            return
        for field, value in defaults.items():
            setattr(session, field, value)
        update_fields = list(defaults.keys()) + ['updated_at']
        if session.current_page > len(manifest.files):
            session.current_page = max(1, len(manifest.files))
            session.media_playing = False
            session.media_position = 0
            session.media_anchor = None
            update_fields.extend(
                ['current_page', 'media_playing', 'media_position', 'media_anchor']
            )
        session.save(update_fields=update_fields)


def ensure_manifest(request: HttpRequest, session_id: str) -> InstructionManifest | None:
    """
    Return manifest from query params (and store it) or from the saved session.
    Staff may omit complete_url / staff_complete_url. Subjects may also omit
    complete_url; a query value overrides the session redirect for that subject.
    """
    m = build_manifest_from_query(
        request.GET,
        allow_default_subject_complete_when_complete_missing=False,
    )
    if m is not None:
        set_cached_manifest(str(session_id), m)
        return m
    return get_cached_manifest(str(session_id))


def current_pdf_url(manifest: InstructionManifest, page: int) -> str:
    return manifest.pdf_url_for_page(page)


def current_file_url(manifest: InstructionManifest, page: int) -> str:
    return manifest.file_url_for_page(page)
