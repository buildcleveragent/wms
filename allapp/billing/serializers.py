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
