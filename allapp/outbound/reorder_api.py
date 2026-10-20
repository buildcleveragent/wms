"""Strictly scoped owner purchasing assistance and audited address management."""

import re

from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from allapp.accounts.audit import record_audit_event
from allapp.baseinfo.models import OwnerCustomerAddress
from allapp.outbound.reorder import (
    SHANGHAI,
    CheckoutConflict,
    frequent_products,
    owner_scope,
    scoped_binding,
    validate_submission,
)
from allapp.outbound.serializers import OutboundOrderLineCreateSerializer


class CheckoutInput(serializers.Serializer):
    warehouse_id = serializers.IntegerField(min_value=1)
    customer_id = serializers.IntegerField(min_value=1)
    reorder_source_id = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    items = OutboundOrderLineCreateSerializer(many=True, allow_empty=False)

    def validate_items(self, items):
        keys = [item["line_key"] for item in items if item.get("line_key")]
        if len(keys) != len(set(keys)):
            raise serializers.ValidationError("商品行标识重复，请重新加载订单。")
        return items


class PurchaseView(APIView):
    permission_classes = [IsAuthenticated]

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        return response


class CheckoutValidationView(PurchaseView):
    def post(self, request):
        owner_scope(request.user, buyer=True)
        inputs = CheckoutInput(data=request.data)
        inputs.is_valid(raise_exception=True)
        return Response(
            validate_submission(
                request.user, inputs.validated_data, request=request, raise_error=False
            )
        )


class FrequentInput(serializers.Serializer):
    warehouse_id = serializers.IntegerField(min_value=1)
    search = serializers.CharField(required=False, default="", allow_blank=True, max_length=100)
    page = serializers.IntegerField(default=1, min_value=1)
    page_size = serializers.IntegerField(default=20, min_value=1, max_value=100)


class FrequentProductsView(PurchaseView):
    def get(self, request, customer_id):
        inputs = FrequentInput(data=request.query_params)
        inputs.is_valid(raise_exception=True)
        return Response(
            frequent_products(request.user, customer_id, request=request, **inputs.validated_data)
        )


class AddressInput(serializers.Serializer):
    contact = serializers.CharField(max_length=80)
    phone = serializers.CharField(max_length=40)
    address = serializers.CharField(max_length=200)
    is_default = serializers.BooleanField(default=False)
    version = serializers.IntegerField(min_value=1, required=False)

    def validate_phone(self, value):
        if len(value) < 6 or not any(c.isdigit() for c in value):
            raise serializers.ValidationError("联系电话格式不正确。")
        return value


def address_rows(binding, user):
    qs = OwnerCustomerAddress.objects.filter(binding=binding, is_active=True)
    # Ordinary addresses are never exposed as CASH addresses after a binding
    # code change (and former CASH private addresses never become shared).
    if binding.code.upper() == "CASH":
        return qs.filter(private_user=user)
    return qs.filter(private_user__isnull=True)


def timestamp(value):
    if timezone.is_naive(value):
        value = value.replace(tzinfo=SHANGHAI)
    return value.astimezone(SHANGHAI).isoformat()


def address_payload(address):
    return {
        "id": address.pk,
        "contact": address.contact,
        "phone": address.phone,
        "address": address.address,
        "is_default": address.is_default,
        "version": address.version,
        "updated_at": timestamp(address.updated_at),
    }


def address_binding(request, customer_id, *, lock=False):
    binding = scoped_binding(request.user, customer_id, lock=lock)
    if binding.code.upper() == "CASH":
        # Managers do not receive a directory of cash buyers' personal addresses.
        owner_scope(request.user, buyer=True)
    return binding


def set_default(binding, user, address):
    """Binding lock serializes both empty-book creation and default changes."""
    if not address.is_default:
        return
    others = address_rows(binding, user).filter(is_default=True).exclude(pk=address.pk)
    # A default switch changes those rows' versions too, preventing stale PATCH.
    from django.db.models import F

    others.update(
        is_default=False, version=F("version") + 1, updated_at=timezone.now(), updated_by=user
    )


class CustomerAddressesView(PurchaseView):
    def get(self, request, customer_id):
        binding = address_binding(request, customer_id)
        rows = address_rows(binding, request.user).order_by("-is_default", "-id")
        return Response(
            {
                "customer": {
                    "id": binding.customer_id,
                    "name": binding.customer.name,
                    "code": binding.code,
                },
                "results": [address_payload(a) for a in rows],
                "private": binding.code.upper() == "CASH",
            }
        )

    @transaction.atomic
    def post(self, request, customer_id):
        binding = address_binding(request, customer_id, lock=True)
        inputs = AddressInput(data=request.data)
        inputs.is_valid(raise_exception=True)
        data = dict(inputs.validated_data)
        data.pop("version", None)
        key = request.headers.get("Idempotency-Key", "")
        if not re.fullmatch(r"[A-Za-z0-9._:-]{8,64}", key):
            raise ValidationError({"detail": "请提供有效的地址保存请求标识。"})
        existing = OwnerCustomerAddress.all_objects.filter(binding=binding, request_key=key).first()
        if existing:
            if (
                existing.created_by_id != request.user.pk
                or existing.is_deleted
                or any(getattr(existing, field) != value for field, value in data.items())
            ):
                raise CheckoutConflict("地址保存内容已变化，请刷新后重试。")
            return Response(address_payload(existing))
        address = OwnerCustomerAddress(
            binding=binding,
            private_user=request.user if binding.code.upper() == "CASH" else None,
            created_by=request.user,
            updated_by=request.user,
            request_key=key,
            **data,
        )
        before = [address_payload(a) for a in address_rows(binding, request.user)]
        address.save()
        set_default(binding, request.user, address)
        record_audit_event(
            action="outbound.customer_address.create",
            module="outbound",
            request=request,
            obj=address,
            owner_id=binding.owner_id,
            metadata={"binding_id": binding.pk, "customer_id": binding.customer_id},
            before={"addresses": before},
            after={
                "address": address_payload(address),
                "addresses": [address_payload(a) for a in address_rows(binding, request.user)],
            },
        )
        return Response(address_payload(address), status=201)


class CustomerAddressView(PurchaseView):
    @transaction.atomic
    def patch(self, request, customer_id, address_id):
        return self.change(request, customer_id, address_id)

    @transaction.atomic
    def delete(self, request, customer_id, address_id):
        return self.change(request, customer_id, address_id, remove=True)

    def change(self, request, customer_id, address_id, remove=False):
        binding = address_binding(request, customer_id, lock=True)
        address = get_object_or_404(address_rows(binding, request.user), pk=address_id)
        inputs = AddressInput(data=request.data, partial=True)
        inputs.is_valid(raise_exception=True)
        data = dict(inputs.validated_data)
        if "version" not in data:
            raise ValidationError({"version": "缺少地址版本，请重新加载。"})
        if data.pop("version") != address.version:
            raise CheckoutConflict("地址已被修改，请重新加载后再保存。")
        before = [address_payload(a) for a in address_rows(binding, request.user)]
        if remove:
            address.is_active = False
            address.is_deleted = True
            address.is_default = False
            address.deleted_by = request.user
            address.deleted_at = timezone.now()
        else:
            for field, value in data.items():
                setattr(address, field, value)
        address.version += 1
        address.updated_by = request.user
        address.save()
        set_default(binding, request.user, address)
        record_audit_event(
            action=f"outbound.customer_address.{'delete' if remove else 'update'}",
            module="outbound",
            request=request,
            obj=address,
            owner_id=binding.owner_id,
            metadata={"binding_id": binding.pk, "customer_id": binding.customer_id},
            before={"addresses": before},
            after={
                "address": address_payload(address),
                "addresses": [address_payload(a) for a in address_rows(binding, request.user)],
            },
        )
        return Response(status=204) if remove else Response(address_payload(address))
