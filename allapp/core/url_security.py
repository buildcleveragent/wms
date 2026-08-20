"""Fail-closed validation for configured outbound HTTP endpoints."""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

from django.core.exceptions import ImproperlyConfigured


def require_https_endpoint(value, *, setting_name: str, allowed_hosts) -> str:
    """Return a normalized HTTPS endpoint restricted to an explicit host set."""

    raw = str(value or "").strip()
    parsed = urlsplit(raw)
    allowed = {str(host).strip().lower() for host in allowed_hosts if str(host).strip()}
    try:
        port = parsed.port
    except ValueError as exc:
        raise ImproperlyConfigured(f"{setting_name} contains an invalid port.") from exc
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.hostname.lower() not in allowed
        or port not in (None, 443)
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ImproperlyConfigured(
            f"{setting_name} must be an HTTPS URL on an explicitly allowed host."
        )
    return urlunsplit(("https", parsed.netloc, parsed.path, "", "")).rstrip("/")
