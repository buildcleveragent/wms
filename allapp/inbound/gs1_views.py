"""PDA endpoints for GS1-assisted, immediate product creation."""

from __future__ import annotations

import logging
import re
from decimal import Decimal
from uuid import uuid4

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import OperationalError, ProgrammingError
from django.db.models import Q
from rest_framework import serializers, status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from allapp.products.gs1 import (
    Gs1LookupError,
    get_or_fetch_lookup,
    public_candidate,
)
from allapp.products.models import Product, ProductCategory, ProductUom

from .gs1_services import (
    find_owner_product,
    quick_create_product,
    receive_product_card,
    require_quick_create_owner,
)
from .permissions import CanReceiveWithoutOrder

logger = logging.getLogger(__name__)

REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{8,64}$")

LOOKUP_ERROR_MAP = {
    "provider_not_configured": (
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "GS1_CONFIG_MISSING",
    ),
    "provider_rate_limited": (
        status.HTTP_429_TOO_MANY_REQUESTS,
        "GS1_RATE_LIMITED",
    ),
    "lookup_in_progress": (
        status.HTTP_429_TOO_MANY_REQUESTS,
        "GS1_LOOKUP_IN_PROGRESS",
    ),
    "provider_invalid_response": (status.HTTP_502_BAD_GATEWAY, "GS1_INVALID_RESPONSE"),
    "provider_quota_exhausted": (
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "GS1_QUOTA_EXHAUSTED",
    ),
    "provider_timeout": (status.HTTP_503_SERVICE_UNAVAILABLE, "GS1_TIMEOUT"),
    "provider_network_error": (
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "GS1_NETWORK_ERROR",
    ),
    "provider_unavailable": (
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "GS1_NETWORK_ERROR",
    ),
    "invalid_barcode": (status.HTTP_400_BAD_REQUEST, "GS1_INVALID_BARCODE"),
}


def _lookup_request_id(request):
    existing = getattr(request, "gs1_request_id", "")
    if existing:
        return existing
    supplied = str(request.headers.get("X-Request-ID") or "").strip()
    request_id = supplied if REQUEST_ID_RE.fullmatch(supplied) else str(uuid4())
    request.gs1_request_id = request_id
    return request_id


def _lookup_response(request, *, code, detail, http_status, retry_after=None):
    request_id = _lookup_request_id(request)
    response = Response(
        {
            "code": code,
            "detail": detail,
            "request_id": request_id,
            "retry_after": retry_after,
        },
        status=http_status,
    )
    response["X-Request-ID"] = request_id
    if retry_after:
        response["Retry-After"] = str(retry_after)
    return response


def _with_lookup_request_id(request, response):
    response["X-Request-ID"] = _lookup_request_id(request)
    return response


def _schema_is_missing(exc):
    if getattr(exc, "args", ()) and exc.args[0] == 1146:
        return True
    message = str(exc).lower()
    return "no such table" in message or (
        "doesn't exist" in message
        and ("products_gs1lookupcache" in message or "products_gs1providerratelimit" in message)
    )


class Gs1LookupErrorContractMixin:
    def handle_exception(self, exc):
        if isinstance(exc, (PermissionDenied, DjangoPermissionDenied)):
            return _lookup_response(
                self.request,
                code="GS1_OWNER_FORBIDDEN",
                detail=str(exc) or "账号无权查询该货主。",
                http_status=status.HTTP_403_FORBIDDEN,
            )
        return super().handle_exception(exc)


class LookupInputSerializer(serializers.Serializer):
    owner_id = serializers.IntegerField(min_value=1)
    barcode = serializers.CharField(max_length=32, trim_whitespace=True)


class QuickCreateInputSerializer(serializers.Serializer):
    owner_id = serializers.IntegerField(min_value=1)
    lookup_id = serializers.UUIDField()
    category_id = serializers.IntegerField(min_value=1)
    base_uom_id = serializers.IntegerField(min_value=1)
    quantity = serializers.DecimalField(
        max_digits=18, decimal_places=4, min_value=Decimal("0.0001")
    )
    batch_control = serializers.BooleanField(default=True)
    lot_no = serializers.CharField(max_length=50, allow_blank=True, required=False, default="")
    expiry_control = serializers.BooleanField(default=True)
    expiry_basis = serializers.ChoiceField(
        choices=("MFG", "INBOUND"), required=False, allow_null=True
    )
    shelf_life_days = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    inbound_valid_days = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    expiry_warning_days = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    mfg_date = serializers.DateField(required=False, allow_null=True)
    exp_date = serializers.DateField(required=False, allow_null=True)

    def validate(self, attrs):
        attrs["lot_no"] = (attrs.get("lot_no") or "").strip().upper()
        if attrs["batch_control"] and not attrs["lot_no"]:
            raise serializers.ValidationError({"lot_no": "启用批次管理时必须填写本次批号。"})
        if not attrs["batch_control"]:
            attrs["lot_no"] = ""
        if not attrs["expiry_control"]:
            for field in (
                "expiry_basis",
                "shelf_life_days",
                "inbound_valid_days",
                "expiry_warning_days",
                "mfg_date",
                "exp_date",
            ):
                attrs[field] = None
            return attrs

        basis = attrs.get("expiry_basis")
        if not basis:
            raise serializers.ValidationError({"expiry_basis": "启用效期管理时必须选择效期基准。"})
        if basis == "MFG":
            if not attrs.get("shelf_life_days"):
                raise serializers.ValidationError({"shelf_life_days": "请输入保质期天数。"})
            if not attrs.get("mfg_date"):
                raise serializers.ValidationError({"mfg_date": "请输入本次生产日期。"})
            if not attrs.get("exp_date"):
                raise serializers.ValidationError({"exp_date": "请输入本次到期日期。"})
            attrs["inbound_valid_days"] = None
            limit = attrs["shelf_life_days"]
        else:
            if not attrs.get("inbound_valid_days"):
                raise serializers.ValidationError({"inbound_valid_days": "请输入入库有效天数。"})
            attrs["shelf_life_days"] = None
            limit = attrs["inbound_valid_days"]
        if (
            attrs.get("mfg_date")
            and attrs.get("exp_date")
            and attrs["exp_date"] < attrs["mfg_date"]
        ):
            raise serializers.ValidationError({"exp_date": "到期日期不得早于生产日期。"})
        warning = attrs.get("expiry_warning_days")
        if warning is not None and warning >= limit:
            raise serializers.ValidationError(
                {"expiry_warning_days": "预警天数必须小于保质期或入库有效天数。"}
            )
        return attrs


def _django_validation(exc):
    if hasattr(exc, "message_dict"):
        return ValidationError(exc.message_dict)
    return ValidationError(getattr(exc, "messages", [str(exc)]))


class Gs1LookupApi(Gs1LookupErrorContractMixin, APIView):
    permission_classes = [IsAuthenticated, CanReceiveWithoutOrder]

    def post(self, request):
        serializer = LookupInputSerializer(data=request.data)
        if not serializer.is_valid():
            detail = serializer.errors.get("barcode") or serializer.errors
            if isinstance(detail, (list, tuple)):
                detail = detail[0] if detail else "条码格式无效。"
            return _lookup_response(
                request,
                code="GS1_INVALID_BARCODE",
                detail=str(detail),
                http_status=status.HTTP_400_BAD_REQUEST,
            )
        values = serializer.validated_data
        try:
            owner = require_quick_create_owner(request.user, values["owner_id"])
            local = find_owner_product(owner.pk, values["barcode"])
            if local is not None:
                return _with_lookup_request_id(
                    request,
                    Response({"source": "local", "product": receive_product_card(local)}),
                )
            cache, cache_hit = get_or_fetch_lookup(values["barcode"])
        except ValueError as exc:
            return _lookup_response(
                request,
                code="GS1_INVALID_BARCODE",
                detail=str(exc),
                http_status=status.HTTP_400_BAD_REQUEST,
            )
        except (PermissionDenied, DjangoPermissionDenied) as exc:
            return _lookup_response(
                request,
                code="GS1_OWNER_FORBIDDEN",
                detail=str(exc) or "账号无权查询该货主。",
                http_status=status.HTTP_403_FORBIDDEN,
            )
        except DjangoValidationError as exc:
            converted = _django_validation(exc)
            return _lookup_response(
                request,
                code="GS1_INVALID_BARCODE",
                detail=str(converted.detail),
                http_status=status.HTTP_400_BAD_REQUEST,
            )
        except Gs1LookupError as exc:
            http_status, code = LOOKUP_ERROR_MAP.get(
                exc.code,
                (status.HTTP_503_SERVICE_UNAVAILABLE, "GS1_NETWORK_ERROR"),
            )
            return _lookup_response(
                request,
                code=code,
                detail=str(exc),
                http_status=http_status,
                retry_after=exc.retry_after,
            )
        except (OperationalError, ProgrammingError) as exc:
            if _schema_is_missing(exc):
                return _lookup_response(
                    request,
                    code="GS1_SCHEMA_NOT_READY",
                    detail=(
                        "GS1 查询表尚未初始化，请管理员执行 "
                        "products.0012_gs1_lookup_cache_and_sku_format 数据库迁移。"
                    ),
                    http_status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )
            return self._internal_error(request, values, exc, stage="database")
        except Exception as exc:
            return self._internal_error(request, values, exc, stage="lookup")
        logger.info(
            "inbound.gs1.lookup request_id=%s owner_id=%s canonical_gtin=%s "
            "cache_hit=%s found=%s",
            _lookup_request_id(request),
            owner.pk,
            cache.canonical_gtin,
            cache_hit,
            cache.found,
        )
        return _with_lookup_request_id(
            request,
            Response(
                {
                    "source": "gs1",
                    "cache_hit": cache_hit,
                    "candidate": public_candidate(cache),
                }
            ),
        )

    @staticmethod
    def _internal_error(request, values, exc, *, stage):
        request_id = _lookup_request_id(request)
        logger.exception(
            "inbound.gs1.lookup_failed request_id=%s stage=%s owner_id=%s barcode=%s",
            request_id,
            stage,
            values.get("owner_id"),
            values.get("barcode"),
            exc_info=True,
        )
        return _lookup_response(
            request,
            code="GS1_LOOKUP_INTERNAL_ERROR",
            detail=f"GS1 查询发生未预期错误，请联系管理员并提供错误编号 {request_id}。",
            http_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


class Gs1OptionsApi(APIView):
    permission_classes = [IsAuthenticated, CanReceiveWithoutOrder]

    def get(self, request):
        try:
            owner_id = int(request.query_params.get("owner_id") or 0)
        except (TypeError, ValueError) as exc:
            raise ValidationError({"owner_id": "请输入有效货主。"}) from exc
        require_quick_create_owner(request.user, owner_id)
        search = (request.query_params.get("search") or "").strip()
        categories = ProductCategory.objects.filter(
            is_active=True, is_deleted=False
        ).select_related("parent", "parent__parent")
        uoms = ProductUom.objects.filter(is_active=True, is_deleted=False)
        if search:
            categories = categories.filter(Q(code__icontains=search) | Q(name__icontains=search))
            uoms = uoms.filter(Q(code__icontains=search) | Q(name__icontains=search))
        category_data = [
            {"id": row.pk, "code": row.code, "name": row.name, "label": row.full_path}
            for row in categories.order_by("sort_order", "code")[:100]
            if row.has_active_path()
        ]
        uom_data = [
            {
                "id": row.pk,
                "code": row.code,
                "name": row.name,
                "label": f"{row.name} ({row.code})",
            }
            for row in uoms.order_by("code")[:100]
        ]
        return Response({"categories": category_data, "uoms": uom_data})


class Gs1QuickCreateApi(APIView):
    permission_classes = [IsAuthenticated, CanReceiveWithoutOrder]

    def post(self, request):
        serializer = QuickCreateInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        owner = require_quick_create_owner(request.user, values["owner_id"])
        try:
            product, created = quick_create_product(
                owner=owner,
                lookup_id=values["lookup_id"],
                values=values,
                user=request.user,
                request=request,
            )
        except DjangoValidationError as exc:
            raise _django_validation(exc) from exc
        product = Product.objects.select_related("base_uom").get(pk=product.pk)
        cart_item = {
            "product_id": product.pk,
            "quantity": format(values["quantity"], "f"),
            "lot_no": values.get("lot_no") or "",
            "mfg_date": (values["mfg_date"].isoformat() if values.get("mfg_date") else None),
            "exp_date": (values["exp_date"].isoformat() if values.get("exp_date") else None),
        }
        return Response(
            {
                "created": created,
                "product": receive_product_card(product),
                "cart_item": cart_item,
            },
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )
