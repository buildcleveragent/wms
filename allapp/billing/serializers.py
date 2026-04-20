from rest_framework import serializers

from .models import (
    Bill,
    BillLine,
    BillingAccrual,
    BillingEvent,
    BillingMetricDaily,
    BillingPeriod,
    BillingRule,
    BillingRuleTier,
)
from .enums import MetricType


class BillingRuleTierSerializer(serializers.ModelSerializer):
    class Meta:
        model = BillingRuleTier
        fields = [
            "id",
            "rule",
            "threshold_from",
            "threshold_to",
            "unit_price",
            "percent_rate",
            "note",
        ]


class BillingRuleSerializer(serializers.ModelSerializer):
    owner_name = serializers.CharField(source="owner.name", read_only=True)
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)
    tiers = BillingRuleTierSerializer(many=True, read_only=True)

    class Meta:
        model = BillingRule
        fields = [
            "id",
            "owner",
            "owner_name",
            "warehouse",
            "warehouse_name",
            "charge_type",
            "calc_method",
            "ladder_mode",
            "unit_price",
            "currency",
            "taxable",
            "tax_rate",
            "min_charge",
            "cap_mode",
            "cap_amount",
            "bundle_key",
            "bundle_scope",
            "bundle_type",
            "bundle_price",
            "active",
            "priority",
            "effective_from",
            "effective_to",
            "note",
            "tiers",
        ]


class BillingMetricDailySerializer(serializers.ModelSerializer):
    owner_name = serializers.CharField(source="owner.name", read_only=True)
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)

    class Meta:
        model = BillingMetricDaily
        fields = [
            "id",
            "owner",
            "owner_name",
            "warehouse",
            "warehouse_name",
            "service_date",
            "metric_type",
            "value",
            "source",
            "note",
            "created_at",
        ]
        read_only_fields = ["created_at"]


class BillingEventSerializer(serializers.ModelSerializer):
    owner_name = serializers.CharField(source="owner.name", read_only=True)
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)
    task_no = serializers.CharField(source="task.task_no", read_only=True)

    class Meta:
        model = BillingEvent
        fields = [
            "id",
            "owner",
            "owner_name",
            "warehouse",
            "warehouse_name",
            "charge_type",
            "service_date",
            "task",
            "task_no",
            "task_line",
            "scan_log",
            "posting_journal",
            "quantity",
            "quantity_uom",
            "event_fp",
            "created_at",
        ]
        read_only_fields = fields


class BillingAccrualSerializer(serializers.ModelSerializer):
    owner_name = serializers.CharField(source="owner.name", read_only=True)
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)
    period_label = serializers.CharField(source="period.label", read_only=True)
    rule_calc_method = serializers.CharField(source="rule.calc_method", read_only=True)
    rule_note = serializers.CharField(source="rule.note", read_only=True)
    event_fp = serializers.CharField(source="event.event_fp", read_only=True)
    created_by_username = serializers.CharField(source="created_by.username", read_only=True)

    class Meta:
        model = BillingAccrual
        fields = [
            "id",
            "owner",
            "owner_name",
            "warehouse",
            "warehouse_name",
            "period",
            "period_label",
            "charge_type",
            "rule",
            "rule_calc_method",
            "rule_note",
            "service_date",
            "currency",
            "quantity",
            "unit_price",
            "amount",
            "tax_amount",
            "status",
            "event",
            "event_fp",
            "bundle_key",
            "acc_fingerprint",
            "created_at",
            "created_by",
            "created_by_username",
        ]
        read_only_fields = fields


class BillingAccrualDetailSerializer(BillingAccrualSerializer):
    event_charge_type = serializers.CharField(source="event.charge_type", read_only=True)
    event_service_date = serializers.DateField(source="event.service_date", read_only=True)
    event_task = serializers.IntegerField(source="event.task_id", read_only=True)
    event_task_no = serializers.CharField(source="event.task.task_no", read_only=True)
    event_task_line = serializers.IntegerField(source="event.task_line_id", read_only=True)
    event_scan_log = serializers.IntegerField(source="event.scan_log_id", read_only=True)
    event_posting_journal = serializers.IntegerField(source="event.posting_journal_id", read_only=True)
    event_quantity = serializers.DecimalField(
        source="event.quantity",
        max_digits=18,
        decimal_places=4,
        read_only=True,
    )
    event_quantity_uom = serializers.CharField(source="event.quantity_uom", read_only=True)
    is_reversal = serializers.BooleanField(read_only=True)
    reversal_of = serializers.IntegerField(source="reversal_of_id", read_only=True)
    pre_adjustment_amount = serializers.DecimalField(
        max_digits=18,
        decimal_places=2,
        read_only=True,
    )
    bill_id = serializers.SerializerMethodField()
    bill_invoice_no = serializers.SerializerMethodField()
    bill_status = serializers.SerializerMethodField()
    bill_line_description = serializers.SerializerMethodField()

    class Meta(BillingAccrualSerializer.Meta):
        fields = BillingAccrualSerializer.Meta.fields + [
            "event_charge_type",
            "event_service_date",
            "event_task",
            "event_task_no",
            "event_task_line",
            "event_scan_log",
            "event_posting_journal",
            "event_quantity",
            "event_quantity_uom",
            "is_reversal",
            "reversal_of",
            "pre_adjustment_amount",
            "bill_id",
            "bill_invoice_no",
            "bill_status",
            "bill_line_description",
        ]
        read_only_fields = fields

    def _bill_line(self, obj):
        if hasattr(obj, "_billing_detail_bill_line_loaded"):
            return getattr(obj, "_billing_detail_bill_line", None)

        prefetched = getattr(obj, "_prefetched_objects_cache", {})
        bill_lines = prefetched.get("billline_set")
        bill_line = bill_lines[0] if bill_lines else None
        if bill_lines is None:
            bill_line = obj.billline_set.select_related("bill").first()

        obj._billing_detail_bill_line = bill_line
        obj._billing_detail_bill_line_loaded = True
        return bill_line

    def get_bill_id(self, obj):
        bill_line = self._bill_line(obj)
        return getattr(bill_line, "bill_id", None)

    def get_bill_invoice_no(self, obj):
        bill_line = self._bill_line(obj)
        return getattr(getattr(bill_line, "bill", None), "invoice_no", None)

    def get_bill_status(self, obj):
        bill_line = self._bill_line(obj)
        return getattr(getattr(bill_line, "bill", None), "status", None)

    def get_bill_line_description(self, obj):
        bill_line = self._bill_line(obj)
        return getattr(bill_line, "description", "")


class BillingPeriodSerializer(serializers.ModelSerializer):
    owner_name = serializers.CharField(source="owner.name", read_only=True)
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)
    bill_id = serializers.SerializerMethodField()

    class Meta:
        model = BillingPeriod
        fields = [
            "id",
            "owner",
            "owner_name",
            "warehouse",
            "warehouse_name",
            "label",
            "start_date",
            "end_date",
            "status",
            "currency",
            "bill_id",
        ]
        read_only_fields = ["status", "bill_id"]

    def get_bill_id(self, obj):
        return obj.bill_set.order_by("id").values_list("id", flat=True).first()


class BillLineSerializer(serializers.ModelSerializer):
    accrual_fingerprint = serializers.CharField(source="accrual.acc_fingerprint", read_only=True)

    class Meta:
        model = BillLine
        fields = [
            "id",
            "accrual",
            "accrual_fingerprint",
            "charge_type",
            "service_date",
            "quantity",
            "unit_price",
            "amount",
            "tax_amount",
            "description",
        ]
        read_only_fields = fields


class BillListSerializer(serializers.ModelSerializer):
    owner_name = serializers.CharField(source="owner.name", read_only=True)
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)
    period_label = serializers.CharField(source="period.label", read_only=True)
    line_count = serializers.SerializerMethodField()

    class Meta:
        model = Bill
        fields = [
            "id",
            "owner",
            "owner_name",
            "warehouse",
            "warehouse_name",
            "period",
            "period_label",
            "invoice_no",
            "issue_date",
            "due_date",
            "currency",
            "subtotal",
            "tax_total",
            "total",
            "status",
            "memo",
            "line_count",
        ]
        read_only_fields = fields

    def get_line_count(self, obj):
        return obj.lines.count()


class BillDetailSerializer(BillListSerializer):
    lines = BillLineSerializer(many=True, read_only=True)

    class Meta(BillListSerializer.Meta):
        fields = BillListSerializer.Meta.fields + ["lines"]
        read_only_fields = fields


class BillingPeriodInvoiceSerializer(serializers.Serializer):
    invoice_no = serializers.CharField(required=False, allow_blank=False, max_length=40)
    issue_date = serializers.DateField(required=False)
    due_date = serializers.DateField(required=False, allow_null=True)


class UnlockPeriodSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, default="")


class BillingMetricGenerateSerializer(serializers.Serializer):
    service_date = serializers.DateField(required=False)
    start_date = serializers.DateField(required=False)
    end_date = serializers.DateField(required=False)
    owner = serializers.IntegerField(required=False)
    warehouse = serializers.IntegerField(required=False)
    metric_types = serializers.ListField(
        child=serializers.ChoiceField(choices=MetricType.choices),
        required=False,
        allow_empty=False,
    )
    overwrite = serializers.BooleanField(required=False, default=False)
    allow_area_fallback = serializers.BooleanField(required=False, default=False)

    def validate(self, attrs):
        service_date = attrs.get("service_date")
        start_date = attrs.get("start_date")
        end_date = attrs.get("end_date")

        if service_date and (start_date or end_date):
            raise serializers.ValidationError("service_date 与 start_date/end_date 只能二选一。")
        if service_date:
            attrs["start_date"] = service_date
            attrs["end_date"] = service_date
            return attrs
        if not start_date or not end_date:
            raise serializers.ValidationError("必须提供 service_date，或同时提供 start_date 和 end_date。")
        if start_date > end_date:
            raise serializers.ValidationError("start_date 不能晚于 end_date。")
        return attrs
