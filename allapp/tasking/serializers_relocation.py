from decimal import Decimal

from rest_framework import serializers

from allapp.tasking.models import RelocationRequest, WmsTask


class RelocationLayerInputSerializer(serializers.Serializer):
    inventory_detail_id = serializers.IntegerField(min_value=1)
    qty = serializers.DecimalField(
        max_digits=18, decimal_places=4, min_value=Decimal("0.0001")
    )
    to_location_id = serializers.IntegerField(min_value=1)
    to_container_id = serializers.IntegerField(min_value=1, required=False, allow_null=True)


class RelocationRequestCreateSerializer(serializers.Serializer):
    owner_id = serializers.IntegerField(min_value=1)
    warehouse_id = serializers.IntegerField(min_value=1)
    mode = serializers.ChoiceField(choices=RelocationRequest.Mode.choices)
    reason = serializers.CharField(max_length=200)
    lines = RelocationLayerInputSerializer(many=True, required=False)
    source_container_id = serializers.IntegerField(min_value=1, required=False)
    to_location_id = serializers.IntegerField(min_value=1, required=False)
    target_parent_container_id = serializers.IntegerField(
        min_value=1, required=False, allow_null=True
    )

    def validate(self, attrs):
        if attrs["mode"] == RelocationRequest.Mode.LAYER:
            if not attrs.get("lines"):
                raise serializers.ValidationError({"lines": "库存层模式至少需要一条明细。"})
            if attrs.get("source_container_id"):
                raise serializers.ValidationError("库存层模式不能同时提交整容器字段。")
        else:
            if not attrs.get("source_container_id") or not attrs.get("to_location_id"):
                raise serializers.ValidationError("整容器模式必须提供来源容器和目标库位。")
            if attrs.get("lines"):
                raise serializers.ValidationError("整容器模式不能同时提交库存层明细。")
        return attrs


class RelocationReviewSerializer(serializers.Serializer):
    note = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")


class RelocationExceptionSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=30, required=False, default="OPERATION_EXCEPTION")
    note = serializers.CharField(max_length=200)


class RelocationRecordSerializer(serializers.Serializer):
    request_id = serializers.RegexField(regex=r"^[A-Za-z0-9._:-]{8,64}$", max_length=64)
    line_id = serializers.IntegerField(min_value=1)
    from_location_code = serializers.CharField(max_length=60)
    to_location_code = serializers.CharField(max_length=60)
    from_container_code = serializers.CharField(
        max_length=60, required=False, allow_blank=True, default=""
    )
    to_container_code = serializers.CharField(
        max_length=60, required=False, allow_blank=True, default=""
    )
    product_code = serializers.CharField(max_length=128)
    qty = serializers.DecimalField(
        max_digits=18, decimal_places=4, min_value=Decimal("0.0001")
    )
    serial_no = serializers.CharField(max_length=80, required=False, allow_blank=True, default="")


class RelocationRequestSerializer(serializers.ModelSerializer):
    owner_name = serializers.CharField(source="owner.name", read_only=True)
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)
    requested_by_name = serializers.CharField(source="created_by.username", read_only=True)
    reviewed_by_name = serializers.CharField(source="reviewed_by.username", read_only=True)
    generated_task_no = serializers.CharField(source="generated_task.task_no", read_only=True)
    source_container_no = serializers.CharField(source="source_container.container_no", read_only=True)
    to_location_code = serializers.CharField(source="to_location.code", read_only=True)
    target_parent_container_no = serializers.CharField(
        source="target_parent_container.container_no", read_only=True
    )
    lines = serializers.SerializerMethodField()

    class Meta:
        model = RelocationRequest
        fields = [
            "id", "owner", "owner_name", "warehouse", "warehouse_name", "mode", "trigger",
            "reason", "status", "source_container", "source_container_no", "to_location",
            "to_location_code", "target_parent_container", "target_parent_container_no",
            "requested_by_name", "reviewed_by_name", "reviewed_at", "review_note",
            "generated_task", "generated_task_no", "lines", "created_at",
        ]
        read_only_fields = fields

    @staticmethod
    def get_lines(obj):
        result = []
        for line in obj.lines.all():
            detail = line.inventory_detail
            result.append(
                {
                    "id": line.pk,
                    "inventory_detail_id": line.inventory_detail_id,
                    "product_id": detail.product_id,
                    "product_code": getattr(detail.product, "code", ""),
                    "product_name": getattr(detail.product, "name", ""),
                    "from_location_code": getattr(detail.location, "code", ""),
                    "from_container_no": getattr(detail.container, "container_no", ""),
                    "to_location_id": line.to_location_id,
                    "to_location_code": getattr(line.to_location, "code", ""),
                    "to_container_id": line.to_container_id,
                    "to_container_no": getattr(line.to_container, "container_no", ""),
                    "requested_qty": str(line.requested_qty),
                    "source_snapshot": line.source_snapshot,
                }
            )
        return result


class RelocationTaskSerializer(serializers.ModelSerializer):
    owner_name = serializers.CharField(source="owner.name", read_only=True)
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)
    trigger = serializers.CharField(source="reloctaskextra.trigger", read_only=True)
    execution_state = serializers.CharField(source="reloctaskextra.execution_state", read_only=True)
    exception_code = serializers.CharField(source="reloctaskextra.exception_code", read_only=True)
    exception_note = serializers.CharField(source="reloctaskextra.exception_note", read_only=True)
    lines = serializers.SerializerMethodField()
    is_assigned_to_me = serializers.SerializerMethodField()
    can_claim = serializers.SerializerMethodField()
    can_start = serializers.SerializerMethodField()
    can_record = serializers.SerializerMethodField()

    class Meta:
        model = WmsTask
        fields = [
            "id", "task_no", "status", "priority", "owner", "owner_name", "warehouse",
            "warehouse_name", "ref_no", "remark", "review_status", "posting_status",
            "posting_note", "trigger", "execution_state", "exception_code", "exception_note",
            "lines", "is_assigned_to_me", "can_claim", "can_start", "can_record",
            "created_at", "released_at", "started_at", "finished_at",
        ]

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
        return obj.status == WmsTask.Status.RELEASED and self._assigned(obj)

    def get_can_record(self, obj):
        try:
            normal = obj.reloctaskextra.execution_state != "EXCEPTION"
        except Exception:
            normal = False
        return obj.status == WmsTask.Status.IN_PROGRESS and self._assigned(obj) and normal

    @staticmethod
    def get_lines(obj):
        result = []
        for line in obj.lines.all():
            meta = line.plan_meta or {}
            try:
                extra = line.reloclineextra
            except Exception:
                extra = None
            result.append(
                {
                    "id": line.pk,
                    "product_id": line.product_id,
                    "product_code": getattr(line.product, "code", ""),
                    "product_sku": getattr(line.product, "sku", ""),
                    "product_name": getattr(line.product, "name", ""),
                    "serial_control": bool(getattr(line.product, "serial_control", False)),
                    "from_location_code": getattr(line.from_location, "code", ""),
                    "to_location_code": getattr(line.to_location, "code", ""),
                    "from_container_no": getattr(getattr(extra, "from_container", None), "container_no", ""),
                    "to_container_no": getattr(getattr(extra, "to_container", None), "container_no", ""),
                    "qty_plan": str(line.qty_plan),
                    "qty_done": str(line.qty_done),
                    "qty_pending": str(line.qty_pending),
                    "status": line.status,
                    "finished_at": line.finished_at,
                    "batch_no": meta.get("batch_no") or "",
                    "expiry_date": meta.get("expiry_date"),
                    "serial_no": meta.get("serial_no") or "",
                }
            )
        return result
