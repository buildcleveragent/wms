"""ApiZero GS1 lookup client with database cache, coalescing and rate control."""

from __future__ import annotations

import json
import re
import socket
import time
from datetime import timedelta
from urllib import error as url_error
from urllib import parse as url_parse
from urllib import request as url_request
from urllib.parse import urlparse

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from allapp.core.models import SecretSettingError, SystemSetting

from .models import Gs1LookupCache, Gs1ProviderRateLimit

GTIN_RE = re.compile(r"^(?:\d{8}|\d{12}|\d{13}|\d{14}|01\d{14})$")
ALLOWED_IMAGE_HOST = "gds.org.cn"


class Gs1LookupError(Exception):
    def __init__(self, message, *, code="provider_unavailable", retry_after=None):
        super().__init__(message)
        self.code = code
        self.retry_after = retry_after


def normalize_gtin(value: str) -> tuple[str, str]:
    raw = str(value or "").strip()
    if not GTIN_RE.fullmatch(raw):
        raise ValueError("条码必须是 8/12/13/14 位数字，或 01+GTIN-14。")
    lookup_code = raw[2:] if len(raw) == 16 and raw.startswith("01") else raw
    return lookup_code, lookup_code.zfill(14)


def equivalent_gtins(value: str) -> tuple[str, ...]:
    lookup_code, canonical = normalize_gtin(value)
    variants = {lookup_code, canonical}
    for length in (13, 12, 8):
        candidate = canonical[-length:]
        if candidate.zfill(14) == canonical:
            variants.add(candidate)
    return tuple(sorted(variants, key=lambda item: (len(item), item)))


def _sanitize_images(values) -> list[str]:
    images = []
    for value in values if isinstance(values, list) else []:
        text = str(value or "").strip()
        parsed = urlparse(text)
        host = (parsed.hostname or "").lower()
        if parsed.scheme == "https" and (
            host == ALLOWED_IMAGE_HOST or host.endswith(f".{ALLOWED_IMAGE_HOST}")
        ):
            images.append(text)
    return images[:5]


def public_candidate(cache: Gs1LookupCache) -> dict:
    data = dict(cache.payload or {})
    return {
        "lookup_id": str(cache.pk),
        "barcode": data.get("barcode") or cache.query_code,
        "gtin14": data.get("gtin14") or cache.canonical_gtin,
        "found": bool(cache.found),
        "registered": bool(cache.registered),
        "registration_message": data.get("registration_message") or "",
        "name": data.get("name") or "",
        "brand": data.get("brand") or "",
        "general_name": data.get("general_name") or "",
        "category": data.get("category") or "",
        "specification": data.get("specification") or data.get("net_content") or "",
        "net_content": data.get("net_content") or "",
        "manufacturer": data.get("manufacturer") or "",
        "images": _sanitize_images(data.get("images")),
        "provider_request_id": cache.provider_request_id,
        "expires_at": cache.expires_at,
    }


def _reserve_provider_slot():
    now = timezone.now()
    with transaction.atomic():
        try:
            gate, _ = Gs1ProviderRateLimit.objects.get_or_create(
                provider="apizero",
                defaults={"next_allowed_at": now},
            )
        except IntegrityError:
            gate = Gs1ProviderRateLimit.objects.get(provider="apizero")
        gate = Gs1ProviderRateLimit.objects.select_for_update().get(pk=gate.pk)
        scheduled = max(now, gate.next_allowed_at)
        delay = max(0.0, (scheduled - now).total_seconds())
        if delay > 2.0:
            raise Gs1LookupError(
                "GS1 查询繁忙，请稍后重试。",
                code="provider_rate_limited",
                retry_after=1,
            )
        gate.next_allowed_at = scheduled + timedelta(milliseconds=500)
        gate.save(update_fields=["next_allowed_at", "updated_at"])
    if delay:
        time.sleep(delay)


def _provider_request(query_code: str) -> dict:
    try:
        api_key = SystemSetting.get_secret_value(
            SystemSetting.INTEGRATION_NAMESPACE,
            SystemSetting.APIZERO_GS1_API_KEY,
            "",
        )
    except SecretSettingError as exc:
        raise Gs1LookupError(
            "GS1 查询配置无法解密，请管理员检查系统设置加密主密钥。",
            code="provider_not_configured",
        ) from exc
    api_key = str(api_key or "").strip()
    if not api_key:
        raise Gs1LookupError(
            "GS1 查询配置缺失：尚未配置 ApiZero API Key，请管理员在系统设置中配置。",
            code="provider_not_configured",
        )

    _reserve_provider_slot()
    endpoint = getattr(
        settings, "APIZERO_GS1_URL", "https://v1.apizero.cn/api/barcode-gs1"
    )
    query = url_parse.urlencode({"code": query_code, "key": api_key})
    req = url_request.Request(
        f"{endpoint}?{query}",
        headers={"Accept": "application/json", "User-Agent": "WMS-GS1/1.0"},
        method="GET",
    )
    try:
        with url_request.urlopen(
            req, timeout=float(getattr(settings, "APIZERO_GS1_TIMEOUT", 5.0))
        ) as response:
            body = response.read().decode("utf-8")
    except url_error.HTTPError as exc:
        if exc.code == 429:
            raise Gs1LookupError(
                "GS1 查询过于频繁，请稍后重试。",
                code="provider_rate_limited",
                retry_after=1,
            ) from exc
        raise Gs1LookupError(
            f"GS1 查询服务返回 HTTP {exc.code}，请稍后重试。",
            code="provider_network_error",
        ) from exc
    except (TimeoutError, socket.timeout) as exc:
        raise Gs1LookupError(
            "GS1 查询超时，请稍后重试。", code="provider_timeout"
        ) from exc
    except url_error.URLError as exc:
        if isinstance(exc.reason, (TimeoutError, socket.timeout)):
            raise Gs1LookupError(
                "GS1 查询超时，请稍后重试。", code="provider_timeout"
            ) from exc
        raise Gs1LookupError(
            "无法连接 GS1 查询服务，请检查网络后重试。",
            code="provider_network_error",
        ) from exc
    except OSError as exc:
        raise Gs1LookupError(
            "无法连接 GS1 查询服务，请检查网络后重试。",
            code="provider_network_error",
        ) from exc
    except UnicodeDecodeError as exc:
        raise Gs1LookupError(
            "GS1 查询服务返回了无效数据。", code="provider_invalid_response"
        ) from exc
    try:
        result = json.loads(body)
    except (TypeError, ValueError) as exc:
        raise Gs1LookupError(
            "GS1 查询服务返回了无效数据。", code="provider_invalid_response"
        ) from exc
    if not isinstance(result, dict):
        raise Gs1LookupError(
            "GS1 查询服务返回了无效数据。", code="provider_invalid_response"
        )
    try:
        provider_code = int(result.get("code", -1))
    except (TypeError, ValueError) as exc:
        raise Gs1LookupError(
            "GS1 查询服务返回了无效状态码。", code="provider_invalid_response"
        ) from exc
    if provider_code != 0:
        mapping = {
            4000: ("条码格式不受 GS1 查询服务支持。", "invalid_barcode", None),
            4029: ("GS1 查询过于频繁，请稍后重试。", "provider_rate_limited", 1),
            4030: (
                "GS1 查询额度已用完，请联系管理员。",
                "provider_quota_exhausted",
                None,
            ),
        }
        message, code, retry_after = mapping.get(
            provider_code,
            ("GS1 查询服务暂不可用，请稍后重试。", "provider_unavailable", None),
        )
        raise Gs1LookupError(message, code=code, retry_after=retry_after)
    if not isinstance(result.get("data"), dict):
        raise Gs1LookupError(
            "GS1 查询服务返回了无效数据。", code="provider_invalid_response"
        )
    return result


def get_or_fetch_lookup(barcode: str) -> tuple[Gs1LookupCache, bool]:
    query_code, canonical = normalize_gtin(barcode)
    now = timezone.now()
    lease_until = now + timedelta(seconds=30)
    with transaction.atomic():
        try:
            cache, created = Gs1LookupCache.objects.get_or_create(
                canonical_gtin=canonical,
                defaults={
                    "query_code": query_code,
                    "status": Gs1LookupCache.Status.FETCHING,
                    "expires_at": lease_until,
                    "lease_until": lease_until,
                },
            )
        except IntegrityError:
            cache = Gs1LookupCache.objects.get(canonical_gtin=canonical)
            created = False
        cache = Gs1LookupCache.objects.select_for_update().get(pk=cache.pk)
        if cache.status == Gs1LookupCache.Status.SUCCESS and cache.expires_at > now:
            return cache, True
        if (
            not created
            and cache.status == Gs1LookupCache.Status.FETCHING
            and cache.lease_until
            and cache.lease_until > now
        ):
            raise Gs1LookupError(
                "该条码正在查询，请稍后重试。", code="lookup_in_progress", retry_after=1
            )
        if cache.status == Gs1LookupCache.Status.ERROR and cache.expires_at > now:
            cached_error = dict(cache.payload or {})
            raise Gs1LookupError(
                cache.provider_message or "GS1 查询服务暂不可用，请稍后重试。",
                code=cached_error.get("error_code") or "provider_unavailable",
                retry_after=cached_error.get("retry_after"),
            )
        cache.query_code = query_code
        cache.status = Gs1LookupCache.Status.FETCHING
        cache.lease_until = lease_until
        cache.expires_at = lease_until
        cache.save(
            update_fields=[
                "query_code",
                "status",
                "lease_until",
                "expires_at",
                "updated_at",
            ]
        )

    try:
        result = _provider_request(query_code)
    except Gs1LookupError as exc:
        Gs1LookupCache.objects.filter(pk=cache.pk).update(
            status=Gs1LookupCache.Status.ERROR,
            payload={"error_code": exc.code, "retry_after": exc.retry_after},
            provider_message=str(exc)[:200],
            expires_at=timezone.now() + timedelta(seconds=30),
            lease_until=None,
        )
        raise

    data = result["data"]
    now = timezone.now()
    Gs1LookupCache.objects.filter(pk=cache.pk).update(
        status=Gs1LookupCache.Status.SUCCESS,
        found=bool(data.get("found")),
        registered=bool(data.get("registered")),
        payload=data,
        provider_code=0,
        provider_message=str(result.get("msg") or "")[:200],
        provider_request_id=str(result.get("request_id") or "")[:64],
        fetched_at=now,
        expires_at=now + timedelta(hours=24),
        lease_until=None,
    )
    return Gs1LookupCache.objects.get(pk=cache.pk), False
