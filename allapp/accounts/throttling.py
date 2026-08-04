"""Database-backed throttles dedicated to credential login attempts."""

from __future__ import annotations

import hashlib

from django.conf import settings
from django.core.cache import caches
from rest_framework.throttling import SimpleRateThrottle

from .client_ip import get_client_ip


def _key_digest(*parts):
    raw = "\x00".join(str(part) for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class LoginRateThrottle(SimpleRateThrottle):
    cache_format = "wms_login_%(scope)s_%(ident)s"
    rate_setting = ""

    def __init__(self):
        self.cache = caches[settings.LOGIN_THROTTLE_CACHE_ALIAS]
        super().__init__()

    def get_rate(self):
        return getattr(settings, self.rate_setting)

    def client_ident(self, request):
        return get_client_ip(request) or "unknown"


class LoginUsernameIPThrottle(LoginRateThrottle):
    scope = "username_ip"
    rate_setting = "LOGIN_USERNAME_IP_RATE"

    def get_cache_key(self, request, view):
        username = str(request.data.get("username") or "").strip().casefold()
        ident = _key_digest(self.client_ident(request), username)
        return self.cache_format % {"scope": self.scope, "ident": ident}


class LoginIPThrottle(LoginRateThrottle):
    scope = "ip"
    rate_setting = "LOGIN_IP_RATE"

    def get_cache_key(self, request, view):
        ident = _key_digest(self.client_ident(request))
        return self.cache_format % {"scope": self.scope, "ident": ident}
