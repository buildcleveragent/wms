"""PDA endpoints for GS1-assisted, immediate product creation."""

from __future__ import annotations

import logging
from decimal import Decimal

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Q
from rest_framework import serializers, status
from rest_framework.exceptions import ValidationError
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
    lot_no = serializers.CharField(
        max_length=50, allow_blank=True, required=False, default=""
    )
    expiry_control = serializers.BooleanField(default=True)
    expiry_basis = serializers.ChoiceField(
        choices=("MFG", "INBOUND"), required=False, allow_null=True
    )
    shelf_life_days = serializers.IntegerField(
        min_value=1, required=False, allow_null=True
    )
    inbound_valid_days = serializers.IntegerField(
        min_value=1, required=False, allow_null=True
    )
    expiry_warning_days = serializers.IntegerField(
        min_value=1, required=False, allow_null=True
    )
    mfg_date = serializers.DateField(required=False, allow_null=True)
    exp_date = serializers.DateField(required=False, allow_null=True)

    def validate(self, attrs):
        attrs["lot_no"] = (attrs.get("lot_no") or "").strip().upper()
        if attrs["batch_control"] and not attrs["lot_no"]:
            raise serializers.ValidationError(
                {"lot_no": "启用批次管理时必须填写本次批号。"}
            )
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
            raise serializers.ValidationError(
                {"expiry_basis": "启用效期管理时必须选择效期基准。"}
            )
        if basis == "MFG":
            if not attrs.get("shelf_life_days"):
                raise serializers.ValidationError(
                    {"shelf_life_days": "请输入保质期天数。"}
                )
            if not attrs.get("mfg_date"):
                raise serializers.ValidationError({"mfg_date": "请输入本次生产日期。"})
            if not attrs.get("exp_date"):
                raise serializers.ValidationError({"exp_date": "请输入本次到期日期。"})
            attrs["inbound_valid_days"] = None
            limit = attrs["shelf_life_days"]
        else:
            if not attrs.get("inbound_valid_days"):
                raise serializers.ValidationError(
                    {"inbound_valid_days": "请输入入库有效天数。"}
                )
            attrs["shelf_life_days"] = None
            limit = attrs["inbound_valid_days"]
        if (
            attrs.get("mfg_date")
            and attrs.get("exp_date")
            and attrs["exp_date"] < attrs["mfg_date"]
        ):
            raise serializers.ValidationError(
                {"exp_date": "到期日期不得早于生产日期。"}
            )
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


class Gs1LookupApi(APIView):
    permission_classes = [IsAuthenticated, CanReceiveWithoutOrder]

    def post(self, request):
        serializer = LookupInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        owner = require_quick_create_owner(request.user, values["owner_id"])
        try:
            local = find_owner_product(owner.pk, values["barcode"])
        except (ValueError, DjangoValidationError) as exc:
            if isinstance(exc, ValueError):
                raise ValidationError({"barcode": str(exc)}) from exc
            raise _django_validation(exc) from exc
        if local is not None:
            return Response({"source": "local", "product": receive_product_card(local)})
        try:
            cache, cache_hit = get_or_fetch_lookup(values["barcode"])
        except ValueError as exc:
            raise ValidationError({"barcode": str(exc)}) from exc
        except Gs1LookupError as exc:
            http_status = (
                status.HTTP_429_TOO_MANY_REQUESTS
                if exc.code in {"provider_rate_limited", "lookup_in_progress"}
                else status.HTTP_503_SERVICE_UNAVAILABLE
            )
            response = Response(
                {"code": exc.code, "detail": str(exc), "retry_after": exc.retry_after},
                status=http_status,
            )
            if exc.retry_after:
                response["Retry-After"] = str(exc.retry_after)
            return response
        logger.info(
            "inbound.gs1.lookup owner_id=%s canonical_gtin=%s cache_hit=%s found=%s",
            owner.pk,
            cache.canonical_gtin,
            cache_hit,
            cache.found,
        )
        return Response(
            {
                "source": "gs1",
                "cache_hit": cache_hit,
                "candidate": public_candidate(cache),
            }
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
            categories = categories.filter(
                Q(code__icontains=search) | Q(name__icontains=search)
            )
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
            "mfg_date": (
                values["mfg_date"].isoformat() if values.get("mfg_date") else None
            ),
            "exp_date": (
                values["exp_date"].isoformat() if values.get("exp_date") else None
            ),
        }
        return Response(
            {
                "created": created,
                "product": receive_product_card(product),
                "cart_item": cart_item,
            },
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )
