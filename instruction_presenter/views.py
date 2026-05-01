from urllib.parse import urlencode
from uuid import UUID

from django.http import HttpResponseBadRequest, HttpResponseRedirect, QueryDict
from django.shortcuts import render

from instruction_presenter.manifest import (
    build_manifest_from_query,
    current_pdf_url,
    ensure_manifest,
    get_cached_manifest,
    set_subject_completion_url,
    subject_completion_overlay_allowed,
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
    """
    Opens the parameterized demo subject view. Subjects need not pass complete_url
    here (the manifest from staff supplies completion when instructions end). Optional
    complete_url= overrides the per-subject redirect when instructions end.
    """
    raw = (request.GET.get('complete_url') or '').strip()
    path = (
        f'/instructions/{_DEMO_PARAM_SESSION}/subject/'
        f'{_DEMO_PARAM_PLAYER}/'
    )
    if not raw:
        return HttpResponseRedirect(path)
    if not subject_completion_overlay_allowed(raw):
        return HttpResponseBadRequest(
            'Invalid complete_url (must be an allowed host for redirects '
            'or the canonical demo URL).'
        )
    return HttpResponseRedirect(f'{path}?{urlencode([("complete_url", raw)])}')


def demo_dutch_sealed_first_staff(request):
    q = _demo_dutch_sealed_first_query()
    return HttpResponseRedirect(
        f'/instructions/{_DEMO_DUTCH_SEALED_SESSION}/staff/?{q}'
    )


def demo_dutch_sealed_first_subject(request):
    raw = (request.GET.get('complete_url') or '').strip()
    path = (
        f'/instructions/{_DEMO_DUTCH_SEALED_SESSION}/subject/'
        f'{_DEMO_DUTCH_SEALED_PLAYER}/'
    )
    if not raw:
        return HttpResponseRedirect(path)
    if not subject_completion_overlay_allowed(raw):
        return HttpResponseBadRequest(
            'Invalid complete_url (must be an allowed host for redirects '
            'or the canonical demo URL).'
        )
    return HttpResponseRedirect(f'{path}?{urlencode([("complete_url", raw)])}')


def staff_home(request, session_id):
    manifest = ensure_manifest(request, session_id)
    if not manifest or manifest.total_pages == 0:
        return HttpResponseBadRequest(
            'Missing or invalid instruction parameters. '
            'Provide base_url, repeated f= for each PDF in order '
            '(optional complete_url; if omitted, base_url/subject-home/ is used), '
            'or open a link that includes those parameters once so the session is cached.'
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


# Sessions where subject_home does not require complete_url (built-in demos).
_SUBJECT_HOME_OPTIONAL_COMPLETE_URL_SESSIONS = frozenset(
    {
        str(_DEMO_PARAM_SESSION),
        str(_DEMO_DUTCH_SEALED_SESSION),
    }
)


def subject_home(request, session_id, player_key):
    sid = str(session_id)
    pk = str(player_key)

    # Subjects must not pass manifest parameters; staff establishes the PDF list.
    if set(request.GET.keys()) - {'complete_url'}:
        return HttpResponseBadRequest(
            'Subject links only accept complete_url. '
            'Open staff first so the instruction session is cached.'
        )

    overlay = (request.GET.get('complete_url') or '').strip()
    optional_complete = sid in _SUBJECT_HOME_OPTIONAL_COMPLETE_URL_SESSIONS
    if not optional_complete:
        if not overlay:
            return HttpResponseBadRequest(
                'Missing complete_url. Open your assigned subject link '
                'including complete_url=….'
            )
        if not subject_completion_overlay_allowed(overlay):
            return HttpResponseBadRequest(
                'Invalid complete_url (must use an allowed host for redirects).'
            )
        set_subject_completion_url(sid, pk, overlay)
    elif overlay:
        if not subject_completion_overlay_allowed(overlay):
            return HttpResponseBadRequest(
                'Invalid complete_url (must be an allowed host for redirects '
                'or the canonical demo URL).'
            )
        set_subject_completion_url(sid, pk, overlay)

    manifest = get_cached_manifest(sid)
    if not manifest or manifest.total_pages == 0:
        return HttpResponseBadRequest(
            'Unknown or expired instruction session. '
            'Staff must open the manifest first so this session is cached.'
        )
    page = min(max(get_current_page_sync(sid), 1), manifest.total_pages)
    context = {
        'session_id': sid,
        'player_key': str(player_key),
        'current_page': page,
        'total_pages': manifest.total_pages,
        'current_pdf': current_pdf_url(manifest, page),
    }
    return render(request, 'subject_home.html', context)
