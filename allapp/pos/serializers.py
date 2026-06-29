from __future__ import annotations

import datetime
from decimal import Decimal

from django.conf import settings
from django.db.models import Sum
from django.utils import timezone
from rest_framework import serializers

from allapp.inventory.models import InventoryDetail
from allapp.outbound.serializers import OutboundOrderReadSerializer
from allapp.products.models import Product

from .models import (
    PosPayment,
    PosPaymentLine,
    PosRefund,
    PosReturn,
    PosReturnLine,
    PosSale,
    PosSaleLine,
)
from .services import build_receipt, create_pos_return, create_pos_sale

ZERO = Decimal("0")


class SafeDateTimeField(serializers.DateTimeField):
    def to_representation(self, value):
        if not value:
            return None
        if isinstance(value, datetime.datetime) and settings.USE_TZ:
            if timezone.is_naive(value):
                value = timezone.make_aware(value, timezone.get_current_timezone())
        return super().to_representation(value)


def decimal_or_zero(value):
    if value in (None, ""):
        return ZERO
    return Decimal(str(value))


def available_qty_for_product(*, owner_id=None, warehouse_id, product_id):
    return available_qty_for_product_in_scope(
        owner_id=owner_id,
        warehouse_id=warehouse_id,
        product_id=product_id,
        zone_type=None,
    )


def available_qty_for_product_in_scope(
    *, owner_id=None, warehouse_id, product_id, zone_type=None
):
    queryset = InventoryDetail.objects.filter(
        warehouse_id=warehouse_id,
        product_id=product_id,
        is_active=True,
        available_qty__gt=0,
    )
    if owner_id:
        queryset = queryset.filter(owner_id=owner_id)
    if zone_type is not None:
        queryset = queryset.filter(zone_type=zone_type)
    result = queryset.aggregate(total=Sum("available_qty"))
    return result["total"] or ZERO


class PosProductSerializer(serializers.ModelSerializer):
    base_unit = serializers.SerializerMethodField()
    price = serializers.SerializerMethodField()
    min_price = serializers.SerializerMethodField()
    max_discount = serializers.SerializerMethodField()
    available_qty = serializers.SerializerMethodField()
    unit_options = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            "id",
            "owner_id",
            "code",
            "sku",
            "name",
            "gtin",
            "unit_barcode",
            "carton_barcode",
            "base_unit",
            "price",
            "min_price",
            "max_discount",
            "available_qty",
            "unit_options",
        ]

    def get_base_unit(self, obj):
        uom = getattr(obj, "base_uom", None)
        if not uom:
            return None
        return {"id": uom.id, "code": uom.code, "name": uom.name}

    def get_price(self, obj):
        return decimal_or_zero(getattr(obj, "price", None))

    def get_min_price(self, obj):
        value = getattr(obj, "min_price", None)
        return None if value in (None, "") else value

    def get_max_discount(self, obj):
        value = getattr(obj, "max_discount", None)
        return None if value in (None, "") else value

    def get_available_qty(self, obj):
        return available_qty_for_product_in_scope(
            owner_id=obj.owner_id,
            warehouse_id=self.context["warehouse_id"],
            product_id=obj.id,
            zone_type=self.context.get("zone_type"),
        )

    def get_unit_options(self, obj):
        options = []
        if obj.base_uom_id:
            options.append(
                {
                    "kind": "base",
                    "package_id": None,
                    "label": obj.base_uom.name or obj.base_uom.code,
                    "multiplier": Decimal("1"),
                    "barcode": obj.unit_barcode or obj.gtin or "",
                }
            )
        for package in (
            obj.packages.filter(is_active=True)
            .select_related("uom")
            .order_by("sort_order", "id")
        ):
            options.append(
                {
                    "kind": "package",
                    "package_id": package.id,
                    "label": package.uom.name if package.uom_id else "",
                    "multiplier": package.qty_in_base,
                    "barcode": package.barcode or "",
                }
            )
        return options


class PosCheckoutLineSerializer(serializers.Serializer):
    product_id = serializers.IntegerField()
    qty = serializers.DecimalField(
        max_digits=18, decimal_places=3, min_value=Decimal("0.001")
    )
    price = serializers.DecimalField(
        max_digits=18, decimal_places=4, min_value=Decimal("0.0000")
    )


class PosPaymentInputSerializer(serializers.Serializer):
    method = serializers.ChoiceField(
        choices=[choice.value for choice in PosPayment.Method]
    )
    amount_received = serializers.DecimalField(
        max_digits=18,
        decimal_places=2,
        min_value=Decimal("0.00"),
        required=False,
    )
    reference_no = serializers.CharField(required=False, allow_blank=True, default="")


class PosPaymentLineInputSerializer(serializers.Serializer):
    method = serializers.ChoiceField(
        choices=[choice.value for choice in PosPayment.Method]
    )
    amount = serializers.DecimalField(
        max_digits=18,
        decimal_places=2,
        min_value=Decimal("0.01"),
    )
    amount_received = serializers.DecimalField(
        max_digits=18,
        decimal_places=2,
        min_value=Decimal("0.00"),
        required=False,
    )
    reference_no = serializers.CharField(required=False, allow_blank=True, default="")


class PosCheckoutSerializer(serializers.Serializer):
    customer_id = serializers.IntegerField(required=False, allow_null=True)
    src_bill_no = serializers.CharField(required=False, allow_blank=True, default="")
    remark = serializers.CharField(required=False, allow_blank=True, default="")
    idempotency_key = serializers.CharField(
        required=False, allow_blank=True, default=""
    )
    stock_zone_type = serializers.IntegerField(required=False, allow_null=True)
    payment = PosPaymentInputSerializer(required=False)
    payments = PosPaymentLineInputSerializer(many=True, required=False, default=list)
    items = PosCheckoutLineSerializer(many=True)

    def validate(self, attrs):
        if not attrs.get("items"):
            raise serializers.ValidationError({"items": "购物车不能为空。"})
        if not attrs.get("payment") and not attrs.get("payments"):
            raise serializers.ValidationError({"payment": "请填写支付信息。"})
        return attrs

    def create(self, validated_data):
        return create_pos_sale(
            user=self.context["request"].user,
            customer_id=validated_data.get("customer_id"),
            src_bill_no=validated_data.get("src_bill_no", ""),
            remark=validated_data.get("remark", ""),
            items=validated_data.get("items", []),
            payment=validated_data.get("payment"),
            payments=validated_data.get("payments", []),
            idempotency_key=validated_data.get("idempotency_key", ""),
            stock_zone_type=validated_data.get("stock_zone_type"),
        )


class PosPaymentReadSerializer(serializers.ModelSerializer):
    created_at = SafeDateTimeField(read_only=True)

    class Meta:
        model = PosPayment
        fields = [
            "id",
            "method",
            "amount_due",
            "amount_received",
            "change_amount",
            "reference_no",
            "status",
            "created_at",
        ]


class PosPaymentLineReadSerializer(serializers.ModelSerializer):
    created_at = SafeDateTimeField(read_only=True)

    class Meta:
        model = PosPaymentLine
        fields = [
            "id",
            "method",
            "amount",
            "amount_received",
            "change_amount",
            "reference_no",
            "status",
            "created_at",
        ]


class PosSaleLineReadSerializer(serializers.ModelSerializer):
    product_code = serializers.CharField(source="product.code", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    returned_qty = serializers.SerializerMethodField()
    returnable_qty = serializers.SerializerMethodField()

    class Meta:
        model = PosSaleLine
        fields = [
            "id",
            "line_no",
            "owner_id",
            "product_id",
            "product_code",
            "product_name",
            "qty",
            "price",
            "amount",
            "returned_qty",
            "returnable_qty",
            "outbound_order_line_id",
        ]

    def get_returned_qty(self, obj):
        returned = getattr(obj, "_pos_returned_qty", None)
        if returned is None:
            returned = sum(
                (
                    line.qty
                    for line in obj.return_lines.filter(
                        return_order__status=PosReturn.Status.COMPLETED
                    )
                ),
                Decimal("0.000"),
            )
        return returned

    def get_returnable_qty(self, obj):
        returned = Decimal(str(self.get_returned_qty(obj) or 0))
        return max(obj.qty - returned, Decimal("0.000"))


class PosSaleReadSerializer(serializers.ModelSerializer):
    payment = PosPaymentReadSerializer(read_only=True)
    payment_lines = PosPaymentLineReadSerializer(many=True, read_only=True)
    lines = PosSaleLineReadSerializer(many=True, read_only=True)
    receipt = serializers.SerializerMethodField()
    orders = serializers.SerializerMethodField()
    created_at = SafeDateTimeField(read_only=True)
    voided_at = SafeDateTimeField(read_only=True)

    class Meta:
        model = PosSale
        fields = [
            "id",
            "sale_no",
            "src_bill_no",
            "warehouse_id",
            "cashier_id",
            "shift_id",
            "selected_customer_id",
            "status",
            "total_amount",
            "remark",
            "voided_at",
            "voided_by_id",
            "void_reason",
            "created_at",
            "payment",
            "payment_lines",
            "lines",
            "orders",
            "receipt",
        ]

    def get_receipt(self, obj):
        return build_receipt(obj)

    def get_orders(self, obj):
        orders = [
            link.outbound_order
            for link in obj.sale_orders.select_related("outbound_order").order_by(
                "owner_id"
            )
        ]
        return PosCheckoutResponseSerializer(
            orders,
            many=True,
            context=self.context,
        ).data


class PosShiftOpenSerializer(serializers.Serializer):
    opening_cash_amount = serializers.DecimalField(
        max_digits=18,
        decimal_places=2,
        min_value=Decimal("0.00"),
        required=False,
        default=Decimal("0.00"),
    )
    remark = serializers.CharField(required=False, allow_blank=True, default="")


class PosShiftClosePaymentSerializer(serializers.Serializer):
    method = serializers.ChoiceField(
        choices=[choice.value for choice in PosPayment.Method]
    )
    actual_amount = serializers.DecimalField(
        max_digits=18,
        decimal_places=2,
        required=False,
    )


class PosShiftCloseSerializer(serializers.Serializer):
    actual_cash_amount = serializers.DecimalField(
        max_digits=18,
        decimal_places=2,
        required=False,
    )
    payments = PosShiftClosePaymentSerializer(many=True, required=False, default=list)
    remark = serializers.CharField(required=False, allow_blank=True, default="")


class PosShiftReopenSerializer(serializers.Serializer):
    reason = serializers.CharField(required=True, allow_blank=False, max_length=200)


class PosRefundInputSerializer(serializers.Serializer):
    method = serializers.ChoiceField(
        choices=[choice.value for choice in PosPayment.Method]
    )
    amount = serializers.DecimalField(
        max_digits=18,
        decimal_places=2,
        min_value=Decimal("0.01"),
    )
    reference_no = serializers.CharField(required=False, allow_blank=True, default="")
    status = serializers.ChoiceField(
        choices=[choice.value for choice in PosRefund.Status],
        required=False,
        default=PosRefund.Status.REFUNDED,
    )


class PosReturnLineInputSerializer(serializers.Serializer):
    sale_line_id = serializers.IntegerField()
    qty = serializers.DecimalField(
        max_digits=18, decimal_places=3, min_value=Decimal("0.001")
    )


class PosReturnCreateSerializer(serializers.Serializer):
    sale_id = serializers.IntegerField()
    reason = serializers.CharField(required=True, allow_blank=False, max_length=200)
    idempotency_key = serializers.CharField(
        required=False, allow_blank=True, default=""
    )
    lines = PosReturnLineInputSerializer(many=True)
    refunds = PosRefundInputSerializer(many=True)

    def validate(self, attrs):
        if not attrs.get("lines"):
            raise serializers.ValidationError({"lines": "退货明细不能为空。"})
        if not attrs.get("refunds"):
            raise serializers.ValidationError({"refunds": "退款明细不能为空。"})
        return attrs

    def create(self, validated_data):
        return create_pos_return(
            user=self.context["request"].user,
            sale_id=validated_data["sale_id"],
            lines=validated_data.get("lines", []),
            refunds=validated_data.get("refunds", []),
            reason=validated_data.get("reason", ""),
            idempotency_key=validated_data.get("idempotency_key", ""),
        )


class PosRefundReadSerializer(serializers.ModelSerializer):
    processed_at = SafeDateTimeField(read_only=True)
    created_at = SafeDateTimeField(read_only=True)

    class Meta:
        model = PosRefund
        fields = [
            "id",
            "method",
            "amount",
            "reference_no",
            "status",
            "processed_by_id",
            "processed_at",
            "created_at",
        ]


class PosReturnLineReadSerializer(serializers.ModelSerializer):
    product_code = serializers.CharField(source="product.code", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    created_at = SafeDateTimeField(read_only=True)

    class Meta:
        model = PosReturnLine
        fields = [
            "id",
            "line_no",
            "sale_line_id",
            "owner_id",
            "product_id",
            "product_code",
            "product_name",
            "qty",
            "price",
            "amount",
            "created_at",
        ]


class PosReturnReadSerializer(serializers.ModelSerializer):
    lines = PosReturnLineReadSerializer(many=True, read_only=True)
    refunds = PosRefundReadSerializer(many=True, read_only=True)
    created_at = SafeDateTimeField(read_only=True)

    class Meta:
        model = PosReturn
        fields = [
            "id",
            "return_no",
            "sale_id",
            "warehouse_id",
            "shift_id",
            "cashier_id",
            "status",
            "total_amount",
            "reason",
            "created_at",
            "lines",
            "refunds",
        ]


def serialize_checkout_result(result, request):
    orders = result["orders"]
    sale = result["sale"]
    payment = result["payment"]
    return {
        "sale": PosSaleReadSerializer(sale, context={"request": request}).data,
        "payment": PosPaymentReadSerializer(payment).data,
        "payment_lines": PosPaymentLineReadSerializer(
            sale.payment_lines.order_by("id"), many=True
        ).data,
        "orders": PosCheckoutResponseSerializer(
            orders,
            many=True,
            context={"request": request},
        ).data,
        "order_count": len(orders),
        "src_bill_no": sale.src_bill_no,
        "receipt": result["receipt"],
    }


def serialize_return_result(result):
    return_order = result["return"]
    return {"return": PosReturnReadSerializer(return_order).data}


class PosCheckoutResponseSerializer(OutboundOrderReadSerializer):
    created_at = SafeDateTimeField(read_only=True)
    etd = SafeDateTimeField(read_only=True)
    priced_at = SafeDateTimeField(read_only=True)
