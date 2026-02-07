from datetime import date

from django.apps import apps
from django.utils import timezone
# serializers.py
import logging
from django.apps import apps
from django.utils import timezone
from rest_framework import serializers

logger = logging.getLogger(__name__)
from rest_framework import serializers
from decimal import Decimal

from .models import OutboundOrder, OutboundOrderLine

# 兼容不同字段命名的小工具
def _get(obj, names, default=None):
    for n in names:
        if hasattr(obj, n):
            v = getattr(obj, n)
            return v() if callable(v) else v
    return default

# ---------- 创建用 ----------
class OutboundOrderLineCreateSerializer(serializers.Serializer):
    product_id = serializers.IntegerField()
    uom_id     = serializers.IntegerField(required=False, allow_null=True)  # 如需包装下单可使用
    qty        = serializers.DecimalField(max_digits=18, decimal_places=3)  # 约定为“基本单位数量”
    price      = serializers.DecimalField(max_digits=18, decimal_places=4)  # 约定为“基本单位单价”

# class OutboundOrderCreateSerializer(serializers.Serializer):
#     owner_id       = serializers.IntegerField(required=False)                # 多货主建议前端显式传
#     customer_id    = serializers.IntegerField(required=False, allow_null=True)
#     supplier_id    = serializers.IntegerField(required=False, allow_null=True)
#     warehouse_id   = serializers.IntegerField()
#     outbound_type  = serializers.CharField(required=False, default="SALES")  # 运行时校验 choices
#     delivery_method= serializers.CharField(required=False, allow_null=True)
#     etd            = serializers.DateTimeField(required=False, allow_null=True)
#     remark         = serializers.CharField(required=False, allow_blank=True, default="")
#     items          = OutboundOrderLineCreateSerializer(many=True)
#
#     logger.debug("OutboundOrderCreateSerializer items ", items)
#
#     def _allowed(self, model_field_name):
#         try:
#             f = OutboundOrder._meta.get_field(model_field_name)
#             return [c[0] for c in (f.choices or [])]
#         except Exception:
#             return None
#
#     def _infer_owner_from_customer(self, customer_id):
#         try:
#             Customer = apps.get_model("baseinfo", "Customer")
#             c = Customer.objects.only("id", "owner_id").get(pk=customer_id)
#             return getattr(c, "owner_id", None)
#         except Exception:
#             return None
#
#     def validate(self, data):
#         if not data.get("items"):
#             raise serializers.ValidationError("至少需要一条明细。")
#
#         # 校验 outbound_type / delivery_method 是否在模型 choices 内（若模型定义了）
#         ot = data.get("outbound_type", "SALES")
#         allowed_ot = self._allowed("outbound_type")
#         if allowed_ot and ot not in allowed_ot:
#             raise serializers.ValidationError(f"不支持的出库类型：{ot}")
#
#         dm = data.get("delivery_method", None)
#         allowed_dm = self._allowed("delivery_method")
#         if dm and allowed_dm and dm not in allowed_dm:
#             raise serializers.ValidationError(f"不支持的配送方式：{dm}")
#
#         # 退供 vs 销售 的客户/供应商约束（按你模型习惯）
#         if ot == "SUPPLIER_RETURN":
#             if not data.get("supplier_id") or data.get("customer_id"):
#                 raise serializers.ValidationError("退供单必须提供 supplier_id 且 customer_id 为空。")
#         else:
#             if not data.get("customer_id") or data.get("supplier_id"):
#                 raise serializers.ValidationError("非退供出库单必须提供 customer_id 且 supplier_id 为空。")
#         return data
#
#     def create(self, validated):
#         req  = self.context.get("request")
#         user = getattr(req, "user", None)
#
#         owner_id = validated.get("owner_id")
#         if not owner_id and validated.get("customer_id"):
#             owner_id = self._infer_owner_from_customer(validated["customer_id"])
#         if not owner_id:
#             raise serializers.ValidationError("缺少 owner_id，且无法从客户推断。")
#         logger.debug("OutboundOrderCreateSerializer OutboundOrder.objects.create ")
#         order = OutboundOrder.objects.create(
#             owner_id=owner_id,
#             customer_id=validated.get("customer_id"),
#             supplier_id=validated.get("supplier_id"),
#             warehouse_id=validated["warehouse_id"],
#             outbound_type=validated.get("outbound_type", "SALES"),
#             delivery_method=validated.get("delivery_method"),
#             etd=validated.get("etd"),
#             memo=validated.get("remark", ""),
#             created_by=user if (user and user.is_authenticated) else None,
#             biz_date=timezone.localdate(),
#         )
#         for it in validated["items"]:
#             OutboundOrderLine.objects.create(
#                 order=order,
#                 product_id=it["product_id"],
#                 base_qty=it["qty"],
#                 base_price=it["price"],
#                 # 如需包装下单，可额外写入：aux_uom_id=it.get("uom_id"), aux_qty=..., aux_price=...
#             )
#         return order

# ---------- 读取用 ----------

class OutboundOrderCreateSerializer(serializers.Serializer):
    # 不再接收 owner_id / warehouse_id
    customer_id     = serializers.IntegerField(required=False, allow_null=True)
    supplier_id     = serializers.IntegerField(required=False, allow_null=True)
    outbound_type   = serializers.CharField(required=False, default="SALES")
    delivery_method = serializers.CharField(required=False, allow_null=True)
    etd             = serializers.DateTimeField(required=False, allow_null=True)
    remark          = serializers.CharField(required=False, allow_blank=True, default="")
    items           = OutboundOrderLineCreateSerializer(many=True)

    # ---------- 内部辅助 ----------
    def _allowed(self, model_field_name):
        OutboundOrder = apps.get_model("outbound", "OutboundOrder")
        try:
            f = OutboundOrder._meta.get_field(model_field_name)
            return [c[0] for c in (f.choices or [])]
        except Exception:
            return None

    def _assert_customer_belongs_to_owner(self, customer_id, owner_id):
        if not customer_id:
            return
        Customer = apps.get_model("baseinfo", "Customer")
        c = Customer.objects.only("id", "owner_id").get(pk=customer_id)
        if c.owner_id != owner_id:
            raise serializers.ValidationError("客户不属于当前用户的货主，禁止下单。")

    def _assert_products_belong_to_owner(self, items, owner_id):
        Product = apps.get_model("products", "Product")
        pid_list = [it["product_id"] for it in items if it.get("product_id")]
        if not pid_list:
            return
        owners = dict(Product.objects.filter(id__in=pid_list).values_list("id", "owner_id"))
        bad = [pid for pid in pid_list if owners.get(pid) != owner_id]
        if bad:
            raise serializers.ValidationError(f"存在不属于当前货主的商品：{bad}")

    # ---------- 校验 ----------
    def validate(self, data):
        if not data.get("items"):
            raise serializers.ValidationError("至少需要一条明细。")

        # 从登录用户获取 owner / warehouse（不再走前端）
        req = self.context.get("request")
        user = getattr(req, "user", None)
        owner_id = getattr(user, "owner_id", None)
        warehouse_id = getattr(user, "warehouse_id", None)
        if not owner_id:
            raise serializers.ValidationError("当前用户未绑定货主（owner），请联系管理员。")
        if not warehouse_id:
            raise serializers.ValidationError("当前用户未绑定仓库（warehouse），请联系管理员。")

        # 出库类型 / 配送方式 choices 校验（若模型定义了）
        ot = data.get("outbound_type", "SALES")
        allowed_ot = self._allowed("outbound_type")
        if allowed_ot and ot not in allowed_ot:
            raise serializers.ValidationError(f"不支持的出库类型：{ot}")

        dm = data.get("delivery_method")
        allowed_dm = self._allowed("delivery_method")
        if dm and allowed_dm and dm not in allowed_dm:
            raise serializers.ValidationError(f"不支持的配送方式：{dm}")

        # 退供 vs 销售 的客户/供应商约束
        if ot == "SUPPLIER_RETURN":
            if not data.get("supplier_id") or data.get("customer_id"):
                raise serializers.ValidationError("退供单必须提供 supplier_id 且 customer_id 为空。")
        else:
            if not data.get("customer_id") or data.get("supplier_id"):
                raise serializers.ValidationError("非退供出库单必须提供 customer_id 且 supplier_id 为空。")

        # 一致性：客户、商品均需属于当前用户的 owner
        self._assert_customer_belongs_to_owner(data.get("customer_id"), owner_id)
        self._assert_products_belong_to_owner(data["items"], owner_id)

        # 把后端推断出的 owner/warehouse 放入 validated_data，供 create 使用
        data["owner_id__from_user"] = owner_id
        data["warehouse_id__from_user"] = warehouse_id
        return data

    # ---------- 创建 ----------
    def create(self, validated):
        logger.debug("%s.create items=%d customer_id=%s",
                     self.__class__.__name__, len(validated.get("items", [])),
                     validated.get("customer_id"))
        OutboundOrder     = apps.get_model("outbound", "OutboundOrder")
        OutboundOrderLine = apps.get_model("outbound", "OutboundOrderLine")

        req  = self.context.get("request")
        user = getattr(req, "user", None)

        owner_id     = validated["owner_id__from_user"]
        warehouse_id = validated["warehouse_id__from_user"]

        logger.debug(
            "Create OutboundOrder owner_id=%s warehouse_id=%s customer_id=%s items=%s",
            owner_id, warehouse_id, validated.get("customer_id"), len(validated.get("items", []))
        )

        order = OutboundOrder.objects.create(
            owner_id      = owner_id,
            customer_id   = validated.get("customer_id"),
            supplier_id   = validated.get("supplier_id"),
            warehouse_id  = warehouse_id,
            outbound_type = validated.get("outbound_type", "SALES"),
            delivery_method = validated.get("delivery_method"),
            etd           = validated.get("etd"),
            memo          = validated.get("remark", ""),
            created_by    = user if (user and user.is_authenticated) else None,
            biz_date      = date.today(),
            submit_status="SUBMITTED",
        )

        for it in validated["items"]:
            OutboundOrderLine.objects.create(
                order      = order,
                product_id = it["product_id"],
                base_qty   = it["qty"],
                base_price = it["price"],
                # 如需包装下单：可额外写入 aux_uom_id / aux_qty / aux_price
            )

        return order

class OutboundOrderLineReadSerializer(serializers.ModelSerializer):
    product_name = serializers.SerializerMethodField()
    product_sku  = serializers.SerializerMethodField()
    amount       = serializers.SerializerMethodField()

    class Meta:
        model  = OutboundOrderLine
        fields = [
            "id","line_no","product","product_sku","product_name",
            "base_uom","base_qty","base_price","amount",
            "aux_uom","aux_qty","aux_price","ratio",
            "lot_no","pack_requirement","pack_note","note",
        ]

    def get_product_name(self, obj):
        return _get(obj.product, ["name","title","full_name","display_name"], "")

    def get_product_sku(self, obj):
        return _get(obj.product, ["sku","code","barcode"], "")

    def get_amount(self, obj):
        try:
            return (Decimal(obj.base_qty or 0) * Decimal(obj.base_price or 0)).quantize(Decimal("0.01"))
        except Exception:
            return Decimal("0.00")



from decimal import Decimal
from rest_framework import serializers

# ... 你原来已有的 OutboundOrderLineReadSerializer 保持不变 ...


class OutboundOrderReadSerializer(serializers.ModelSerializer):
    submit_status_name   = serializers.SerializerMethodField()
    approval_status_name = serializers.SerializerMethodField()
    total_amount         = serializers.SerializerMethodField()
    total_qty            = serializers.SerializerMethodField()

    # ✅ 新增：业务员姓名（制表人）
    created_by_name      = serializers.SerializerMethodField()

    # ✅ 你的模型 OutboundOrderLine.order 的 related_name = "lines"
    #    所以这里不要写 source="lines"，直接这样写即可
    lines = OutboundOrderLineReadSerializer(many=True, read_only=True)

    class Meta:
        model = OutboundOrder
        fields = [
            "id", "order_no", "biz_date",
            "submit_status", "submit_status_name",
            "approval_status", "approval_status_name",
            "outbound_type", "delivery_method", "etd",

            "owner", "customer", "supplier", "warehouse",

            # ✅ 把 created_by id 和 created_by_name 都输出
            "created_by", "created_by_name",
            "created_at",

            "ship_to", "contact", "contact_phone",
            "memo", "is_closed", "close_reason",

            "lines",
            "total_qty", "total_amount",
        ]

    def _name_of(self, obj, field, fallback):
        try:
            mapping = dict(getattr(OutboundOrder, field))
            code = getattr(obj, field.replace("_name", ""))
            return mapping.get(code, code)
        except Exception:
            return getattr(obj, fallback, None)

    def get_submit_status_name(self, obj):
        return self._name_of(obj, "SUBMIT_CHOICES", "submit_status")

    def get_approval_status_name(self, obj):
        return self._name_of(obj, "APPROVAL_CHOICES", "approval_status")

    def get_created_by_name(self, obj):
        u = getattr(obj, "created_by", None)
        if not u:
            return ""
        # 你的 User 模型有 name 字段
        return (getattr(u, "name", None) or getattr(u, "username", None) or "")

    def get_total_qty(self, obj):
        total = Decimal("0")
        for l in obj.lines.all():
            total += Decimal(l.base_qty or 0)
        return total

    def get_total_amount(self, obj):
        total = Decimal("0")
        for l in obj.lines.all():
            total += Decimal(l.base_qty or 0) * Decimal(l.base_price or 0)
        return total.quantize(Decimal("0.01"))


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
