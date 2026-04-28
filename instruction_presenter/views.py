from urllib.parse import urlencode
from uuid import UUID

from django.http import HttpResponseBadRequest, HttpResponseRedirect
from django.shortcuts import render

from instruction_presenter.manifest import current_pdf_url, ensure_manifest
from instruction_presenter.session_state import get_current_page_sync

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
