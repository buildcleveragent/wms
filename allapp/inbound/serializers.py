# allapp/inbound/serializers.py
from decimal import Decimal

from django.db.models import Sum
from rest_framework import serializers

from allapp.accounts.access import AccessScope
from allapp.accounts.models import UserRoleScope
from allapp.baseinfo.models import Owner, Supplier
from allapp.inbound.models import InboundOrder, InboundOrderLine
from allapp.locations.models import Warehouse
from allapp.products.models import Product
from allapp.tasking.models import (
    PutawayLineExtra,
    ReceiveLineExtra,
    TaskAssignment,
    WmsTask,
    WmsTaskLine,
)

# class ReceiveWithoutOrderItemSerializer(serializers.Serializer):
#     product_id = serializers.IntegerField(required=True)
#     qty = serializers.DecimalField(max_digits=18, decimal_places=4, required=True)
#
#
#     def validate_qty(self, v):
#         if v <= 0:
#             raise serializers.ValidationError("qty 必须 > 0")
#         return v


# class ReceiveWithoutOrderItemSerializer(serializers.Serializer):
#     product_id = serializers.IntegerField(required=True)
#     qty = serializers.DecimalField(max_digits=18, decimal_places=4, required=True)
#     lot_no = serializers.CharField(required=False, allow_blank=True, default="")
#     mfg_date = serializers.DateField(required=False, allow_null=True)
#     exp_date = serializers.DateField(required=False, allow_null=True)
#     batch_no = serializers.CharField(required=False, allow_blank=True, default="")
#     batch_number = serializers.CharField(required=False, allow_blank=True, default="")
#     production_date = serializers.DateField(required=False, allow_null=True)
#     expiry_date = serializers.DateField(required=False, allow_null=True)
#
#     def validate_qty(self, v):
#         if v <= 0:
#             raise serializers.ValidationError("qty 必须 > 0")
#         return v
#
#     def validate(self, attrs):
#         attrs["lot_no"] = attrs.get("lot_no") or attrs.get("batch_no") or attrs.get("batch_number") or ""
#         attrs["mfg_date"] = attrs.get("mfg_date") or attrs.get("production_date")
#         attrs["exp_date"] = attrs.get("exp_date") or attrs.get("expiry_date")
#         attrs.pop("batch_no", None)
#         attrs.pop("batch_number", None)
#         attrs.pop("production_date", None)
#         attrs.pop("expiry_date", None)
#
#         mfg_date = attrs.get("mfg_date")
#         exp_date = attrs.get("exp_date")
#         if mfg_date and exp_date and exp_date < mfg_date:
#             raise serializers.ValidationError({"exp_date": "有效期不得早于生产日期"})
#         return attrs


class ReceiveWithoutOrderItemSerializer(serializers.Serializer):
    product_id = serializers.IntegerField(required=True)
    qty = serializers.DecimalField(max_digits=18, decimal_places=4, required=True)
    lot_no = serializers.CharField(required=False, allow_blank=True, default="")
    mfg_date = serializers.DateField(required=False, allow_null=True)
    exp_date = serializers.DateField(required=False, allow_null=True)
    batch_no = serializers.CharField(required=False, allow_blank=True, default="")
    batch_number = serializers.CharField(required=False, allow_blank=True, default="")
    production_date = serializers.DateField(required=False, allow_null=True)
    expiry_date = serializers.DateField(required=False, allow_null=True)
    lotNo = serializers.CharField(required=False, allow_blank=True, default="")
    batchNumber = serializers.CharField(required=False, allow_blank=True, default="")
    mfgDate = serializers.DateField(required=False, allow_null=True)
    expDate = serializers.DateField(required=False, allow_null=True)
    productionDate = serializers.DateField(required=False, allow_null=True)
    expiryDate = serializers.DateField(required=False, allow_null=True)

    def validate_qty(self, v):
        if v <= 0:
            raise serializers.ValidationError("qty 必须 > 0")
        return v

    def validate(self, attrs):
        attrs["lot_no"] = (
            attrs.get("lot_no")
            or attrs.get("lotNo")
            or attrs.get("batch_no")
            or attrs.get("batch_number")
            or attrs.get("batchNumber")
            or ""
        )
        attrs["mfg_date"] = (
            attrs.get("mfg_date")
            or attrs.get("mfgDate")
            or attrs.get("production_date")
            or attrs.get("productionDate")
        )
        attrs["exp_date"] = (
            attrs.get("exp_date")
            or attrs.get("expDate")
            or attrs.get("expiry_date")
            or attrs.get("expiryDate")
        )
        for alias in (
            "batch_no",
            "batch_number",
            "production_date",
            "expiry_date",
            "lotNo",
            "batchNumber",
            "mfgDate",
            "expDate",
            "productionDate",
            "expiryDate",
        ):
            attrs.pop(alias, None)

        mfg_date = attrs.get("mfg_date")
        exp_date = attrs.get("exp_date")
        if mfg_date and exp_date and exp_date < mfg_date:
            raise serializers.ValidationError({"exp_date": "有效期不得早于生产日期"})
        return attrs


class ReceiveWithoutOrderPayloadSerializer(serializers.Serializer):
    request_id = serializers.RegexField(
        regex=r"^[A-Za-z0-9._:-]{8,64}$",
        max_length=64,
        required=True,
        error_messages={
            "invalid": "request_id 仅允许字母、数字及 . _ : -，长度为 8-64 位",
        },
    )
    owner_id = serializers.IntegerField(required=True)
    warehouse_id = serializers.IntegerField(required=False, allow_null=True)
    location_id = serializers.IntegerField(required=False, allow_null=True)
    remark = serializers.CharField(required=False, allow_blank=True, default="")
    items = ReceiveWithoutOrderItemSerializer(many=True, required=True, allow_empty=False)


class InboundOrderLineInputSerializer(serializers.Serializer):
    product_id = serializers.IntegerField(min_value=1)
    base_qty = serializers.DecimalField(max_digits=18, decimal_places=3, min_value=Decimal("0.001"))
    base_price = serializers.DecimalField(
        max_digits=14,
        decimal_places=4,
        min_value=Decimal("0"),
        required=False,
        default=Decimal("0"),
    )
    lot_no = serializers.CharField(max_length=50, required=False, allow_blank=True, default="")
    min_remaining_days = serializers.IntegerField(min_value=0, required=False, allow_null=True)
    expiry_not_earlier_than = serializers.DateField(required=False, allow_null=True)
    note = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")


class InboundOrderCreateSerializer(serializers.Serializer):
    """Create a draft ASN for the authenticated owner and selected warehouse."""

    owner_id = serializers.IntegerField(required=False, min_value=1)
    warehouse_id = serializers.IntegerField(min_value=1)
    supplier_id = serializers.IntegerField(min_value=1)
    biz_date = serializers.DateField(required=False)
    src_bill_no = serializers.CharField(max_length=100, required=False, allow_blank=True, allow_null=True)
    inbound_type = serializers.ChoiceField(
        choices=InboundOrder.INBOUND_TYPE_CHOICES,
        required=False,
        default="PURCHASE",
    )
    delivery_method = serializers.ChoiceField(
        choices=InboundOrder.DELIVERY_METHOD_CHOICES,
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    eta = serializers.DateTimeField(required=False, allow_null=True)
    address = serializers.CharField(max_length=100, required=False, allow_blank=True, allow_null=True)
    memo = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    lines = InboundOrderLineInputSerializer(many=True, allow_empty=False)

    def validate(self, attrs):
        request = self.context["request"]
        user = request.user
        scope = AccessScope.for_user(user)
        if not scope.is_valid:
            raise serializers.ValidationError("当前账号没有有效的货主范围。")

        if user.is_superuser:
            owner_id = attrs.get("owner_id")
            if not owner_id:
                raise serializers.ValidationError({"owner_id": "系统管理员创建时必须指定货主。"})
        else:
            if UserRoleScope.Role.OWNER_SALESPERSON not in scope.roles:
                raise serializers.ValidationError("仅货主业务员可以创建入库单。")
            if len(scope.owner_ids) != 1:
                raise serializers.ValidationError("当前账号的货主范围配置无效。")
            owner_id = next(iter(scope.owner_ids))
            supplied_owner_id = attrs.get("owner_id")
            if supplied_owner_id and int(supplied_owner_id) != owner_id:
                raise serializers.ValidationError({"owner_id": "不能为其他货主创建入库单。"})

        try:
            owner = Owner.objects.get(pk=owner_id)
        except Owner.DoesNotExist as exc:
            raise serializers.ValidationError({"owner_id": "货主不存在。"}) from exc
        try:
            warehouse = Warehouse.objects.get(pk=attrs["warehouse_id"])
        except Warehouse.DoesNotExist as exc:
            raise serializers.ValidationError({"warehouse_id": "仓库不存在。"}) from exc
        try:
            supplier = Supplier.objects.get(pk=attrs["supplier_id"], owner_id=owner_id)
        except Supplier.DoesNotExist as exc:
            raise serializers.ValidationError({"supplier_id": "供应商不存在或不属于当前货主。"}) from exc

        product_ids = {line["product_id"] for line in attrs["lines"]}
        products = {
            product.pk: product
            for product in Product.objects.filter(pk__in=product_ids, owner_id=owner_id).select_related(
                "base_uom"
            )
        }
        missing_ids = sorted(product_ids - set(products))
        if missing_ids:
            raise serializers.ValidationError(
                {"lines": f"商品不存在或不属于当前货主：{missing_ids}"}
            )

        attrs["_owner"] = owner
        attrs["_warehouse"] = warehouse
        attrs["_supplier"] = supplier
        attrs["_products"] = products
        return attrs

    def create(self, validated_data):
        request = self.context["request"]
        lines = validated_data.pop("lines")
        owner = validated_data.pop("_owner")
        warehouse = validated_data.pop("_warehouse")
        supplier = validated_data.pop("_supplier")
        products = validated_data.pop("_products")
        validated_data.pop("owner_id", None)
        validated_data.pop("warehouse_id", None)
        validated_data.pop("supplier_id", None)

        order = InboundOrder.objects.create(
            owner=owner,
            warehouse=warehouse,
            supplier=supplier,
            created_by=request.user,
            **validated_data,
        )
        for payload in lines:
            product = products[payload.pop("product_id")]
            InboundOrderLine.objects.create(order=order, product=product, **payload)
        return order


class InboundOrderLineReadSerializer(serializers.ModelSerializer):
    product_sku = serializers.CharField(source="product.sku", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    base_uom_name = serializers.CharField(source="product.base_uom.name", read_only=True)

    class Meta:
        model = InboundOrderLine
        fields = (
            "id",
            "line_no",
            "product_id",
            "product_sku",
            "product_name",
            "base_uom",
            "base_uom_name",
            "base_qty",
            "base_price",
            "lot_no",
            "min_remaining_days",
            "expiry_not_earlier_than",
            "note",
        )


class InboundOrderReadSerializer(serializers.ModelSerializer):
    owner_name = serializers.CharField(source="owner.name", read_only=True)
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)
    supplier_name = serializers.CharField(source="supplier.name", read_only=True)
    created_by_name = serializers.SerializerMethodField()
    submit_status_name = serializers.CharField(source="get_submit_status_display", read_only=True)
    approval_status_name = serializers.CharField(source="get_approval_status_display", read_only=True)
    inbound_type_name = serializers.CharField(source="get_inbound_type_display", read_only=True)
    lines = InboundOrderLineReadSerializer(many=True, read_only=True)
    planned_qty = serializers.SerializerMethodField()
    processed_qty = serializers.SerializerMethodField()
    task = serializers.SerializerMethodField()
    actions = serializers.SerializerMethodField()

    class Meta:
        model = InboundOrder
        fields = (
            "id",
            "order_no",
            "owner_id",
            "owner_name",
            "warehouse_id",
            "warehouse_name",
            "supplier_id",
            "supplier_name",
            "created_by_id",
            "created_by_name",
            "biz_date",
            "src_bill_no",
            "inbound_type",
            "inbound_type_name",
            "delivery_method",
            "eta",
            "address",
            "memo",
            "submit_status",
            "submit_status_name",
            "approval_status",
            "approval_status_name",
            "is_closed",
            "planned_qty",
            "processed_qty",
            "task",
            "actions",
            "lines",
            "created_at",
            "updated_at",
        )

    @staticmethod
    def get_created_by_name(obj):
        if not obj.created_by_id:
            return ""
        return obj.created_by.get_full_name() or obj.created_by.username

    @staticmethod
    def get_planned_qty(obj):
        annotated = getattr(obj, "planned_qty_value", None)
        if annotated is not None:
            return annotated
        return obj.lines.aggregate(total=Sum("base_qty"))["total"] or Decimal("0")

    @staticmethod
    def _receive_task(obj):
        prefetched = getattr(obj, "_receive_tasks", None)
        if prefetched is not None:
            return prefetched[0] if prefetched else None
        return (
            WmsTask.objects.filter(
                task_type=WmsTask.TaskType.RECEIVE,
                source_app="inbound",
                source_model="InboundOrder",
                source_pk=str(obj.pk),
            )
            .exclude(status=WmsTask.Status.CANCELLED)
            .order_by("id")
            .first()
        )

    def get_processed_qty(self, obj):
        task = self._receive_task(obj)
        if not task:
            return Decimal("0")
        return task.lines.aggregate(total=Sum("qty_done"))["total"] or Decimal("0")

    def get_task(self, obj):
        task = self._receive_task(obj)
        if not task:
            return None
        return {
            "id": task.pk,
            "task_no": task.task_no,
            "status": task.status,
            "status_name": task.get_status_display(),
            "review_status": task.review_status,
            "posting_status": task.posting_status,
        }

    def get_actions(self, obj):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return {}
        scope = AccessScope.for_user(user)
        is_superuser = bool(user.is_superuser)
        can_submit = bool(
            user.has_perm("inbound.submit_as_owner_buyers")
            and (
                is_superuser
                or UserRoleScope.Role.OWNER_SALESPERSON in scope.roles
            )
            and obj.created_by_id == user.pk
            and not obj.is_closed
            and obj.submit_status == "DRAFT"
            and obj.approval_status in {"NOT_READY", "OWNER_REJECTED", "WHS_REJECTED"}
        )
        return {
            "can_submit": can_submit,
            "can_owner_approve": bool(
                user.has_perm("inbound.approve_as_owner_manager")
                and (
                    is_superuser
                    or UserRoleScope.Role.OWNER_MANAGER in scope.roles
                )
                and obj.submit_status == "SUBMITTED"
                and obj.approval_status == "OWNER_PENDING"
            ),
            "can_owner_reject": bool(
                user.has_perm("inbound.approve_as_owner_manager")
                and (
                    is_superuser
                    or UserRoleScope.Role.OWNER_MANAGER in scope.roles
                )
                and obj.submit_status == "SUBMITTED"
                and obj.approval_status == "OWNER_PENDING"
            ),
        }


class InboundTaskLineSerializer(serializers.ModelSerializer):
    product_sku = serializers.CharField(source="product.sku", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    from_location_code = serializers.CharField(source="from_location.code", read_only=True)
    to_location_code = serializers.CharField(source="to_location.code", read_only=True)
    qty_pending = serializers.SerializerMethodField()
    receive = serializers.SerializerMethodField()
    putaway = serializers.SerializerMethodField()

    class Meta:
        model = WmsTaskLine
        fields = (
            "id",
            "product_id",
            "product_sku",
            "product_name",
            "from_location_id",
            "from_location_code",
            "to_location_id",
            "to_location_code",
            "qty_plan",
            "qty_done",
            "qty_pending",
            "status",
            "finished_at",
            "receive",
            "putaway",
        )

    @staticmethod
    def get_qty_pending(obj):
        return max(Decimal("0"), Decimal(obj.qty_plan or 0) - Decimal(obj.qty_done or 0))

    @staticmethod
    def get_receive(obj):
        try:
            extra = obj.receivelineextra
        except ReceiveLineExtra.DoesNotExist:
            return None
        return {
            "qty_ok": extra.qty_ok,
            "qty_damage": extra.qty_damage,
            "qty_reject": extra.qty_reject,
            "lot_no": extra.lot_no,
            "mfg_date": extra.mfg_date,
            "exp_date": extra.exp_date,
            "damage_reason_code": extra.damage_reason_code,
            "reject_reason_code": extra.reject_reason_code,
        }

    @staticmethod
    def get_putaway(obj):
        try:
            extra = obj.putawaylineextra
        except PutawayLineExtra.DoesNotExist:
            return None
        return {
            "qty_moved": extra.qty_moved,
            "to_location_id": extra.to_location_id,
            "to_location_code": extra.to_location.code if extra.to_location_id else "",
        }


class InboundTaskSerializer(serializers.ModelSerializer):
    owner_name = serializers.CharField(source="owner.name", read_only=True)
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)
    task_type_name = serializers.CharField(source="get_task_type_display", read_only=True)
    status_name = serializers.CharField(source="get_status_display", read_only=True)
    lines = InboundTaskLineSerializer(many=True, read_only=True)
    is_assigned_to_me = serializers.SerializerMethodField()
    can_claim = serializers.SerializerMethodField()
    can_start = serializers.SerializerMethodField()
    can_record = serializers.SerializerMethodField()

    class Meta:
        model = WmsTask
        fields = (
            "id",
            "task_no",
            "task_type",
            "task_type_name",
            "status",
            "status_name",
            "owner_id",
            "owner_name",
            "warehouse_id",
            "warehouse_name",
            "ref_no",
            "remark",
            "released_at",
            "started_at",
            "finished_at",
            "review_status",
            "posting_status",
            "is_assigned_to_me",
            "can_claim",
            "can_start",
            "can_record",
            "lines",
        )

    def _request_user(self):
        request = self.context.get("request")
        return getattr(request, "user", None)

    def get_is_assigned_to_me(self, obj):
        user = self._request_user()
        return bool(
            user
            and TaskAssignment.objects.filter(
                task=obj,
                assignee=user,
                finished_at__isnull=True,
            ).exists()
        )

    def get_can_claim(self, obj):
        user = self._request_user()
        if not user or not user.has_perm("tasking.claim_task_as_wh_operator"):
            return False
        if obj.status != WmsTask.Status.RELEASED:
            return False
        return not TaskAssignment.objects.filter(task=obj, finished_at__isnull=True).exists()

    def get_can_start(self, obj):
        return bool(
            obj.status == WmsTask.Status.RELEASED and self.get_is_assigned_to_me(obj)
        )

    def get_can_record(self, obj):
        return bool(
            obj.status in {WmsTask.Status.RELEASED, WmsTask.Status.IN_PROGRESS}
            and self.get_is_assigned_to_me(obj)
        )


class ReceiptRecordSerializer(serializers.Serializer):
    request_id = serializers.RegexField(regex=r"^[A-Za-z0-9._:-]{8,64}$", max_length=64)
    line_id = serializers.IntegerField(min_value=1)
    location_id = serializers.IntegerField(min_value=1)
    qty_ok = serializers.DecimalField(max_digits=14, decimal_places=3, min_value=Decimal("0"))
    qty_damage = serializers.DecimalField(
        max_digits=14, decimal_places=3, min_value=Decimal("0"), required=False, default=Decimal("0")
    )
    qty_reject = serializers.DecimalField(
        max_digits=14, decimal_places=3, min_value=Decimal("0"), required=False, default=Decimal("0")
    )
    lot_no = serializers.CharField(max_length=60, required=False, allow_blank=True, default="")
    mfg_date = serializers.DateField(required=False, allow_null=True)
    exp_date = serializers.DateField(required=False, allow_null=True)
    damage_reason_code = serializers.CharField(max_length=20, required=False, allow_blank=True, default="")
    reject_reason_code = serializers.CharField(max_length=20, required=False, allow_blank=True, default="")
    finalize = serializers.BooleanField(required=False, default=False)
    variance_reason = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")

    def validate(self, attrs):
        if attrs.get("mfg_date") and attrs.get("exp_date") and attrs["exp_date"] < attrs["mfg_date"]:
            raise serializers.ValidationError({"exp_date": "有效期不得早于生产日期。"})
        processed = attrs["qty_ok"] + attrs["qty_damage"] + attrs["qty_reject"]
        if processed == 0 and not attrs.get("finalize"):
            raise serializers.ValidationError("数量均为零时必须明确结束该差异行。")
        if attrs["qty_damage"] > 0 and not attrs.get("damage_reason_code"):
            raise serializers.ValidationError({"damage_reason_code": "存在破损数量时必须填写原因。"})
        if attrs["qty_reject"] > 0 and not attrs.get("reject_reason_code"):
            raise serializers.ValidationError({"reject_reason_code": "存在拒收数量时必须填写原因。"})
        return attrs


class PutawayRecordSerializer(serializers.Serializer):
    request_id = serializers.RegexField(regex=r"^[A-Za-z0-9._:-]{8,64}$", max_length=64)
    line_id = serializers.IntegerField(min_value=1)
    to_location_id = serializers.IntegerField(min_value=1)
    qty = serializers.DecimalField(max_digits=14, decimal_places=3, min_value=Decimal("0.001"))
