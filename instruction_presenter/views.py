from urllib.parse import urlencode
from uuid import UUID

from django.http import HttpResponseBadRequest, HttpResponseRedirect, QueryDict
from django.shortcuts import render

from instruction_presenter.manifest import (
    build_manifest_from_query,
    current_pdf_url,
    ensure_manifest,
)
from instruction_presenter.session_state import get_current_page_sync

# Parameterized demo (base_url + page_count); shared session for staff + subject.
_DEMO_PARAM_SESSION = UUID('c3d4e5f6-a7b8-4901-c234-deadbeef0011')
_DEMO_PARAM_PLAYER = UUID('d4e5f6a7-b8c9-4012-d345-feedface0022')
_DEMO_PARAM_COMPLETE = '/'

# Demo: Dutch sealed first PDFs on chapman static (shared session for staff + subject).
_DEMO_DUTCH_SEALED_SESSION = UUID('a1b2c3d4-e5f6-4789-a012-3456789abcde')
_DEMO_DUTCH_SEALED_PLAYER = UUID('b2c3d4e5-f6a7-4890-b123-456789abcdef')
_DEMO_DUTCH_SEALED_BASE = (
    'https://chapman-experiments-r1.azurewebsites.net/static/dutch_sealed_first/'
)
# Same-origin path so redirect validation passes when ALLOWED_HOSTS is only this app.
# For absolute URLs to another host, add that host to INSTRUCTION_REDIRECT_ALLOWED_HOSTS.
_DEMO_DUTCH_SEALED_COMPLETE = '/'


def _demo_dutch_sealed_first_query() -> str:
    return urlencode(
        [
            ('base_url', _DEMO_DUTCH_SEALED_BASE),
            ('f', 'page001.pdf'),
            ('f', 'page002.pdf'),
            ('complete_url', _DEMO_DUTCH_SEALED_COMPLETE),
            ('staff_complete_url', _DEMO_DUTCH_SEALED_COMPLETE),
        ]
    )


def _parse_page_count(request) -> int | None:
    raw = (request.GET.get('page_count') or '').strip()
    if not raw:
        return None
    try:
        n = int(raw, 10)
    except ValueError:
        return None
    if n < 1:
        return None
    return n


def _numbered_page_files(page_count: int) -> list[str]:
    """page001.pdf, page002.pdf, … (same convention as dutch-sealed-first demo)."""
    return [f'page{i:03d}.pdf' for i in range(1, page_count + 1)]


def _demo_param_query_string(
    base_url: str,
    files: list[str],
    complete_url: str,
    staff_complete_url: str,
) -> str:
    pairs: list[tuple[str, str]] = [('base_url', base_url)]
    for f in files:
        pairs.append(('f', f))
    pairs.append(('complete_url', complete_url))
    pairs.append(('staff_complete_url', staff_complete_url))
    return urlencode(pairs)


def _demo_param_redirect_response(request, *, subject: bool):
    """
    Build redirect for parameterized demo using base_url, page_count (number of PDFs),
    and optional complete_url / staff_complete_url (default same-origin `/`).
    Filenames are generated as page001.pdf, page002.pdf, …
    """
    base_url = (request.GET.get('base_url') or '').strip()
    page_count = _parse_page_count(request)
    complete_url = (request.GET.get('complete_url') or _DEMO_PARAM_COMPLETE).strip() or _DEMO_PARAM_COMPLETE
    staff_raw = request.GET.get('staff_complete_url')
    staff_complete_url = (
        staff_raw.strip() if staff_raw else complete_url
    ) or complete_url

    if not base_url or page_count is None:
        return HttpResponseBadRequest(
            'Missing or invalid base_url or page_count. '
            'Provide base_url=… and page_count=… (positive integer). '
            'PDFs are named page001.pdf, page002.pdf, … under base_url. '
            'Optional: complete_url, staff_complete_url.'
        )

    files = _numbered_page_files(page_count)

    qd = QueryDict(mutable=True)
    qd['base_url'] = base_url
    qd.setlist('f', files)
    qd['complete_url'] = complete_url
    qd['staff_complete_url'] = staff_complete_url
    if build_manifest_from_query(qd) is None:
        return HttpResponseBadRequest(
            'Invalid base_url or complete_url / staff_complete_url (must be allowed hosts).'
        )

    q = _demo_param_query_string(base_url, files, complete_url, staff_complete_url)
    if subject:
        path = (
            f'/instructions/{_DEMO_PARAM_SESSION}/subject/'
            f'{_DEMO_PARAM_PLAYER}/?{q}'
        )
    else:
        path = f'/instructions/{_DEMO_PARAM_SESSION}/staff/?{q}'
    return HttpResponseRedirect(path)


def demo_staff(request):
    return _demo_param_redirect_response(request, subject=False)


def demo_subject(request):
    """Same manifest as demo_staff; opens the subject view for the shared demo session."""
    return _demo_param_redirect_response(request, subject=True)


def demo_dutch_sealed_first_staff(request):
    q = _demo_dutch_sealed_first_query()
    return HttpResponseRedirect(
        f'/instructions/{_DEMO_DUTCH_SEALED_SESSION}/staff/?{q}'
    )


def demo_dutch_sealed_first_subject(request):
    q = _demo_dutch_sealed_first_query()
    return HttpResponseRedirect(
        f'/instructions/{_DEMO_DUTCH_SEALED_SESSION}/subject/'
        f'{_DEMO_DUTCH_SEALED_PLAYER}/?{q}'
    )


def staff_home(request, session_id):
    manifest = ensure_manifest(request, session_id)
    if not manifest or manifest.total_pages == 0:
        return HttpResponseBadRequest(
            'Missing or invalid instruction parameters. '
            'Provide base_url, repeated f= for each PDF in order, and complete_url, '
            'or open a link that includes them once so the session is cached.'
        )
    sid = str(session_id)
    page = min(max(get_current_page_sync(sid), 1), manifest.total_pages)
    context = {
        'session_id': sid,
        'current_page': page,
        'total_pages': manifest.total_pages,
        'current_pdf': current_pdf_url(manifest, page),
    }
    return render(request, 'staff_home.html', context)


def subject_home(request, session_id, player_key):
    manifest = ensure_manifest(request, session_id)
    if not manifest or manifest.total_pages == 0:
        return HttpResponseBadRequest(
            'Unknown or expired instruction session. '
            'Use the same query parameters as staff, or open staff first.'
        )
    sid = str(session_id)
    page = min(max(get_current_page_sync(sid), 1), manifest.total_pages)
    context = {
        'session_id': sid,
        'player_key': str(player_key),
        'current_page': page,
        'total_pages': manifest.total_pages,
        'current_pdf': current_pdf_url(manifest, page),
    }
    return render(request, 'subject_home.html', context)
