import logging
from datetime import date
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.apps import apps
from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.utils.dateparse import parse_datetime
from rest_framework import serializers

from allapp.accounts.access import AccessScope
from allapp.accounts.models import UserRoleScope
from allapp.baseinfo.customer_access import customer_binding, customers_for_owner
from allapp.outbound.enums import PricingStatus
from allapp.outbound.services import can_edit_standard_draft, get_default_product_price
from allapp.outbound.warehouse_access import owner_can_use_warehouse
from allapp.products.pricing import InvalidSalePriceRule

from .amounts import line_amount
from .models import OutboundOrder, OutboundOrderLine

logger = logging.getLogger(__name__)

# 兼容不同字段命名的小工具
def _get(obj, names, default=None):
    for n in names:
        if hasattr(obj, n):
            v = getattr(obj, n)
            return v() if callable(v) else v
    return default

class ConfirmPricingLineSerializer(serializers.Serializer):
    line_id = serializers.IntegerField()
    base_price = serializers.DecimalField(
        max_digits=18,
        decimal_places=4,
        min_value=Decimal("0.0000"),
    )


class ConfirmPricingSerializer(serializers.Serializer):
    lines = ConfirmPricingLineSerializer(many=True)

    def validate(self, attrs):
        order: OutboundOrder = self.context["order"]
        lines_payload = attrs["lines"]

        if order.pricing_status == PricingStatus.CONFIRMED:
            raise serializers.ValidationError("该订单已确认价格，不能重复确认。")

        db_lines = {
            line.id: line
            for line in OutboundOrderLine.objects.filter(order=order, is_deleted=False)
        }
        if not db_lines:
            raise serializers.ValidationError("订单没有可定价的明细行。")

        payload_ids = [item["line_id"] for item in lines_payload]
        if len(payload_ids) != len(set(payload_ids)):
            raise serializers.ValidationError("请求中存在重复的 line_id。")

        for item in lines_payload:
            line = db_lines.get(item["line_id"])
            if not line:
                raise serializers.ValidationError(
                    f"订单行 {item['line_id']} 不存在或不属于当前订单。"
                )

        missing_ids = set(db_lines.keys()) - set(payload_ids)
        if missing_ids:
            raise serializers.ValidationError(
                f"还有订单行未定价：{sorted(missing_ids)}"
            )

        return attrs


# 给现有 OutboundOrderReadSerializer 增加如下字段
# 如果你当前 serializer 名字不是这个，请合并到实际详情 serializer 里

# priced_by_name = serializers.SerializerMethodField()

# class Meta.fields 里补：
# "pricing_status",
# "priced_at",
# "priced_by",
# "priced_by_name",
# "final_order_amount",

# 建议实现：
# def get_priced_by_name(self, obj):
#     u = getattr(obj, "priced_by", None)
#     if not u:
#         return ""
#     return getattr(u, "name", None) or getattr(u, "username", None) or ""


# ---------- 创建用 ----------
class OutboundOrderLineCreateSerializer(serializers.Serializer):
    is_priced = serializers.BooleanField(read_only=True)
    product_id = serializers.IntegerField()
    line_key = serializers.CharField(required=False, allow_blank=True, max_length=100)
    base_uom_id = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    catalog_version = serializers.CharField(required=False, allow_blank=True, max_length=64)
    unit_unverified = serializers.BooleanField(required=False, default=False)
    uom_id     = serializers.IntegerField(required=False, allow_null=True)  # 如需包装下单可使用
    qty = serializers.DecimalField(
        max_digits=14,
        decimal_places=3,
        min_value=Decimal("0.001"),
    )  # 约定为“基本单位数量”
    # price      = serializers.DecimalField(max_digits=18, decimal_places=4)  # 约定为“基本单位单价”
    price      = serializers.DecimalField(
        max_digits=14,
        decimal_places=4,
        min_value=Decimal("0"),
        required=False,
        allow_null=True,
    )


class AssistedOutboundLineSerializer(serializers.Serializer):
    transfer_price = serializers.DecimalField(
        max_digits=18, decimal_places=4, min_value=Decimal("0"), required=False, allow_null=True
    )
    product_id = serializers.IntegerField(min_value=1)
    qty = serializers.DecimalField(
        max_digits=18,
        decimal_places=3,
        min_value=Decimal("0.001"),
    )
    package_id = serializers.IntegerField(
        min_value=1,
        required=False,
        allow_null=True,
    )
    package_qty = serializers.DecimalField(
        max_digits=18,
        decimal_places=3,
        min_value=Decimal("0.001"),
        required=False,
        allow_null=True,
    )
    price = serializers.DecimalField(
        max_digits=18,
        decimal_places=4,
        min_value=Decimal("0"),
        required=False,
        allow_null=True,
    )


class AssistedOutboundOrderCreateSerializer(serializers.Serializer):
    """Strict input contract for a warehouse-created assisted SALES order."""

    request_id = serializers.UUIDField()
    owner_id = serializers.IntegerField(min_value=1)
    new_owner_id = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    customer_id = serializers.IntegerField(min_value=1)
    items = AssistedOutboundLineSerializer(many=True, allow_empty=False)
    src_bill_no = serializers.CharField(required=False, allow_blank=True, max_length=100)
    delivery_method = serializers.ChoiceField(
        choices=OutboundOrder.DELIVERY_METHOD_CHOICES,
        required=False,
        allow_null=True,
    )
    etd = serializers.DateTimeField(required=False, allow_null=True)
    contact = serializers.CharField(required=False, allow_blank=True, max_length=80)
    contact_phone = serializers.CharField(required=False, allow_blank=True, max_length=40)
    ship_to = serializers.CharField(required=False, allow_blank=True, max_length=200)
    remark = serializers.CharField(required=False, allow_blank=True, max_length=100, default="")
    assistance_reason = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=200,
        default="",
    )

    def validate(self, data):
        Owner = apps.get_model("baseinfo", "Owner")
        Product = apps.get_model("products", "Product")
        ProductPackage = apps.get_model("products", "ProductPackage")

        owner = Owner.objects.filter(
            pk=data["owner_id"],
            is_active=True,
            allow_warehouse_assisted_outbound=True,
        ).first()
        if owner is None:
            raise serializers.ValidationError(
                {"owner_id": "货主不存在、未启用，或未授权仓库代办出库。"}
            )

        new_owner = None
        if data.get("new_owner_id"):
            if data["new_owner_id"] == owner.pk:
                raise serializers.ValidationError({"new_owner_id": "新货主不能与原货主相同。"})
            new_owner = Owner.objects.filter(
                pk=data["new_owner_id"], is_active=True, allow_warehouse_assisted_outbound=True
            ).first()
            if new_owner is None:
                raise serializers.ValidationError(
                    {"new_owner_id": "新货主不存在、未启用或未授权代办。"}
                )
        sales_owner = new_owner or owner
        customer = customers_for_owner(sales_owner.pk).filter(pk=data["customer_id"]).first()
        if customer is None:
            raise serializers.ValidationError(
                {"customer_id": "客户不存在、未启用，或不属于所选货主。"}
            )

        product_ids = [item["product_id"] for item in data["items"]]
        if len(product_ids) != len(set(product_ids)):
            raise serializers.ValidationError({"items": "同一商品不能重复提交。"})
        products = {
            product.id: product
            for product in Product.objects.filter(
                id__in=product_ids,
                owner_id=owner.id,
                is_active=True,
            ).select_related("base_uom")
        }
        missing = sorted(set(product_ids) - set(products))
        if missing:
            raise serializers.ValidationError(
                {"items": f"商品不存在、未启用，或不属于所选货主：{missing}"}
            )

        package_ids = {
            item["package_id"]
            for item in data["items"]
            if item.get("package_id") is not None
        }
        packages = {
            package.id: package
            for package in ProductPackage.objects.filter(
                id__in=package_ids,
                is_active=True,
                uom__is_active=True,
            ).select_related("uom")
        }

        for item in data["items"]:
            product = products[item["product_id"]]
            package_id = item.get("package_id")
            package_qty = item.get("package_qty")
            if (package_id is None) != (package_qty is None):
                raise serializers.ValidationError(
                    {"items": f"商品 {product.name} 的包装和包装数量必须同时提供。"}
                )
            package = None
            if package_id is not None:
                package = packages.get(package_id)
                if package is None or package.product_id != product.id:
                    raise serializers.ValidationError(
                        {"items": f"所选包装不存在、未启用，或不属于商品 {product.name}。"}
                    )
                expected_base_qty = (
                    package_qty * Decimal(package.qty_in_base)
                ).quantize(Decimal("0.001"))
                if item["qty"] != expected_base_qty:
                    raise serializers.ValidationError(
                        {
                            "items": (
                                f"商品 {product.name} 的包装数量换算不一致："
                                f"{package_qty} {package.uom.name} 应为 "
                                f"{expected_base_qty} {product.base_uom.name}。"
                            )
                        }
                    )
                item["qty"] = expected_base_qty
            item["package"] = package
            item["package_qty"] = package_qty
            supplied_price = item.get("price")
            price = (
                supplied_price
                if supplied_price is not None
                else get_default_product_price(product)
            )
            item["product"] = product
            # 仓库代办出库只负责货物流转：已知价格可由操作员填写，未填则
            # 使用服务端默认价；货主未提供售价时允许以零价创建订单行。
            item["price"] = price if price > 0 else Decimal("0")
            if not new_owner and item.get("transfer_price") is not None:
                raise serializers.ValidationError({"items": "转货单价仅用于填写了新货主的订单。"})
            if item.get("transfer_price") is None:
                item["transfer_price"] = item["price"]

        for key in ("src_bill_no", "contact", "contact_phone", "ship_to"):
            data[key] = (data.get(key) or "").strip()

        if data["src_bill_no"]:
            existing = OutboundOrder.objects.filter(
                owner_id=sales_owner.id,
                src_bill_no=data["src_bill_no"],
            ).first()
            if existing:
                raise serializers.ValidationError(
                    {
                        "src_bill_no": f"平台单号重复，已存在订单 {existing.order_no}",
                        "existing_order_id": str(existing.id),
                    }
                )

        if (customer.owner_customer_code or "").strip().upper() == "CASH":
            if not data["contact"]:
                raise serializers.ValidationError({"contact": "散客代发必须填写收件人。"})
            if not data["contact_phone"]:
                raise serializers.ValidationError({"contact_phone": "散客代发必须填写联系电话。"})
            if not data["ship_to"]:
                raise serializers.ValidationError({"ship_to": "散客代发必须填写收货地址。"})
            if len(data["contact_phone"]) < 6 or not any(
                char.isdigit() for char in data["contact_phone"]
            ):
                raise serializers.ValidationError({"contact_phone": "联系电话格式不正确。"})

        data["owner"] = owner
        data["new_owner"] = new_owner
        data["customer"] = customer
        return data
# class OutboundOrderCreateSerializer(serializers.Serializer):
#     # 不再接收 owner_id / warehouse_id
#     customer_id     = serializers.IntegerField(required=False, allow_null=True)
#     supplier_id     = serializers.IntegerField(required=False, allow_null=True)
#     outbound_type   = serializers.CharField(required=False, default="SALES")
#     delivery_method = serializers.CharField(required=False, allow_null=True)
#     etd             = serializers.DateTimeField(required=False, allow_null=True)
#     remark          = serializers.CharField(required=False, allow_blank=True, default="")
#     items           = OutboundOrderLineCreateSerializer(many=True)
#
#     src_bill_no = serializers.CharField(required=False, allow_blank=True)
#     contact = serializers.CharField(required=False, allow_blank=True)
#     contact_phone = serializers.CharField(required=False, allow_blank=True)
#     ship_to = serializers.CharField(required=False, allow_blank=True)
#
#     # ---------- 内部辅助 ----------
#     def _allowed(self, model_field_name):
#         OutboundOrder = apps.get_model("outbound", "OutboundOrder")
#         try:
#             f = OutboundOrder._meta.get_field(model_field_name)
#             return [c[0] for c in (f.choices or [])]
#         except Exception:
#             return None
#
#     def _assert_customer_belongs_to_owner(self, customer_id, owner_id):
#         if not customer_id:
#             return
#         Customer = apps.get_model("baseinfo", "Customer")
#         c = Customer.objects.only("id", "owner_id").get(pk=customer_id)
#         if c.owner_id != owner_id:
#             raise serializers.ValidationError("客户不属于当前用户的货主，禁止下单。")
#
#     def _assert_products_belong_to_owner(self, items, owner_id):
#         Product = apps.get_model("products", "Product")
#         pid_list = [it["product_id"] for it in items if it.get("product_id")]
#         if not pid_list:
#             return
#         owners = dict(Product.objects.filter(id__in=pid_list).values_list("id", "owner_id"))
#         bad = [pid for pid in pid_list if owners.get(pid) != owner_id]
#         if bad:
#             raise serializers.ValidationError(f"存在不属于当前货主的商品：{bad}")
#
#     # ---------- 校验 ----------
#     def validate(self, data):
#         if not data.get("items"):
#             raise serializers.ValidationError("至少需要一条明细。")
#
#         # 从登录用户获取 owner / warehouse（不再走前端）
#         req = self.context.get("request")
#         user = getattr(req, "user", None)
#         owner_id 和 warehouse_id 应从显式 AccessScope 解析。
#         if not owner_id:
#             raise serializers.ValidationError("当前用户未绑定货主（owner），请联系管理员。")
#         if not warehouse_id:
#             raise serializers.ValidationError("当前用户未绑定仓库（warehouse），请联系管理员。")
#
#         # 出库类型 / 配送方式 choices 校验（若模型定义了）
#         ot = data.get("outbound_type", "SALES")
#         allowed_ot = self._allowed("outbound_type")
#         if allowed_ot and ot not in allowed_ot:
#             raise serializers.ValidationError(f"不支持的出库类型：{ot}")
#
#         dm = data.get("delivery_method")
#         allowed_dm = self._allowed("delivery_method")
#         if dm and allowed_dm and dm not in allowed_dm:
#             raise serializers.ValidationError(f"不支持的配送方式：{dm}")
#
#         # 退供 vs 销售 的客户/供应商约束
#         if ot == "SUPPLIER_RETURN":
#             if not data.get("supplier_id") or data.get("customer_id"):
#                 raise serializers.ValidationError("退供单必须提供 supplier_id 且 customer_id 为空。")
#         else:
#             if not data.get("customer_id") or data.get("supplier_id"):
#                 raise serializers.ValidationError("非退供出库单必须提供 customer_id 且 supplier_id 为空。")
#
#         # 一致性：客户、商品均需属于当前用户的 owner
#         self._assert_customer_belongs_to_owner(data.get("customer_id"), owner_id)
#         self._assert_products_belong_to_owner(data["items"], owner_id)
#
#         # 把后端推断出的 owner/warehouse 放入 validated_data，供 create 使用
#         data["owner_id__from_user"] = owner_id
#         data["warehouse_id__from_user"] = warehouse_id
#         return data
#
#     # ---------- 创建 ----------
#     def create(self, validated):
#         logger.debug("%s.create items=%d customer_id=%s",
#                      self.__class__.__name__, len(validated.get("items", [])),
#                      validated.get("customer_id"))
#         OutboundOrder     = apps.get_model("outbound", "OutboundOrder")
#         OutboundOrderLine = apps.get_model("outbound", "OutboundOrderLine")
#
#         req  = self.context.get("request")
#         user = getattr(req, "user", None)
#
#         owner_id     = validated["owner_id__from_user"]
#         warehouse_id = validated["warehouse_id__from_user"]
#
#         logger.debug(
#             "Create OutboundOrder owner_id=%s warehouse_id=%s customer_id=%s items=%s",
#             owner_id, warehouse_id, validated.get("customer_id"), len(validated.get("items", []))
#         )
#
#         order = OutboundOrder.objects.create(
#             owner_id      = owner_id,
#             customer_id   = validated.get("customer_id"),
#             supplier_id   = validated.get("supplier_id"),
#             warehouse_id  = warehouse_id,
#             outbound_type = validated.get("outbound_type", "SALES"),
#             delivery_method = validated.get("delivery_method"),
#             etd           = validated.get("etd"),
#             memo          = validated.get("remark", ""),
#             created_by    = user if (user and user.is_authenticated) else None,
#             biz_date      = date.today(),
#             submit_status="SUBMITTED",
#         )
#
#         for it in validated["items"]:
#             OutboundOrderLine.objects.create(
#                 order      = order,
#                 product_id = it["product_id"],
#                 base_qty   = it["qty"],
#                 base_price = it["price"],
#                 # 如需包装下单：可额外写入 aux_uom_id / aux_qty / aux_price
#             )
#
#         return order

class OutboundOrderCreateSerializer(serializers.Serializer):
    # owner 永远从显式货主角色范围解析；warehouse 可由货主业务员选择。
    warehouse_id = serializers.IntegerField(
        min_value=1,
        required=True,
        error_messages={"required": "请选择出库仓库。"},
    )
    customer_id     = serializers.IntegerField(required=False, allow_null=True)
    supplier_id     = serializers.IntegerField(required=False, allow_null=True)
    outbound_type   = serializers.CharField(required=False, default="SALES")
    delivery_method = serializers.CharField(required=False, allow_null=True)
    etd             = serializers.DateTimeField(required=False, allow_null=True)
    remark          = serializers.CharField(required=False, allow_blank=True, default="")
    items           = OutboundOrderLineCreateSerializer(many=True)
    reorder_source_id = serializers.IntegerField(required=False, allow_null=True, min_value=1)

    # 新增：一件代发/收件信息
    src_bill_no   = serializers.CharField(required=False, allow_blank=True)
    contact       = serializers.CharField(required=False, allow_blank=True)
    contact_phone = serializers.CharField(required=False, allow_blank=True)
    ship_to       = serializers.CharField(required=False, allow_blank=True)

    # ---------- 内部辅助 ----------
    def _allowed(self, model_field_name):
        OutboundOrder = apps.get_model("outbound", "OutboundOrder")
        try:
            f = OutboundOrder._meta.get_field(model_field_name)
            return [c[0] for c in (f.choices or [])]
        except Exception:
            return None

    def _get_customer(self, customer_id):
        if not customer_id:
            return None
        Customer = apps.get_model("baseinfo", "Customer")
        customer = (
            Customer.objects.only(
                "id", "owner_id", "salesperson_id", "code", "name", "is_active"
            )
            .filter(pk=customer_id, is_active=True)
            .first()
        )
        if customer is None:
            self._raise_customer_access_error()
        return customer

    @staticmethod
    def _raise_customer_access_error():
        raise serializers.ValidationError(
            {"customer_id": "客户不存在、未启用或不在当前业务员可用范围内。"}
        )

    def _assert_customer_access(self, customer, owner_id, user):
        if not customer:
            return
        binding = customer_binding(customer, owner_id)
        if binding is None:
            self._raise_customer_access_error()
        customer.owner_customer_code = binding.code
        if not self._is_cash_customer(customer) and binding.salesperson_id != getattr(
            user, "id", None
        ):
            self._raise_customer_access_error()

    def _assert_supplier_belongs_to_owner(self, supplier_id, owner_id):
        Supplier = apps.get_model("baseinfo", "Supplier")
        if not Supplier.objects.filter(
            pk=supplier_id,
            owner_id=owner_id,
            is_active=True,
        ).exists():
            raise serializers.ValidationError(
                {"supplier_id": "供应商不存在、已停用或不属于当前货主。"}
            )

    def _assert_products_belong_to_owner(self, items, owner_id):
        Product = apps.get_model("products", "Product")
        pid_list = [it["product_id"] for it in items if it.get("product_id")]
        if not pid_list:
            return
        owners = dict(Product.objects.filter(id__in=pid_list).values_list("id", "owner_id"))
        bad = [pid for pid in pid_list if owners.get(pid) != owner_id]
        if bad:
            raise serializers.ValidationError(f"存在不属于当前货主的商品：{bad}")

    def _validate_standard_sales_prices(self, items, owner_id, *, draft=False):
        from .pricing import match_saved_line, pricing_applies, sale_minimum

        Product = apps.get_model("products", "Product")
        product_ids = [item["product_id"] for item in items]
        products = {
            product.id: product
            for product in Product.objects.filter(
                id__in=product_ids,
                owner_id=owner_id,
                is_active=True,
            ).only(
                "id",
                "owner_id",
                "code",
                "sku",
                "price",
                "min_price",
                "max_discount",
                "is_active",
            )
        }
        errors = [{} for _ in items]
        for index, item in enumerate(items):
            product = products.get(item["product_id"])
            if product is None:
                errors[index] = {"price": "商品不存在、已停用或不属于当前货主。"}
                continue

            label = product.code or product.sku or str(product.pk)
            price = item.get("price")
            saved = match_saved_line(item, self.context.get("saved_pricing_lines"))
            try:
                item["is_priced"] = pricing_applies(product, saved)
            except InvalidSalePriceRule as exc:
                errors[index] = {"price": f"{label} 价格配置错误：{exc}"}
                continue
            if not item["is_priced"]:
                item["price"] = Decimal("0")
                continue
            if draft:
                continue
            if price is None or price <= 0:
                errors[index] = {"price": f"{label} 成交价必须大于 0。"}
                continue
            try:
                lowest = sale_minimum(product)
            except InvalidSalePriceRule as exc:
                errors[index] = {"price": f"{label} 价格配置错误：{exc}"}
                continue
            if lowest is not None and price < lowest:
                errors[index] = {
                    "price": f"{label} 成交价不能低于 {lowest}。"
                }

        if any(errors):
            raise serializers.ValidationError({"items": errors})

    @staticmethod
    def _line_model_validation_detail(exc):
        aliases = {
            "base_qty": "qty",
            "base_price": "price",
            "product": "product_id",
            "__all__": "non_field_errors",
        }
        if hasattr(exc, "message_dict"):
            return {
                aliases.get(field, field): messages
                for field, messages in exc.message_dict.items()
            }
        return {"non_field_errors": list(exc.messages)}

    def _is_cash_customer(self, customer):
        code = (getattr(customer, "owner_customer_code", "") or "").strip().upper()
        return code == "CASH"

    # ---------- 校验 ----------
    def validate(self, data):
        if not data.get("items"):
            raise serializers.ValidationError("至少需要一条明细。")
        line_keys = [item["line_key"] for item in data["items"] if item.get("line_key")]
        if len(line_keys) != len(set(line_keys)):
            raise serializers.ValidationError({"items": "商品行标识重复，请重新加载订单。"})

        # owner 只能来自服务端角色范围；货主角色必须显式选择目标仓库。
        req = self.context.get("request")
        user = getattr(req, "user", None)
        scope = self.context.get("access_scope") or AccessScope.for_user(user)
        structural_only = self.context.get("structural_only", False)
        owner_id = scope.single_owner_id
        warehouse_id = data.get("warehouse_id")

        if not owner_id:
            raise serializers.ValidationError("当前用户没有单一有效货主角色范围，请联系管理员。")
        if not warehouse_id:
            raise serializers.ValidationError({"warehouse_id": "请选择出库仓库。"})
        if not structural_only and not owner_can_use_warehouse(owner_id, warehouse_id):
            raise serializers.ValidationError(
                {"warehouse_id": "仓库不可用或未关联当前货主。"}
            )

        # With USE_TZ=False DRF normalizes offset-aware input to naive UTC.
        # WMS business timestamps are local warehouse time, so preserve the
        # represented instant as a local naive value before persisting it.
        raw_etd = (
            self.initial_data.get("etd")
            if hasattr(self.initial_data, "get")
            else None
        )
        if not settings.USE_TZ and isinstance(raw_etd, str):
            parsed_etd = parse_datetime(raw_etd)
            if parsed_etd is not None and parsed_etd.tzinfo is not None:
                data["etd"] = parsed_etd.astimezone(
                    ZoneInfo(settings.TIME_ZONE)
                ).replace(tzinfo=None)

        # 出库类型 / 配送方式 choices 校验（若模型定义了）
        ot = data.get("outbound_type", "SALES")
        allowed_ot = self._allowed("outbound_type")
        if allowed_ot and ot not in allowed_ot:
            raise serializers.ValidationError(f"不支持的出库类型：{ot}")
        if data.get("reorder_source_id") and ot != "SALES":
            raise serializers.ValidationError("再次下单只能创建标准销售订单。")

        dm = data.get("delivery_method")
        allowed_dm = self._allowed("delivery_method")
        if dm and allowed_dm and dm not in allowed_dm:
            raise serializers.ValidationError(f"不支持的配送方式：{dm}")

        # 退供 vs 销售 的客户/供应商约束
        if ot == "SUPPLIER_RETURN":
            if not data.get("supplier_id") or data.get("customer_id"):
                raise serializers.ValidationError("退供单必须提供 supplier_id 且 customer_id 为空。")
            if not structural_only:
                self._assert_supplier_belongs_to_owner(data["supplier_id"], owner_id)
            customer = None
        else:
            if not data.get("customer_id") or data.get("supplier_id"):
                raise serializers.ValidationError("非退供出库单必须提供 customer_id 且 supplier_id 为空。")
            customer = None if structural_only else self._get_customer(data.get("customer_id"))

        # 一致性：客户、商品均需属于当前用户的 owner
        if not structural_only:
            self._assert_customer_access(customer, owner_id, user)
            self._assert_products_belong_to_owner(data["items"], owner_id)
            if ot == "SALES":
                self._validate_standard_sales_prices(
                    data["items"], owner_id, draft=self.context.get("allow_problem_draft", False)
                )

        # 清洗字符串
        data["src_bill_no"]   = (data.get("src_bill_no") or "").strip() or None
        data["contact"]       = (data.get("contact") or "").strip()
        data["contact_phone"] = (data.get("contact_phone") or "").strip()
        data["ship_to"]       = (data.get("ship_to") or "").strip()

        # 一件代发客户：收件信息必填
        if self._is_cash_customer(customer):
            if not data["contact"]:
                raise serializers.ValidationError({"contact": "一件代发客户必须填写收件人。"})
            if not data["contact_phone"]:
                raise serializers.ValidationError({"contact_phone": "一件代发客户必须填写联系电话。"})
            if not data["ship_to"]:
                raise serializers.ValidationError({"ship_to": "一件代发客户必须填写收货地址。"})
            if len(data["contact_phone"]) < 6 or not any(ch.isdigit() for ch in data["contact_phone"]):
                raise serializers.ValidationError({"contact_phone": "联系电话格式不正确。"})

        # 把后端推断出的 owner/warehouse 放入 validated_data，供 create 使用
        data["owner_id__from_user"] = owner_id
        data["warehouse_id__from_user"] = warehouse_id
        return data

    # ---------- 创建 ----------
    @transaction.atomic
    def create(self, validated):
        logger.debug(
            "outbound.order.create.validated item_count=%d",
            len(validated.get("items", [])),
        )

        OutboundOrder     = apps.get_model("outbound", "OutboundOrder")
        OutboundOrderLine = apps.get_model("outbound", "OutboundOrderLine")

        req  = self.context.get("request")
        user = getattr(req, "user", None)

        owner_id     = validated["owner_id__from_user"]
        warehouse_id = validated["warehouse_id__from_user"]

        order = OutboundOrder.objects.create(
            owner_id        = owner_id,
            customer_id     = validated.get("customer_id"),
            supplier_id     = validated.get("supplier_id"),
            warehouse_id    = warehouse_id,
            outbound_type   = validated.get("outbound_type", "SALES"),
            delivery_method = validated.get("delivery_method"),
            etd             = validated.get("etd"),
            memo            = validated.get("remark", ""),
            src_bill_no     = validated.get("src_bill_no"),
            idempotency_key = validated.get("idempotency_key"),
            idempotency_fingerprint = validated.get("idempotency_fingerprint", ""),
            contact         = validated.get("contact", ""),
            contact_phone   = validated.get("contact_phone", ""),
            ship_to         = validated.get("ship_to", ""),
            created_by      = user if (user and user.is_authenticated) else None,
            biz_date        = date.today(),
            submit_status   = "SUBMITTED",
        )

        items = validated["items"]
        for index, it in enumerate(items):
            try:
                OutboundOrderLine.objects.create(
                    order=order,
                    product_id=it["product_id"],
                    base_qty=it["qty"],
                    base_price=it.get("price") or Decimal("0.0000"),
                    is_priced=it.get("is_priced", True),
                    # 如需包装下单：可额外写入 aux_uom_id / aux_qty / aux_price
                )
            except DjangoValidationError as exc:
                errors = [{} for _ in items]
                errors[index] = self._line_model_validation_detail(exc)
                raise serializers.ValidationError({"items": errors}) from exc

        return order


class OutboundOrderDraftUpdateSerializer(OutboundOrderCreateSerializer):
    """Full-replacement payload for an editable standard owner draft."""

    expected_updated_at = serializers.DateTimeField(
        write_only=True,
        required=True,
        error_messages={"required": "缺少订单编辑版本，请重新进入编辑。"},
    )

class OutboundOrderLineReadSerializer(serializers.ModelSerializer):
    is_priced = serializers.BooleanField(read_only=True)
    product_name = serializers.SerializerMethodField()
    product_sku  = serializers.SerializerMethodField()
    amount       = serializers.SerializerMethodField()

    class Meta:
        model  = OutboundOrderLine
        fields = [
            "id",
            "line_no",
            "product",
            "product_sku",
            "product_name",
            "base_uom",
            "base_qty",
            "base_price",
            "is_priced",
            "amount",
            "aux_uom",
            "aux_qty",
            "aux_price",
            "ratio",
            "lot_no",
            "pack_requirement",
            "pack_note",
            "note",
        ]

    def get_product_name(self, obj):
        return _get(obj.product, ["name","title","full_name","display_name"], "")

    def get_product_sku(self, obj):
        return _get(obj.product, ["sku","code","barcode"], "")

    def get_amount(self, obj):
        if not obj.is_priced:
            return Decimal("0.00")
        try:
            return line_amount(obj.base_qty, obj.base_price)
        except Exception:
            return Decimal("0.00")

class OutboundOrderReadSerializer(serializers.ModelSerializer):
    approved_by_warehouse_name = serializers.SerializerMethodField()
    can_withdraw = serializers.SerializerMethodField()
    can_reorder = serializers.SerializerMethodField()
    customer_name        = serializers.CharField(
        source="customer.name", read_only=True, allow_null=True
    )
    warehouse_name       = serializers.CharField(
        source="warehouse.name", read_only=True
    )
    owner_name           = serializers.CharField(source="owner.name", read_only=True)
    submit_status_name   = serializers.SerializerMethodField()
    approval_status_name = serializers.SerializerMethodField()
    total_amount         = serializers.SerializerMethodField()
    total_qty            = serializers.SerializerMethodField()
    amount_status = serializers.SerializerMethodField()
    priced_amount = serializers.SerializerMethodField()
    unpriced_line_count = serializers.SerializerMethodField()
    created_by_name      = serializers.SerializerMethodField()
    priced_by_name       = serializers.SerializerMethodField()
    can_edit             = serializers.SerializerMethodField()
    can_submit           = serializers.SerializerMethodField()
    can_owner_review     = serializers.SerializerMethodField()
    # ✅ 你的模型 OutboundOrderLine.order 的 related_name = "lines"
    #    所以这里不要写 source="lines"，直接这样写即可
    lines = OutboundOrderLineReadSerializer(many=True, read_only=True)

    class Meta:
        model = OutboundOrder
        fields = [
            "id",
            "order_no",
            "biz_date",
            "submit_status",
            "submit_status_name",
            "approval_status",
            "approval_status_name",
            "pricing_status",
            "priced_at",
            "priced_by",
            "priced_by_name",
            "final_order_amount",
            "outbound_type",
            "delivery_method",
            "etd",
            "owner",
            "owner_name",
            "customer",
            "customer_name",
            "supplier",
            "warehouse",
            "warehouse_name",
            "processing_mode",
            "assisted_by",
            "assisted_at",
            "assistance_reason",
            "assistance_request_id",
            "created_by",
            "created_by_name",
            "created_at",
            "updated_at",
            "src_bill_no",
            "owner_reject_reason",
            "warehouse_reject_reason",
            "approved_by_warehouse",
            "approved_by_warehouse_name",
            "approved_at_warehouse",
            "ship_to",
            "contact",
            "contact_phone",
            "memo",
            "is_closed",
            "close_reason",
            "lines",
            "total_qty",
            "total_amount",
            "amount_status",
            "priced_amount",
            "unpriced_line_count",
            "can_edit",
            "can_withdraw",
            "can_submit",
            "can_owner_review",
            "can_reorder",
        ]
        read_only_fields = [
            "warehouse_reject_reason", "approved_by_warehouse", "approved_at_warehouse",
        ]

    def _access_scope(self):
        """Resolve the request's tenant scope at most once per serializer."""

        if not hasattr(self, "_resolved_access_scope"):
            request = self.context.get("request")
            user = getattr(request, "user", None)
            self._resolved_access_scope = (
                self.context.get("access_scope") or AccessScope.for_user(user)
            )
        return self._resolved_access_scope

    def get_approved_by_warehouse_name(self, obj):
        user = obj.approved_by_warehouse
        return (user.get_full_name() or user.username) if user else ""

    def get_can_withdraw(self, obj):
        user = getattr(self.context.get("request"), "user", None)
        scope = self._access_scope()
        return bool(
            user and user.is_authenticated
            and obj.created_by_id == user.pk
            and obj.processing_mode == "STANDARD"
            and obj.submit_status == "SUBMITTED"
            and obj.approval_status == "WHS_REJECTED"
            and not obj.is_closed
            and scope.is_valid
            and UserRoleScope.Role.OWNER_SALESPERSON in scope.roles
            and scope.allows(owner_id=obj.owner_id, warehouse_id=obj.warehouse_id)
            and user.has_perm("outbound.submit_outbound_as_owner_buyers")
        )

    def get_can_edit(self, obj):
        request = self.context.get("request")
        return can_edit_standard_draft(
            obj,
            getattr(request, "user", None),
            scope=self._access_scope(),
        )

    def get_can_reorder(self, obj):
        user = getattr(self.context.get("request"), "user", None)
        scope = self._access_scope()
        if not (
            user and scope.is_valid and scope.single_owner_id
            and "owner_salesperson" in scope.roles
            and user.has_perm("outbound.submit_outbound_as_owner_buyers")
            and obj.owner_id == scope.single_owner_id and obj.created_by_id == user.pk
            and obj.outbound_type == "SALES" and not obj.is_deleted
            and not (obj.memo or "").startswith("[POS]")
        ):
            return False
        if hasattr(obj, "owner_pos_link"):
            return not (obj.owner_pos_link or obj.owner_pos_line)
        from .reorder import non_pos_orders
        return non_pos_orders(OutboundOrder.objects.filter(pk=obj.pk)).exists()

    def get_can_submit(self, obj):
        return self.get_can_edit(obj)

    def get_can_owner_review(self, obj):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if not getattr(user, "is_authenticated", False):
            return False
        if (
            obj.submit_status != "SUBMITTED"
            or obj.approval_status != "OWNER_PENDING"
            or obj.is_closed
        ):
            return False
        if user.is_superuser:
            return True
        scope = self._access_scope()
        return bool(
            user.has_perm("outbound.approve_outbound_as_owner_manager")
            and UserRoleScope.Role.OWNER_MANAGER in scope.roles
            and scope.allows(owner_id=obj.owner_id, warehouse_id=obj.warehouse_id)
        )

    def get_priced_by_name(self, obj):
        u = getattr(obj, "priced_by", None)
        if not u:
            return ""
        return getattr(u, "name", None) or getattr(u, "username", None) or ""

    def _status_name(self, obj, field):
        if getattr(obj, field) not in dict(obj._meta.get_field(field).choices):
            return "状态待核对"
        return getattr(obj, f"get_{field}_display")()

    def get_submit_status_name(self, obj):
        return self._status_name(obj, "submit_status")

    def get_approval_status_name(self, obj):
        return self._status_name(obj, "approval_status")

    def get_created_by_name(self, obj):
        u = getattr(obj, "created_by", None)
        if not u:
            return ""
        # 你的 User 模型有 name 字段
        return (getattr(u, "name", None) or getattr(u, "username", None) or "")

    def get_total_qty(self, obj):
        if hasattr(obj, "catalog_total_qty"):
            return obj.catalog_total_qty
        total = Decimal("0")
        for line in obj.lines.all():
            total += Decimal(line.base_qty or 0)
        return total

    def get_total_amount(self, obj):
        return self.get_priced_amount(obj)

    def _pricing_summary(self, obj):
        from .amounts import pricing_summary

        cache = getattr(self, "_pricing_summaries", None)
        if cache is None:
            cache = self._pricing_summaries = {}
        if obj.pk not in cache:
            cache[obj.pk] = pricing_summary(obj.lines.all())
        return cache[obj.pk]

    def get_amount_status(self, obj):
        return self._pricing_summary(obj)["amount_status"]

    def get_priced_amount(self, obj):
        return self._pricing_summary(obj)["priced_amount"]

    def get_unpriced_line_count(self, obj):
        return self._pricing_summary(obj)["unpriced_line_count"]


# class OutboundOrderReadSerializer(serializers.ModelSerializer):
#     submit_status_name    = serializers.SerializerMethodField()
#     approval_status_name  = serializers.SerializerMethodField()
#     total_amount          = serializers.SerializerMethodField()
#     total_qty             = serializers.SerializerMethodField()
#
#     # ✅ related_name="lines" 时：不要写 source="lines"
#     lines = OutboundOrderLineReadSerializer(many=True, read_only=True)
#
#     class Meta:
#         model  = OutboundOrder
#         fields = [
#             "id","order_no","biz_date",
#             "submit_status","submit_status_name",
#             "approval_status","approval_status_name",
#             "outbound_type","delivery_method","etd",
#             "owner","customer","supplier","warehouse",
#             "ship_to","contact","contact_phone",
#             "memo","is_closed","close_reason",
#             "created_at",
#             "lines",
#             "total_qty","total_amount",
#         ]
#
#     def _name_of(self, obj, field, fallback):
#         try:
#             mapping = dict(getattr(OutboundOrder, field))
#             code = getattr(obj, field.replace("_name",""))
#             return mapping.get(code, code)
#         except Exception:
#             return getattr(obj, fallback, None)
#
#     def get_submit_status_name(self, obj):
#         return self._name_of(obj, "SUBMIT_CHOICES", "submit_status")
#
#     def get_approval_status_name(self, obj):
#         return self._name_of(obj, "APPROVAL_CHOICES", "approval_status")
#
#     def get_total_qty(self, obj):
#         total = Decimal("0")
#         for l in getattr(obj, "lines").all():
#             total += Decimal(l.base_qty or 0)
#         return total
#
#     def get_total_amount(self, obj):
#         total = Decimal("0")
#         for l in getattr(obj, "lines").all():
#             total += (Decimal(l.base_qty or 0) * Decimal(l.base_price or 0))
#         return total.quantize(Decimal("0.01"))


# class OutboundOrderReadSerializer(serializers.ModelSerializer):
#
#     submit_status_name    = serializers.SerializerMethodField()
#     approval_status_name  = serializers.SerializerMethodField()
#     total_amount          = serializers.SerializerMethodField()
#     total_qty = serializers.SerializerMethodField()
#
#     # 确认你的 related_name；若模型里 related_name='items'，用 source="items"
#     lines = OutboundOrderLineReadSerializer(source="items", many=True, read_only=True)
#
#     class Meta:
#
#         model  = OutboundOrder
#         fields = [
#             "id","order_no","biz_date",
#             "submit_status","submit_status_name",
#             "approval_status","approval_status_name",
#             "outbound_type","delivery_method","etd",
#             "owner","customer","supplier","warehouse",
#             "ship_to","contact","contact_phone",
#             "memo","is_closed","close_reason",
#             "created_at","lines","total_amount",
#             "lines", "total_qty", "total_amount",
#         ]
#
#
#
#     def _name_of(self, obj, field, fallback):
#         try:
#             mapping = dict(getattr(OutboundOrder, field))
#             code = getattr(obj, field.replace("_name",""))
#             return mapping.get(code, code)
#         except Exception:
#             return getattr(obj, fallback, None)
#
#     def get_submit_status_name(self, obj):
#         return self._name_of(obj, "SUBMIT_CHOICES", "submit_status")
#
#     def get_approval_status_name(self, obj):
#         return self._name_of(obj, "APPROVAL_CHOICES", "approval_status")
#
#     # --- 关键修复：安全获取明细可迭代对象 ---
#     def _iter_lines(self, obj):
#         rel = (
#             getattr(obj, "items", None) or     # 优先：related_name='items'
#             getattr(obj, "lines", None) or     # 其次：related_name='lines'
#             getattr(obj, "outboundorderline_set", None)  # 默认反向管理器
#         )
#         if rel is None:
#             return []
#         return rel.all() if hasattr(rel, "all") else rel
#
#     def get_total_qty(self, obj):
#         total = Decimal("0")
#         for l in self._iter_lines(obj):
#             qty = getattr(l, "base_qty", None)
#             if qty is None:
#                 qty = getattr(l, "qty", 0)
#             total += Decimal(qty or 0)
#         return total
#
#     def get_total_amount(self, obj):
#         total = Decimal("0")
#         for l in self._iter_lines(obj):
#             amt = getattr(l, "amount", None)
#             if amt is None:
#                 price = getattr(l, "base_price", None)
#                 if price is None:
#                     price = getattr(l, "price", 0)
#                 qty = getattr(l, "base_qty", None)
#                 if qty is None:
#                     qty = getattr(l, "qty", 0)
#                 amt = Decimal(price or 0) * Decimal(qty or 0)
#             total += Decimal(amt or 0)
#         return total
