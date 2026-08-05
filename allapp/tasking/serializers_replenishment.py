from decimal import Decimal

from django.core.exceptions import ObjectDoesNotExist
from rest_framework import serializers

from allapp.tasking.models import (
    ReplenishmentPolicy,
    ReplenishmentRequest,
    WmsTask,
)


class ReplenishmentPolicySerializer(serializers.ModelSerializer):
    owner_name = serializers.CharField(source="owner.name", read_only=True)
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    product_code = serializers.CharField(source="product.code", read_only=True)
    target_location_code = serializers.CharField(
        source="target_location.code", read_only=True
    )
    replenish_uom_code = serializers.CharField(
        source="replenish_uom.code", read_only=True
    )

    class Meta:
        model = ReplenishmentPolicy
        fields = [
            "id",
            "owner",
            "owner_name",
            "warehouse",
            "warehouse_name",
            "product",
            "product_name",
            "product_code",
            "target_location",
            "target_location_code",
            "min_qty",
            "target_qty",
            "replenish_uom",
            "replenish_uom_code",
            "source_zone_type",
            "priority",
            "auto_release",
            "demand_enabled",
            "is_active",
            "remark",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["created_at", "updated_at"]


class ReplenishmentRequestSerializer(serializers.ModelSerializer):
    owner_name = serializers.CharField(source="owner.name", read_only=True)
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    product_code = serializers.CharField(source="product.code", read_only=True)
    target_location_code = serializers.CharField(
        source="target_location.code", read_only=True
    )
    requested_by_name = serializers.CharField(
        source="created_by.username", read_only=True
    )
    reviewed_by_name = serializers.CharField(
        source="reviewed_by.username", read_only=True
    )
    generated_task_no = serializers.CharField(
        source="generated_task.task_no", read_only=True
    )

    class Meta:
        model = ReplenishmentRequest
        fields = [
            "id",
            "owner",
            "owner_name",
            "warehouse",
            "warehouse_name",
            "product",
            "product_name",
            "product_code",
            "target_location",
            "target_location_code",
            "requested_qty",
            "reason",
            "status",
            "requested_by_name",
            "reviewed_by_name",
            "reviewed_at",
            "review_note",
            "generated_task",
            "generated_task_no",
            "created_at",
        ]
        read_only_fields = fields


class ReplenishmentRequestCreateSerializer(serializers.Serializer):
    policy_id = serializers.IntegerField(min_value=1)
    requested_qty = serializers.DecimalField(
        max_digits=18, decimal_places=4, min_value=Decimal("0.0001")
    )
    reason = serializers.CharField(max_length=200)


class ReplenishmentReviewSerializer(serializers.Serializer):
    note = serializers.CharField(
        max_length=200, required=False, allow_blank=True, default=""
    )


class ReplenishmentEvaluateSerializer(serializers.Serializer):
    policy_id = serializers.IntegerField(min_value=1, required=False)
    owner_id = serializers.IntegerField(min_value=1, required=False)
    warehouse_id = serializers.IntegerField(min_value=1, required=False)
    product_id = serializers.IntegerField(min_value=1, required=False)


class ReplenishmentRecordSerializer(serializers.Serializer):
    request_id = serializers.RegexField(regex=r"^[A-Za-z0-9._:-]{8,64}$", max_length=64)
    line_id = serializers.IntegerField(min_value=1)
    from_location_code = serializers.CharField(max_length=60)
    to_location_code = serializers.CharField(max_length=60)
    product_code = serializers.CharField(max_length=128)
    qty = serializers.DecimalField(
        max_digits=18, decimal_places=3, min_value=Decimal("0.001")
    )
    serial_no = serializers.CharField(
        max_length=80, required=False, allow_blank=True, default=""
    )


class ReplenishmentTaskSerializer(serializers.ModelSerializer):
    owner_name = serializers.CharField(source="owner.name", read_only=True)
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)
    trigger = serializers.SerializerMethodField()
    lines = serializers.SerializerMethodField()
    is_assigned_to_me = serializers.SerializerMethodField()
    can_claim = serializers.SerializerMethodField()
    can_start = serializers.SerializerMethodField()
    can_record = serializers.SerializerMethodField()

    class Meta:
        model = WmsTask
        fields = [
            "id",
            "task_no",
            "status",
            "priority",
            "owner",
            "owner_name",
            "warehouse",
            "warehouse_name",
            "ref_no",
            "remark",
            "review_status",
            "posting_status",
            "posting_note",
            "trigger",
            "lines",
            "is_assigned_to_me",
            "can_claim",
            "can_start",
            "can_record",
            "created_at",
            "released_at",
            "started_at",
            "finished_at",
        ]

    @staticmethod
    def get_trigger(obj):
        try:
            return obj.replenishtaskextra.trigger
        except ObjectDoesNotExist:
            return ""

    def _assigned(self, obj):
        user = self.context["request"].user
        return obj.assignments.filter(assignee=user, finished_at__isnull=True).exists()

    def get_is_assigned_to_me(self, obj):
        return self._assigned(obj)

    def get_can_claim(self, obj):
        user = self.context["request"].user
        return (
            user.has_perm("tasking.claim_task_as_wh_operator")
            and obj.status == WmsTask.Status.RELEASED
            and not obj.assignments.filter(finished_at__isnull=True).exists()
        )

    def get_can_start(self, obj):
        user = self.context["request"].user
        return (
            user.has_perm("tasking.claim_task_as_wh_operator")
            and obj.status == WmsTask.Status.RELEASED
            and self._assigned(obj)
        )

    def get_can_record(self, obj):
        user = self.context["request"].user
        return (
            user.has_perm("tasking.claim_task_as_wh_operator")
            and obj.status == WmsTask.Status.IN_PROGRESS
            and self._assigned(obj)
        )

    @staticmethod
    def get_lines(obj):
        data = []
        for line in obj.lines.all():
            meta = line.plan_meta or {}
            data.append(
                {
                    "id": line.pk,
                    "product_id": line.product_id,
                    "product_code": getattr(line.product, "code", ""),
                    "product_sku": getattr(line.product, "sku", ""),
                    "product_name": getattr(line.product, "name", ""),
                    "serial_control": bool(
                        getattr(line.product, "serial_control", False)
                    ),
                    "from_location_id": line.from_location_id,
                    "from_location_code": getattr(line.from_location, "code", ""),
                    "to_location_id": line.to_location_id,
                    "to_location_code": getattr(line.to_location, "code", ""),
                    "qty_plan": str(line.qty_plan),
                    "qty_done": str(line.qty_done),
                    "qty_pending": str(line.qty_pending),
                    "status": line.status,
                    "finished_at": line.finished_at,
                    "lot_no": meta.get("lot_no") or "",
                    "exp_date": meta.get("exp_date"),
                    "serial_no": meta.get("serial_no") or "",
                }
            )
        return data
