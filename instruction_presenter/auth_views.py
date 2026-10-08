"""Staff sign-in: ESI auth first, then a local Django password."""

from __future__ import annotations

import logging
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import authenticate, get_user_model, login
from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render, resolve_url
from django.utils import timezone
from django.utils.crypto import get_random_string
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods

from instruction_presenter.esi_auth import fetch_profile
from instruction_presenter.models import LoginAttempt

logger = logging.getLogger(__name__)

RATE_LIMIT_MESSAGE = 'Too many sign-in attempts. Try again in a minute.'
_FAILURE_WINDOW = timedelta(minutes=1)
_FAILURE_LIMIT = 5
_AUTH_BACKEND = 'django.contrib.auth.backends.ModelBackend'

User = get_user_model()


class _EsiAccountRejected(Exception):
    def __init__(self, note: str) -> None:
        self.note = note


@require_http_methods(['GET', 'POST'])
def login_view(request):
    if request.method == 'GET' and request.user.is_authenticated:
        return redirect(settings.LOGIN_REDIRECT_URL)

    next_url = request.POST.get('next', request.GET.get('next', ''))
    if request.method != 'POST':
        return render(
            request,
            'registration/login.html',
            {'form': AuthenticationForm(request), 'next': next_url},
        )

    form = AuthenticationForm(request, data=request.POST)
    username = (request.POST.get('username') or '').strip()
    password = request.POST.get('password') or ''
    existing = _find_user(username)
    login_error = ''

    if not username or not password:
        form.add_error(None, 'Invalid username or password')
    elif _is_rate_limited(existing):
        login_error = RATE_LIMIT_MESSAGE
    else:
        signed_in = _login_esi_or_local(request, username, password, existing)
        if signed_in is not None:
            return redirect(_safe_next(request, next_url))
        form.add_error(None, 'Invalid username or password')

    return render(
        request,
        'registration/login.html',
        {'form': form, 'next': next_url, 'login_error': login_error},
    )


def _login_esi_or_local(request, username, password, existing):
    """Sign in via ESI, or a local password when ESI does not accept them.

    Returns the user on success. None means the form should show an error.
    An ESI profile that cannot be stored is a refusal, not a local fallback.
    """
    profile = fetch_profile(username.lower(), password)
    if profile is not None:
        try:
            user = _user_from_esi_profile(profile)
        except _EsiAccountRejected as exc:
            _record(existing, success=False, note=exc.note)
            return None
        _record(user, success=True)
        user.backend = _AUTH_BACKEND
        login(request, user)
        return user

    user = authenticate(request, username=username, password=password)
    if user is None and existing is not None and existing.get_username() != username:
        user = authenticate(
            request,
            username=existing.get_username(),
            password=password,
        )
    if user is not None:
        _record(user, success=True)
        login(request, user)
        return user

    _record(existing, success=False, note='Invalid Password')
    return None


def _find_user(identifier: str):
    if not identifier:
        return None
    return (
        User.objects.filter(email__iexact=identifier).first()
        or User.objects.filter(username__iexact=identifier).first()
    )


def _is_rate_limited(user) -> bool:
    if user is None:
        return False
    since = timezone.now() - _FAILURE_WINDOW
    failures = user.login_attempts.filter(success=False, timestamp__gte=since).count()
    return failures > _FAILURE_LIMIT


def _record(user, *, success: bool, note: str = '') -> None:
    if user is None:
        return
    LoginAttempt.objects.create(user=user, success=success, note=note)


def _user_from_esi_profile(profile: dict):
    global_id = str(profile.get('global_id') or '').strip()
    email = str(profile.get('email') or '').strip()
    if not global_id or not email or len(global_id) > 150:
        logger.warning('ESI auth profile was missing a usable id or email')
        raise _EsiAccountRejected('Unusable ESI profile')

    first_name = str(profile.get('first_name') or '')[:150]
    last_name = str(profile.get('last_name') or '')[:150]
    user = User.objects.filter(username=global_id).first()
    if user is None:
        if User.objects.filter(email__iexact=email).exists():
            logger.warning('ESI auth refused because the email is already in use')
            raise _EsiAccountRejected('Email already exists')
        try:
            return User.objects.create_user(
                username=global_id,
                email=email,
                password=get_random_string(22),
                first_name=first_name,
                last_name=last_name,
            )
        except ValidationError:
            logger.warning('ESI auth could not create a local user')
            raise _EsiAccountRejected('Could not create user')

    user.email = email
    user.first_name = first_name
    user.last_name = last_name
    user.save(update_fields=['email', 'first_name', 'last_name'])
    return user


def _safe_next(request, next_url: str) -> str:
    if next_url and url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return next_url
    return resolve_url(settings.LOGIN_REDIRECT_URL)
