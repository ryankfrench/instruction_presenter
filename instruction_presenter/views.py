from urllib.parse import urlencode
from uuid import UUID, uuid4

from django.contrib.auth.decorators import login_required
from django.http import HttpResponseBadRequest, HttpResponseRedirect, QueryDict
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from instruction_presenter.manifest import (
    build_manifest_from_query,
    current_file_url,
    current_pdf_url,
    ensure_manifest,
    files_are_supported,
    get_cached_manifest,
    set_cached_manifest,
    set_subject_completion_url,
    subject_completion_overlay_allowed,
)
from instruction_presenter.session_state import get_current_page_sync
from instruction_presenter.staff_sessions import build_status_payload, index_rows

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
    return _demo_param_query_string(
        _DEMO_DUTCH_SEALED_BASE,
        _numbered_page_files(12),
        _DEMO_DUTCH_SEALED_COMPLETE,
        _DEMO_DUTCH_SEALED_COMPLETE,
    )


def _parse_positive_int(raw: str) -> int | None:
    raw = (raw or '').strip()
    if not raw:
        return None
    try:
        n = int(raw, 10)
    except ValueError:
        return None
    if n < 1:
        return None
    return n


def _parse_page_count(request) -> int | None:
    return _parse_positive_int(request.GET.get('page_count') or '')


def _numbered_page_files(page_count: int) -> list[str]:
    """page001.pdf, page002.pdf, … (same convention as dutch-sealed-first demo)."""
    return [f'page{i:03d}.pdf' for i in range(1, page_count + 1)]


def _parse_file_list(raw: str) -> list[str]:
    """Non-empty lines from the staff filenames field."""
    return [line.strip() for line in (raw or '').splitlines() if line.strip()]


_UNSUPPORTED_FILE_MESSAGE = 'Each file must be a .pdf or .mp4.'


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


def _absolute(request, path: str) -> str:
    # Keep literal placeholders such as {player_key} unescaped.
    return f'{request.scheme}://{request.get_host()}{path}'


def _index_context(request, *, form_data, form_error, created_id):
    sessions = []
    created_session = None
    for row in index_rows():
        session = {
            **row,
            'presenter_url': _absolute(request, row['presenter_path']),
            'status_url': _absolute(request, row['status_path']),
            'subject_pattern_url': _absolute(request, row['subject_pattern_path']),
            'subject_join_url': _absolute(request, row['subject_join_path']),
        }
        sessions.append(session)
        if created_id and session['session_id'] == created_id:
            created_session = session
    return {
        'sessions': sessions,
        'form_data': form_data,
        'form_error': form_error,
        'created_session': created_session,
    }


@login_required
@require_http_methods(['GET', 'POST'])
def staff_index(request):
    """Signed-in list of instruction sessions, and a form to start one."""
    form_data = {
        'base_url': '',
        'page_count': '',
        'files': '',
        'complete_url': '',
        'staff_complete_url': '',
    }
    form_error = ''
    created_id = (request.GET.get('created') or '').strip()

    if request.method == 'POST':
        form_data = {
            'base_url': (request.POST.get('base_url') or '').strip(),
            'page_count': (request.POST.get('page_count') or '').strip(),
            'files': request.POST.get('files') or '',
            'complete_url': (request.POST.get('complete_url') or '').strip(),
            'staff_complete_url': (request.POST.get('staff_complete_url') or '').strip(),
        }
        listed_files = _parse_file_list(form_data['files'])
        page_count = _parse_positive_int(form_data['page_count'])
        if not form_data['base_url'] or (not listed_files and page_count is None):
            form_error = (
                'Provide a base URL and a positive page count, or list file paths.'
            )
        elif listed_files and not files_are_supported(listed_files):
            form_error = _UNSUPPORTED_FILE_MESSAGE
        else:
            files = listed_files or _numbered_page_files(page_count)
            qd = QueryDict(mutable=True)
            qd['base_url'] = form_data['base_url']
            qd.setlist('f', files)
            if form_data['complete_url']:
                qd['complete_url'] = form_data['complete_url']
            if form_data['staff_complete_url']:
                qd['staff_complete_url'] = form_data['staff_complete_url']
            manifest = build_manifest_from_query(
                qd,
                allow_default_subject_complete_when_complete_missing=False,
            )
            if manifest is None:
                form_error = (
                    'Invalid base URL or completion URL. '
                    'Completion URLs must use an allowed host.'
                )
            else:
                session_id = uuid4()
                set_cached_manifest(str(session_id), manifest)
                index_path = reverse('instruction_presenter:staff_index')
                return HttpResponseRedirect(f'{index_path}?created={session_id}')

    context = _index_context(
        request,
        form_data=form_data,
        form_error=form_error,
        created_id=created_id,
    )
    return render(request, 'staff_index.html', context)


def _unsupported_file_response(request):
    """Reject a staff link whose f= list contains a type other than pdf or mp4."""
    files = request.GET.getlist('f')
    if files and not files_are_supported(files):
        return HttpResponseBadRequest(_UNSUPPORTED_FILE_MESSAGE)
    return None


def _page_view(request, session_id, manifest, *, player_key=None):
    sid = str(session_id)
    page = min(max(get_current_page_sync(sid), 1), manifest.total_pages)
    kind = manifest.media_kind_for_page(page)
    if kind is None:
        return HttpResponseBadRequest(_UNSUPPORTED_FILE_MESSAGE)
    context = {
        'session_id': sid,
        'current_page': page,
        'total_pages': manifest.total_pages,
        'current_file': current_file_url(manifest, page),
        'current_pdf': current_pdf_url(manifest, page),
    }
    if player_key is None:
        template = 'staff_video.html' if kind == 'video' else 'staff_home.html'
    else:
        context['player_key'] = str(player_key)
        template = 'subject_video.html' if kind == 'video' else 'subject_home.html'
    return render(request, template, context)


def staff_home(request, session_id):
    rejected = _unsupported_file_response(request)
    if rejected is not None:
        return rejected
    manifest = ensure_manifest(request, session_id)
    if not manifest or manifest.total_pages == 0:
        return HttpResponseBadRequest(
            'Missing or invalid instruction parameters. '
            'Provide base_url, repeated f= for each .pdf or .mp4 in order '
            '(optional complete_url and staff_complete_url for end-of-session redirects), '
            'or open a link that includes those parameters once so the session is cached.'
        )
    return _page_view(request, session_id, manifest)


def staff_status(request, session_id):
    sid = str(session_id)
    payload = build_status_payload(sid)
    if payload is None:
        return HttpResponseBadRequest(
            'Unknown or expired instruction session. '
            'Open the presenter link with base_url and f= parameters first, '
            'or create the session from the staff index.'
        )
    return render(request, 'staff_status.html', {'status': payload})


def subject_home(request, session_id, player_key):
    sid = str(session_id)
    pk = str(player_key)

    # Subjects must not pass manifest parameters; staff establishes the PDF list.
    if set(request.GET.keys()) - {'complete_url'}:
        return HttpResponseBadRequest(
            'Subject links only accept complete_url. '
            'Open staff first so the instruction session is cached.'
        )

    # complete_url is optional. When present it overrides the session redirect
    # for this subject. When omitted, end-of-instructions uses the manifest URL,
    # or closes the window if that is empty too.
    overlay = (request.GET.get('complete_url') or '').strip()
    if overlay:
        if not subject_completion_overlay_allowed(overlay):
            return HttpResponseBadRequest(
                'Invalid complete_url (must use an allowed host for redirects).'
            )
        set_subject_completion_url(sid, pk, overlay)

    manifest = get_cached_manifest(sid)
    if not manifest or manifest.total_pages == 0:
        return HttpResponseBadRequest(
            'Unknown or expired instruction session. '
            'Staff must open the manifest first so this session is cached.'
        )
    return _page_view(request, session_id, manifest, player_key=player_key)


def subject_join(request, session_id):
    """Assign a new player key and open that subject's instructions."""
    sid = str(session_id)
    if set(request.GET.keys()) - {'complete_url'}:
        return HttpResponseBadRequest(
            'Subject links only accept complete_url. '
            'Open staff first so the instruction session is cached.'
        )

    overlay = (request.GET.get('complete_url') or '').strip()
    if overlay and not subject_completion_overlay_allowed(overlay):
        return HttpResponseBadRequest(
            'Invalid complete_url (must use an allowed host for redirects).'
        )

    manifest = get_cached_manifest(sid)
    if not manifest or manifest.total_pages == 0:
        return HttpResponseBadRequest(
            'Unknown or expired instruction session. '
            'Staff must open the manifest first so this session is cached.'
        )

    path = reverse(
        'instruction_presenter:subject_home',
        args=[sid, uuid4()],
    )
    if overlay:
        path = f'{path}?{urlencode([("complete_url", overlay)])}'
    return HttpResponseRedirect(path)
