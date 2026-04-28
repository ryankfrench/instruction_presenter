"""
Azure App Service health/liveness probes often send Host: <link-local-ip>:8000.
Django rejects that unless every instance IP is listed in ALLOWED_HOSTS. Rewrite
to WEBSITE_HOSTNAME so ALLOWED_HOSTS and Channels origin checks stay strict for
real browser traffic.
"""

from __future__ import annotations

import os
import re

_INTERNAL_HOST = re.compile(r'^169\.254\.\d{1,3}\.\d{1,3}(:\d+)?$')


class AzureInternalHostMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        raw = request.META.get('HTTP_HOST', '')
        host = raw.split(',')[0].strip()
        canonical = os.environ.get('WEBSITE_HOSTNAME', '').strip()
        if canonical and host and _INTERNAL_HOST.match(host):
            request.META['HTTP_HOST'] = canonical
        return self.get_response(request)
