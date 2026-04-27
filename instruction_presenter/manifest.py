"""Instruction session manifest: PDF list, completion URLs, cache helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

from django.conf import settings
from django.core.cache import cache
from django.http import HttpRequest, QueryDict
from django.utils.http import url_has_allowed_host_and_scheme


def manifest_cache_key(session_id: str) -> str:
    return f'instruction_manifest:{session_id}'


@dataclass(frozen=True)
class InstructionManifest:
    base_url: str
    files: tuple[str, ...]
    complete_url: str
    staff_complete_url: str | None

    @property
    def total_pages(self) -> int:
        return len(self.files)

    def pdf_url_for_page(self, page: int) -> str:
        """1-based page index into files."""
        if page < 1 or page > len(self.files):
            raise IndexError('page out of range')
        filename = self.files[page - 1]
        base = self.base_url if self.base_url.endswith('/') else self.base_url + '/'
        return urljoin(base, filename)


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
    return url_has_allowed_host_and_scheme(
        url,
        allowed_hosts=redirect_allowed_hosts(),
        require_https=require_https_redirects(),
    )


def has_manifest_params(query: QueryDict) -> bool:
    return bool(query.get('base_url') and query.getlist('f') and query.get('complete_url'))


def _base_url_ok(base_url: str) -> bool:
    if base_url.startswith('//'):
        return False
    if base_url.startswith('/'):
        return True
    return base_url.startswith(('http://', 'https://'))


def build_manifest_from_query(query: QueryDict) -> InstructionManifest | None:
    if not has_manifest_params(query):
        return None
    base_url = (query.get('base_url') or '').strip()
    files = tuple(query.getlist('f'))
    complete_url = (query.get('complete_url') or '').strip()
    staff_complete = query.get('staff_complete_url')
    staff_complete = staff_complete.strip() if staff_complete else None

    if not base_url or not files or not _base_url_ok(base_url):
        return None
    if not is_safe_redirect_url(complete_url):
        return None
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


def get_cached_manifest(session_id: str) -> InstructionManifest | None:
    raw = cache.get(manifest_cache_key(session_id))
    if not raw:
        return None
    return manifest_from_dict(raw)


def set_cached_manifest(session_id: str, manifest: InstructionManifest) -> None:
    ttl = getattr(settings, 'INSTRUCTION_MANIFEST_TTL', 86400)
    cache.set(manifest_cache_key(session_id), manifest_to_dict(manifest), ttl)


def ensure_manifest(request: HttpRequest, session_id: str) -> InstructionManifest | None:
    """
    Return manifest from query params (and refresh cache) or from cache.
    """
    m = build_manifest_from_query(request.GET)
    if m is not None:
        set_cached_manifest(str(session_id), m)
        return m
    return get_cached_manifest(str(session_id))


def current_pdf_url(manifest: InstructionManifest, page: int) -> str:
    return manifest.pdf_url_for_page(page)
