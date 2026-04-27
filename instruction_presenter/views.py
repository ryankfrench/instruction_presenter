from django.http import HttpResponseBadRequest
from django.shortcuts import render

from instruction_presenter.manifest import current_pdf_url, ensure_manifest
from instruction_presenter.session_state import get_current_page_sync


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
