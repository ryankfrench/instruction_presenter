"""ESI auth service client.

The app signs in to ESI_AUTH_URL with its service account, then asks get-auth
whether a staff member may use this app. The login view tries a local password
first. Missing settings or a failed call return None.
"""

from __future__ import annotations

import logging
from datetime import timedelta

import requests
from django.conf import settings
from django.utils import timezone

from instruction_presenter.models import EsiAuthToken

logger = logging.getLogger(__name__)

_REQUIRED_SETTINGS = (
    'ESI_AUTH_URL',
    'ESI_AUTH_APP',
    'ESI_AUTH_USERNAME',
    'ESI_AUTH_PASS',
    'ESI_AUTH_CLIENT_ID',
    'ESI_AUTH_CLIENT_SECRET',
)
_TOKEN_SKEW = timedelta(minutes=5)
_TIMEOUT_SECONDS = 20


def esi_configured() -> bool:
    for name in _REQUIRED_SETTINGS:
        if not str(getattr(settings, name, '') or '').strip():
            return False
    return True


def fetch_profile(username: str, password: str) -> dict | None:
    """Return the ESI profile when get-auth accepts this staff member."""
    if not esi_configured():
        return None
    if not _ensure_access_token():
        return None
    return _get_auth(username, password, allow_retry=True)


def _base_url() -> str:
    return str(settings.ESI_AUTH_URL).rstrip('/')


def _ensure_access_token() -> bool:
    row = EsiAuthToken.load()
    still_valid = (
        row.access_token
        and row.expires_at is not None
        and row.expires_at > timezone.now() + _TOKEN_SKEW
    )
    if still_valid:
        return True
    if row.refresh_token and _request_token(row, use_refresh=True):
        return True
    return _request_token(row, use_refresh=False)


def _force_new_token() -> bool:
    row = EsiAuthToken.load()
    if row.refresh_token and _request_token(row, use_refresh=True):
        return True
    return _request_token(row, use_refresh=False)


def _request_token(row: EsiAuthToken, *, use_refresh: bool) -> bool:
    headers = {'Accept': 'application/json', 'Accept-Language': 'en_US'}
    url = f'{_base_url()}/o/token/'
    try:
        if use_refresh:
            response = requests.post(
                url,
                headers=headers,
                data={
                    'grant_type': 'refresh_token',
                    'refresh_token': row.refresh_token,
                    'client_id': settings.ESI_AUTH_CLIENT_ID,
                    'client_secret': settings.ESI_AUTH_CLIENT_SECRET,
                },
                timeout=_TIMEOUT_SECONDS,
            )
        else:
            response = requests.post(
                url,
                headers=headers,
                data={
                    'grant_type': 'password',
                    'username': settings.ESI_AUTH_USERNAME,
                    'password': settings.ESI_AUTH_PASS,
                },
                auth=(
                    str(settings.ESI_AUTH_CLIENT_ID),
                    str(settings.ESI_AUTH_CLIENT_SECRET),
                ),
                timeout=_TIMEOUT_SECONDS,
            )
    except requests.RequestException:
        logger.warning('ESI auth token request failed')
        return False

    if response.status_code != 200:
        logger.info('ESI auth token request rejected: status %s', response.status_code)
        return False

    try:
        payload = response.json()
    except ValueError:
        logger.warning('ESI auth token response was not JSON')
        return False
    if not isinstance(payload, dict) or not payload.get('access_token'):
        logger.warning('ESI auth token response did not include an access token')
        return False

    try:
        expires_in = int(payload.get('expires_in') or 0)
    except (TypeError, ValueError):
        expires_in = 0

    row.access_token = payload['access_token']
    refresh_token = payload.get('refresh_token') or ''
    if refresh_token:
        row.refresh_token = refresh_token
    row.expires_at = timezone.now() + timedelta(seconds=expires_in)
    row.save(update_fields=['access_token', 'refresh_token', 'expires_at'])
    return True


def _get_auth(username: str, password: str, *, allow_retry: bool) -> dict | None:
    row = EsiAuthToken.load()
    if not row.access_token:
        return None
    try:
        response = requests.get(
            f'{_base_url()}/get-auth/',
            headers={
                'Content-Type': 'application/json',
                'Accept': 'application/json',
                'Authorization': f'Bearer {row.access_token}',
            },
            json={
                'app_name': settings.ESI_AUTH_APP,
                'username': username,
                'password': password,
            },
            timeout=_TIMEOUT_SECONDS,
        )
    except requests.RequestException:
        logger.warning('ESI get-auth request failed')
        return None

    if response.status_code != 200:
        logger.info('ESI get-auth rejected: status %s', response.status_code)
        if allow_retry and _force_new_token():
            return _get_auth(username, password, allow_retry=False)
        return None

    try:
        payload = response.json()
    except ValueError:
        logger.warning('ESI get-auth response was not JSON')
        return None
    if not isinstance(payload, dict) or payload.get('status') == 'fail':
        return None
    profile = payload.get('profile')
    if not isinstance(profile, dict):
        return None
    return profile
