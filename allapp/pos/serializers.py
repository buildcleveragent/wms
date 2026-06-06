from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from django.apps import apps
from django.db import transaction
from django.db.models import Sum
from rest_framework import serializers

from allapp.inventory.models import InventoryDetail
from allapp.outbound.models import OutboundOrder, OutboundOrderLine
from allapp.outbound.serializers import OutboundOrderReadSerializer
from allapp.products.models import Product


ZERO = Decimal("0")


def decimal_or_zero(value):
    if value in (None, ""):
        return ZERO
    return Decimal(str(value))


def available_qty_for_product(*, owner_id=None, warehouse_id, product_id):
    queryset = InventoryDetail.objects.filter(
        warehouse_id=warehouse_id,
        product_id=product_id,
        is_active=True,
        available_qty__gt=0,
    )
    if owner_id:
        queryset = queryset.filter(owner_id=owner_id)
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
        return available_qty_for_product(
            owner_id=obj.owner_id,
            warehouse_id=self.context["warehouse_id"],
            product_id=obj.id,
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
        for package in obj.packages.filter(is_active=True).select_related("uom").order_by(
            "sort_order", "id"
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
    qty = serializers.DecimalField(max_digits=18, decimal_places=3, min_value=Decimal("0.001"))
    price = serializers.DecimalField(max_digits=18, decimal_places=4, min_value=Decimal("0.0000"))


class PosCheckoutSerializer(serializers.Serializer):
    customer_id = serializers.IntegerField(required=False, allow_null=True)
    src_bill_no = serializers.CharField(required=False, allow_blank=True, default="")
    remark = serializers.CharField(required=False, allow_blank=True, default="")
    items = PosCheckoutLineSerializer(many=True)

    def _user_scope(self):
        request = self.context["request"]
        user = request.user
        owner_id = getattr(user, "owner_id", None)
        warehouse_id = getattr(user, "warehouse_id", None)
        if not warehouse_id:
            raise serializers.ValidationError(
                "当前用户未绑定仓库(warehouse)，无法收银。"
            )
        return owner_id, warehouse_id

    def validate(self, attrs):
        _, warehouse_id = self._user_scope()
        items = attrs.get("items") or []
        if not items:
            raise serializers.ValidationError({"items": "购物车不能为空。"})

        product_ids = [item["product_id"] for item in items]
        product_queryset = Product.objects.filter(
            id__in=product_ids,
            is_active=True,
        )
        products = {
            product.id: product
            for product in product_queryset
        }
        missing = [product_id for product_id in product_ids if product_id not in products]
        if missing:
            raise serializers.ValidationError(
                {"items": f"商品不存在或已停用：{missing}"}
            )

        owner_ids = sorted({product.owner_id for product in products.values()})

        Customer = apps.get_model("baseinfo", "Customer")
        customer_id = attrs.get("customer_id")
        selected_customer = None
        if customer_id:
            selected_customer = Customer.objects.filter(id=customer_id).first()
            if not selected_customer:
                raise serializers.ValidationError(
                    {"customer_id": "客户不存在。"}
                )

        customers_by_owner = {}
        for owner_id in owner_ids:
            if selected_customer and selected_customer.owner_id == owner_id:
                customers_by_owner[owner_id] = selected_customer
                continue
            customers_by_owner[owner_id], _ = Customer.objects.get_or_create(
                owner_id=owner_id,
                code="CASH",
                defaults={"name": "散客", "salesperson": self.context["request"].user},
            )

        src_bill_no = (attrs.get("src_bill_no") or "").strip()
        if src_bill_no:
            duplicate_owner_ids = [
                owner_id
                for owner_id in owner_ids
                if OutboundOrder.objects.filter(owner_id=owner_id, src_bill_no=src_bill_no).exists()
            ]
            if duplicate_owner_ids:
                raise serializers.ValidationError(
                    {"src_bill_no": "POS 小票号/外部单号已存在。"}
                )
        attrs["src_bill_no"] = src_bill_no
        attrs["remark"] = (attrs.get("remark") or "").strip()

        qty_by_product = {}
        items_by_owner = defaultdict(list)
        for item in items:
            product = products[item["product_id"]]
            qty = Decimal(item["qty"])
            price = Decimal(item["price"])
            qty_by_product[product.id] = qty_by_product.get(product.id, ZERO) + qty
            items_by_owner[product.owner_id].append(item)

            min_price = getattr(product, "min_price", None)
            if min_price is not None and price < Decimal(min_price):
                raise serializers.ValidationError(
                    {"price": f"{product.code} 成交价不能低于最低价 {min_price}。"}
                )

            base_price = getattr(product, "price", None)
            max_discount = getattr(product, "max_discount", None)
            if base_price is not None and max_discount is not None:
                discount_rate = (Decimal("100") - Decimal(max_discount)) / Decimal("100")
                lowest = (Decimal(base_price) * discount_rate).quantize(Decimal("0.0001"))
                if price < lowest:
                    raise serializers.ValidationError(
                        {
                            "price": (
                                f"{product.code} 成交价超过最高折扣限制，"
                                f"最低可售 {lowest}。"
                            )
                        }
                    )

        for product_id, required_qty in qty_by_product.items():
            product = products[product_id]
            available = available_qty_for_product(
                owner_id=product.owner_id,
                warehouse_id=warehouse_id,
                product_id=product_id,
            )
            if available < required_qty:
                raise serializers.ValidationError(
                    {
                        "items": (
                            f"{product.code} 可售库存不足，可售 {available}，"
                            f"需要 {required_qty}。"
                        )
                    }
                )

        attrs["owner_ids__from_products"] = owner_ids
        attrs["warehouse_id__from_user"] = warehouse_id
        attrs["customers_by_owner__objects"] = customers_by_owner
        attrs["items_by_owner__from_products"] = dict(items_by_owner)
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        request = self.context["request"]
        user = request.user
        orders = []
        for owner_id in validated_data["owner_ids__from_products"]:
            order = OutboundOrder.objects.create(
                owner_id=owner_id,
                warehouse_id=validated_data["warehouse_id__from_user"],
                customer=validated_data["customers_by_owner__objects"][owner_id],
                outbound_type="SALES",
                delivery_method="PICKUP",
                submit_status="SUBMITTED",
                src_bill_no=validated_data.get("src_bill_no", ""),
                memo=validated_data.get("remark", ""),
                created_by=user if user and user.is_authenticated else None,
            )
            for item in validated_data["items_by_owner__from_products"][owner_id]:
                OutboundOrderLine.objects.create(
                    order=order,
                    product_id=item["product_id"],
                    base_qty=item["qty"],
                    base_price=item["price"],
                )
            orders.append(order)
        return orders


class PosCheckoutResponseSerializer(OutboundOrderReadSerializer):
    pass
