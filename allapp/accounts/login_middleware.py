"""Apply the credential throttle to Django's session-login entry points too."""

import math
from types import SimpleNamespace

from django.http import HttpResponse

from .throttling import LoginIPThrottle, LoginUsernameIPThrottle


class SessionLoginThrottleMiddleware:
    login_paths = frozenset({"/admin/login", "/accounts/login", "/api-auth/login"})

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method == "POST" and request.path.rstrip("/") in self.login_paths:
            wrapped = SimpleNamespace(data=request.POST, META=request.META)
            waits = []
            for throttle in (LoginUsernameIPThrottle(), LoginIPThrottle()):
                if not throttle.allow_request(wrapped, None):
                    waits.append(throttle.wait() or 1)
            if waits:
                response = HttpResponse("登录尝试过于频繁，请稍后重试。", status=429)
                response["Retry-After"] = str(max(1, math.ceil(max(waits))))
                return response
        return self.get_response(request)
