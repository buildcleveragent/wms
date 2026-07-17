from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.http import HttpResponseNotAllowed
from django.urls import path
from django.shortcuts import redirect
from django.db import transaction
import datetime

from allapp.accounts.access import AccessScope
from allapp.accounts.audit import object_snapshot, record_audit_event
from allapp.baseinfo.models import Owner
from allapp.locations.models import Warehouse

from .models import BillingRule, BillingRuleTier, BillingEvent, BillingAccrual, BillingPeriod, Bill, BillLine, BillingMetricDaily, BillingJobRun


class ScopedBillingAdminMixin:
    """Apply the same fail-closed owner/warehouse boundary used by billing APIs."""

    def _scope(self, request):
        return AccessScope.for_user(request.user)

    def _has_financial_access(self, request):
        user = request.user
        if user.is_superuser:
            return True
        scope = self._scope(request)
        return bool(
            scope.is_valid
            and (
                (scope.owner_ids and user.has_perm("accounts.view_owner_financials"))
                or (
                    scope.warehouse_ids
                    and user.has_perm("reports.view_warehouse_finance")
                )
            )
        )

    def has_module_permission(self, request):
        return self._has_financial_access(request) and super().has_module_permission(request)

    def has_view_permission(self, request, obj=None):
        return self._has_financial_access(request) and super().has_view_permission(request, obj)

    def has_add_permission(self, request):
        return self._has_financial_access(request) and super().has_add_permission(request)

    def has_change_permission(self, request, obj=None):
        if not self._has_financial_access(request) or not super().has_change_permission(request, obj):
            return False
        return obj is None or self._scope(request).allows(
            owner_id=getattr(obj, "owner_id", None),
            warehouse_id=getattr(obj, "warehouse_id", None),
        )

    def has_delete_permission(self, request, obj=None):
        if not self._has_financial_access(request) or not super().has_delete_permission(request, obj):
            return False
        return obj is None or self._scope(request).allows(
            owner_id=getattr(obj, "owner_id", None),
            warehouse_id=getattr(obj, "warehouse_id", None),
        )

    def get_queryset(self, request):
        return self._scope(request).filter_queryset(
            super().get_queryset(request),
            owner_field="owner_id",
            warehouse_field="warehouse_id",
        )

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        scope = self._scope(request)
        if not scope.is_global and db_field.name == "owner":
            # Owner roles are pinned to their owner. Warehouse roles may select
            # an owner only while the record remains in an authorized warehouse.
            kwargs["queryset"] = (
                Owner.objects.filter(id__in=scope.owner_ids)
                if scope.owner_ids
                else Owner.objects.all() if scope.warehouse_ids else Owner.objects.none()
            )
        elif not scope.is_global and db_field.name == "warehouse":
            # The inverse is safe for owner roles because save_model validates
            # the owner boundary independently of the selected warehouse.
            kwargs["queryset"] = (
                Warehouse.objects.filter(id__in=scope.warehouse_ids)
                if scope.warehouse_ids
                else Warehouse.objects.all() if scope.owner_ids else Warehouse.objects.none()
            )
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def save_model(self, request, obj, form, change):
        scope = self._scope(request)
        if not scope.allows(
            owner_id=getattr(obj, "owner_id", None),
            warehouse_id=getattr(obj, "warehouse_id", None),
        ):
            raise PermissionDenied("No access to this billing scope.")
        before = object_snapshot(type(obj).objects.get(pk=obj.pk)) if change else {}
        super().save_model(request, obj, form, change)
        record_audit_event(
            action="UPDATE" if change else "CREATE",
            module="billing.admin",
            request=request,
            obj=obj,
            before=before,
            after=object_snapshot(obj),
        )

class BillingRuleTierInline(admin.TabularInline):
    model = BillingRuleTier
    extra = 0
    fields = ("threshold_from","threshold_to","unit_price","percent_rate","note")

@admin.register(BillingRule)
class BillingRuleAdmin(ScopedBillingAdminMixin, admin.ModelAdmin):
    list_display = ("owner","warehouse","charge_type","calc_method","ladder_mode","unit_price","cap_mode","cap_amount","bundle_key","bundle_scope","bundle_type","bundle_price","currency","taxable","tax_rate","active","priority")
    list_filter = ("charge_type","calc_method","ladder_mode","cap_mode","bundle_scope","bundle_type","active","owner","warehouse")
    search_fields = ("note","bundle_key")
    inlines = [BillingRuleTierInline]

@admin.register(BillingEvent)
class BillingEventAdmin(ScopedBillingAdminMixin, admin.ModelAdmin):
    list_display = ("owner","warehouse","charge_type","service_date","quantity","quantity_uom","event_fp","created_at")
    list_filter = ("charge_type","owner","warehouse","service_date")
    search_fields = ("event_fp",)

@admin.register(BillingAccrual)
class BillingAccrualAdmin(ScopedBillingAdminMixin, admin.ModelAdmin):
    list_display = ("owner","warehouse","charge_type","service_date","bundle_key","quantity","unit_price","amount","tax_amount","status","period")
    list_filter = ("status","charge_type","owner","warehouse","service_date","bundle_key")
    search_fields = ("acc_fingerprint",)

class BillLineInline(admin.TabularInline):
    model = BillLine
    extra = 0
    readonly_fields = ("accrual","charge_type","service_date","quantity","unit_price","amount","tax_amount","description")

@admin.register(Bill)
class BillAdmin(ScopedBillingAdminMixin, admin.ModelAdmin):
    list_display = ("invoice_no","owner","warehouse","period","issue_date","due_date","subtotal","tax_total","total","status")
    list_filter = ("status","owner","warehouse","issue_date")
    inlines = [BillLineInline]

@admin.register(BillingMetricDaily)
class BillingMetricDailyAdmin(ScopedBillingAdminMixin, admin.ModelAdmin):
    list_display = ("owner","warehouse","service_date","metric_type","value","source","note","created_at")
    list_filter = ("metric_type","owner","warehouse","service_date")
    search_fields = ("note","source")

@admin.register(BillingJobRun)
class BillingJobRunAdmin(ScopedBillingAdminMixin, admin.ModelAdmin):
    list_display = ("job_name","owner","warehouse","service_date","status","attempts","started_at","finished_at")
    list_filter = ("job_name","status","service_date","owner")
    search_fields = ("message",)
    readonly_fields = ("job_name","owner","warehouse","service_date","status","attempts","started_at","finished_at","message","summary","created_at","updated_at")

@admin.register(BillingPeriod)
class BillingPeriodAdmin(ScopedBillingAdminMixin, admin.ModelAdmin):
    list_display = ("owner","warehouse","label","start_date","end_date","status","currency")
    list_filter = ("status","owner","warehouse")

    def get_urls(self):
        urls = super().get_urls()
        my = [
            path("<int:pk>/accrue-storage/", self.admin_site.admin_view(self.accrue_storage_view), name="billingperiod_accrue_storage"),
            path("<int:pk>/accrue-orders-posted/", self.admin_site.admin_view(self.accrue_orders_posted_view), name="billingperiod_accrue_orders_posted"),
            path("<int:pk>/lock/", self.admin_site.admin_view(self.lock_view), name="billingperiod_lock"),
            path("<int:pk>/invoice/", self.admin_site.admin_view(self.invoice_view), name="billingperiod_invoice"),
            path("<int:pk>/unlock/", self.admin_site.admin_view(self.unlock_view), name="billingperiod_unlock"),
        ]
        return my + urls

    def _guard_status(self, request, period, allowed_statuses, action_label: str):
        if period.status in allowed_statuses:
            return True
        allowed = ", ".join(allowed_statuses)
        self.message_user(request, f"{action_label}仅允许在账期状态 {allowed} 时执行，当前为 {period.status}。", level=messages.ERROR)
        return False

    def _period_for_mutation(self, request, pk):
        if request.method != "POST":
            return None, HttpResponseNotAllowed(["POST"])
        period = self.get_object(request, pk)
        if not period:
            self.message_user(request, "账期不存在或不在授权范围内。", level=messages.ERROR)
            return None, redirect("admin:billing_billingperiod_changelist")
        if not self.has_change_permission(request, period):
            raise PermissionDenied("No permission to change this billing period.")
        return period, None

    def _audit_period_action(self, request, period, action, before, metadata=None):
        period.refresh_from_db()
        record_audit_event(
            action=action,
            module="billing.period",
            request=request,
            obj=period,
            before=before,
            after=object_snapshot(period),
            metadata=metadata or {},
        )

    def accrue_storage_view(self, request, pk: int):
        period, error = self._period_for_mutation(request, pk)
        if error:
            return error
        if not self._guard_status(request, period, ["OPEN"], "仓储计提"):
            return redirect(f"../../{pk}/change/")
        before = object_snapshot(period)
        from allapp.billing.services import accrue_storage_for_date, accrue_metrics_for_date, generate_metrics_for_date
        d = period.start_date
        total_ev = total_acc = 0
        total_metrics_created = total_metrics_updated = 0
        while d <= period.end_date:
            metric_summary = generate_metrics_for_date(period.owner_id, period.warehouse_id, d)
            total_metrics_created += metric_summary["created"]
            total_metrics_updated += metric_summary["updated"]
            ev1, acc1 = accrue_storage_for_date(period.owner_id, period.warehouse_id, d, by_user=request.user)
            ev2, acc2 = accrue_metrics_for_date(period.owner_id, period.warehouse_id, d, by_user=request.user)
            total_ev += (ev1 + ev2)
            total_acc += (acc1 + acc2)
            d += datetime.timedelta(days=1)
        self.message_user(
            request,
            f"仓储计费完成：指标新增 {total_metrics_created} 条，指标更新 {total_metrics_updated} 条，事件 {total_ev} 条，应计 {total_acc} 条。",
            level=messages.SUCCESS,
        )
        self._audit_period_action(
            request,
            period,
            "ACCRUE",
            before,
            {"events": total_ev, "accruals": total_acc},
        )
        return redirect(f"../../{pk}/change/")

    @transaction.atomic
    def accrue_orders_posted_view(self, request, pk: int):
        period, error = self._period_for_mutation(request, pk)
        if error:
            return error
        if not self._guard_status(request, period, ["OPEN"], "订单处理费计提"):
            return redirect(f"../../{pk}/change/")
        before = object_snapshot(period)
        try:
            from allapp.billing.services import accrue_order_processing_from_posted
            ev, acc = accrue_order_processing_from_posted(period.owner_id, period.warehouse_id, period.start_date, period.end_date, by_user=request.user)
        except Exception as e:
            self.message_user(request, f"订单处理费计提失败：{e}", level=messages.ERROR)
            return redirect(f"../../{pk}/change/")
        self.message_user(request, f"订单处理费（事实）计提完成：事件 {ev} 条，应计 {acc} 条。", level=messages.SUCCESS)
        self._audit_period_action(
            request, period, "ACCRUE", before, {"events": ev, "accruals": acc}
        )
        return redirect(f"../../{pk}/change/")

    @transaction.atomic
    def lock_view(self, request, pk: int):
        period, error = self._period_for_mutation(request, pk)
        if error:
            return error
        if not self._guard_status(request, period, ["OPEN"], "关账"):
            return redirect(f"../../{pk}/change/")
        before = object_snapshot(period)
        from allapp.billing.services import lock_period
        try:
            lock_period(period.owner_id, period.warehouse_id, period.label, period.start_date, period.end_date)
        except ValueError as e:
            self.message_user(request, f"关账失败：{e}", level=messages.ERROR)
            return redirect(f"../../{pk}/change/")
        self.message_user(request, "账期已锁定并关闭（OPEN→CLOSED），并已按账期口径应用封顶/打包。", level=messages.SUCCESS)
        self._audit_period_action(request, period, "LOCK", before)
        return redirect(f"../../{pk}/change/")

    @transaction.atomic
    def invoice_view(self, request, pk: int):
        period, error = self._period_for_mutation(request, pk)
        if error:
            return error
        if not self._guard_status(request, period, ["CLOSED"], "生成发票"):
            return redirect(f"../../{pk}/change/")
        before = object_snapshot(period)
        from allapp.billing.services import generate_invoice_for_period
        seq = Bill.objects.filter(period__owner=period.owner, period__warehouse=period.warehouse).count() + 1
        invoice_no = f"INV-{period.label}-{period.owner_id}-{period.warehouse_id}-{seq:04d}"
        try:
            bill = generate_invoice_for_period(period, invoice_no=invoice_no)
        except ValueError as e:
            self.message_user(request, f"生成失败：{e}", level=messages.WARNING)
            return redirect(f"../../{pk}/change/")
        self.message_user(request, f"已生成发票 {bill.invoice_no}（金额 {bill.total}）。", level=messages.SUCCESS)
        self._audit_period_action(
            request, period, "INVOICE", before, {"bill_id": bill.pk, "invoice_no": bill.invoice_no}
        )
        return redirect(f"../../{pk}/change/")

    @transaction.atomic
    def unlock_view(self, request, pk: int):
        period, error = self._period_for_mutation(request, pk)
        if error:
            return error
        if not self._guard_status(request, period, ["CLOSED", "INVOICED"], "撤销关账"):
            return redirect(f"../../{pk}/change/")
        before = object_snapshot(period)
        from allapp.billing.services import unlock_period
        try:
            result = unlock_period(period, by_user=request.user, reason="admin unlock")
        except ValueError as e:
            self.message_user(request, f"撤销失败：{e}", level=messages.ERROR)
            return redirect(f"../../{pk}/change/")
        action = result["action"]
        if action == "direct_rollback":
            self.message_user(request, f"已直接回退，恢复 {result['accruals_reverted']} 条应计为 OPEN。", level=messages.SUCCESS)
        else:
            self.message_user(
                request,
                f"已红冲处理，创建 {result['reversal_accruals_created']} 条冲销记录"
                f"{'，发票 ' + result['bill_voided'] + ' 已作废' if result['bill_voided'] else ''}。",
                level=messages.SUCCESS,
            )
        self._audit_period_action(request, period, "UNLOCK", before, result)
        return redirect(f"../../{pk}/change/")
