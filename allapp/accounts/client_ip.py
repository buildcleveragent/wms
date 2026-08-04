"""Trusted-proxy-aware client address extraction for authentication controls."""

from __future__ import annotations

import ipaddress

from django.conf import settings


def _valid_ip(value):
    value = str(value or "").strip()
    if not value:
        return None
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return None


def get_client_ip(request):
    """Resolve an address using only the configured number of trusted proxies."""

    remote_addr = _valid_ip(request.META.get("REMOTE_ADDR"))
    proxy_count = settings.LOGIN_TRUSTED_PROXY_COUNT
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if proxy_count <= 0 or not forwarded:
        return remote_addr

    forwarded_addresses = [item.strip() for item in forwarded.split(",")]
    if not forwarded_addresses:
        return remote_addr
    candidate = forwarded_addresses[-min(proxy_count, len(forwarded_addresses))]
    return _valid_ip(candidate) or remote_addr
