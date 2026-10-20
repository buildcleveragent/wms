"""Production HTTPS policy with an explicit, temporary native API exception."""

import re
from urllib.parse import urlsplit

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.http.request import split_domain_port
from django.middleware.security import SecurityMiddleware


def http_api_authority(value):
    """Validate a single HTTP origin; empty means the exception is disabled."""
    value = str(value or "").strip()
    if not value:
        return ""
    try:
        parsed = urlsplit(value)
        domain, port = split_domain_port(parsed.netloc.lower())
        if (
            parsed.scheme != "http"
            or not domain
            or parsed.username
            or parsed.password
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or re.search(r"[\s\\]", value)
            or (port and not 0 < parsed.port <= 65535)
        ):
            raise ValueError
    except ValueError:
        raise ImproperlyConfigured(
            "WMS_ANDROID_HTTP_API_ORIGIN 必须是单个 HTTP 来源（主机及端口），不能包含路径或凭据。"
        ) from None
    return f"{domain}:{port or '80'}"


class AndroidApiSecurityMiddleware(SecurityMiddleware):
    """Keep Django's security headers and redirects outside the configured API."""

    def __init__(self, get_response):
        super().__init__(get_response)
        self.http_api_authority = http_api_authority(
            getattr(settings, "WMS_ANDROID_HTTP_API_ORIGIN", "")
        )

    def process_request(self, request):
        if self.http_api_authority and not request.is_secure() and request.path.startswith("/api/"):
            # get_host also enforces ALLOWED_HOSTS. Do not trust a client-supplied
            # forwarded host or change the request's scheme to bypass redirects.
            domain, port = split_domain_port(request.get_host().lower())
            if f"{domain}:{port or '80'}" == self.http_api_authority:
                return None
        return super().process_request(request)
