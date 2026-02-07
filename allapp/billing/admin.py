from django.contrib import admin, messages
from django.urls import path
from django.shortcuts import redirect
from django.db import transaction
import datetime

from .models import BillingRule, BillingRuleTier, BillingEvent, BillingAccrual, BillingPeriod, Bill, BillLine, BillingMetricDaily

class BillingRuleTierInline(admin.TabularInline):
    model = BillingRuleTier
    extra = 0
    fields = ("threshold_from","threshold_to","unit_price","percent_rate","note")

@admin.register(BillingRule)
class BillingRuleAdmin(admin.ModelAdmin):
    list_display = ("owner","charge_type","calc_method","ladder_mode","unit_price","cap_mode","cap_amount","bundle_key","bundle_scope","bundle_type","bundle_price","currency","taxable","tax_rate","active","priority")
    list_filter = ("charge_type","calc_method","ladder_mode","cap_mode","bundle_scope","bundle_type","active","owner")
    search_fields = ("note","bundle_key")
    inlines = [BillingRuleTierInline]

@admin.register(BillingEvent)
class BillingEventAdmin(admin.ModelAdmin):
    list_display = ("owner","charge_type","service_date","quantity","quantity_uom","event_fp","created_at")
    list_filter = ("charge_type","owner","service_date")
    search_fields = ("event_fp",)

@admin.register(BillingAccrual)
class BillingAccrualAdmin(admin.ModelAdmin):
    list_display = ("owner","charge_type","service_date","bundle_key","quantity","unit_price","amount","tax_amount","status","period")
    list_filter = ("status","charge_type","owner","service_date","bundle_key")
    search_fields = ("acc_fingerprint",)

class BillLineInline(admin.TabularInline):
    model = BillLine
    extra = 0
    readonly_fields = ("accrual","charge_type","service_date","quantity","unit_price","amount","tax_amount","description")

@admin.register(Bill)
class BillAdmin(admin.ModelAdmin):
    list_display = ("invoice_no","owner","period","issue_date","due_date","subtotal","tax_total","total","status")
    list_filter = ("status","owner","issue_date")
    inlines = [BillLineInline]

@admin.register(BillingMetricDaily)
class BillingMetricDailyAdmin(admin.ModelAdmin):
    list_display = ("owner","service_date","metric_type","value","source","note","created_at")
    list_filter = ("metric_type","owner","service_date")
    search_fields = ("note","source")

@admin.register(BillingPeriod)
class BillingPeriodAdmin(admin.ModelAdmin):
    list_display = ("owner","label","start_date","end_date","status","currency")
    list_filter = ("status","owner")

    def get_urls(self):
        urls = super().get_urls()
        my = [
            path("<int:pk>/accrue-storage/", self.admin_site.admin_view(self.accrue_storage_view), name="billingperiod_accrue_storage"),
            path("<int:pk>/accrue-orders-posted/", self.admin_site.admin_view(self.accrue_orders_posted_view), name="billingperiod_accrue_orders_posted"),
            path("<int:pk>/lock/", self.admin_site.admin_view(self.lock_view), name="billingperiod_lock"),
            path("<int:pk>/invoice/", self.admin_site.admin_view(self.invoice_view), name="billingperiod_invoice"),
        ]
        return my + urls

    @transaction.atomic
    def accrue_storage_view(self, request, pk: int):
        period = self.get_object(request, pk)
        if not period:
            self.message_user(request, "账期不存在。", level=messages.ERROR)
            return redirect("admin:billing_billingperiod_changelist")
        from allapp.billing.services import accrue_storage_for_date, accrue_metrics_for_date
        d = period.start_date
        total_ev = total_acc = 0
        while d <= period.end_date:
            ev1, acc1 = accrue_storage_for_date(period.owner_id, period.warehouse_id, d, by_user=request.user)
            ev2, acc2 = accrue_metrics_for_date(period.owner_id, period.warehouse_id, d, by_user=request.user)
            total_ev += (ev1 + ev2); total_acc += (acc1 + acc2)
            d += datetime.timedelta(days=1)
        self.message_user(request, f"仓储计费完成：事件 {total_ev} 条，应计 {total_acc} 条。", level=messages.SUCCESS)
        return redirect(f"../../{pk}/change/")

    @transaction.atomic
    def accrue_orders_posted_view(self, request, pk: int):
        period = self.get_object(request, pk)
        if not period:
            self.message_user(request, "账期不存在。", level=messages.ERROR)
            return redirect("admin:billing_billingperiod_changelist")
        try:
            from allapp.billing.services import accrue_order_processing_from_posted
            ev, acc = accrue_order_processing_from_posted(period.owner_id, period.warehouse_id, period.start_date, period.end_date, by_user=request.user)
        except Exception as e:
            self.message_user(request, f"订单处理费计提失败：{e}", level=messages.ERROR)
            return redirect(f"../../{pk}/change/")
        self.message_user(request, f"订单处理费（事实）计提完成：事件 {ev} 条，应计 {acc} 条。", level=messages.SUCCESS)
        return redirect(f"../../{pk}/change/")

    @transaction.atomic
    def lock_view(self, request, pk: int):
        period = self.get_object(request, pk)
        if not period:
            self.message_user(request, "账期不存在。", level=messages.ERROR)
            return redirect("admin:billing_billingperiod_changelist")
        from allapp.billing.services import lock_period
        lock_period(period.owner_id, period.warehouse_id, period.label, period.start_date, period.end_date)
        self.message_user(request, "账期已锁定并关闭（OPEN→CLOSED），并已按账期口径应用封顶/打包。", level=messages.SUCCESS)
        return redirect(f"../../{pk}/change/")

    @transaction.atomic
    def invoice_view(self, request, pk: int):
        period = self.get_object(request, pk)
        if not period:
            self.message_user(request, "账期不存在。", level=messages.ERROR)
            return redirect("admin:billing_billingperiod_changelist")
        from allapp.billing.services import generate_invoice_for_period
        seq = Bill.objects.filter(period__owner=period.owner, period__warehouse=period.warehouse).count() + 1
        invoice_no = f"INV-{period.label}-{period.owner_id}-{seq:04d}"
        try:
            bill = generate_invoice_for_period(period, invoice_no=invoice_no)
        except ValueError as e:
            self.message_user(request, f"生成失败：{e}", level=messages.WARNING)
            return redirect(f"../../{pk}/change/")
        self.message_user(request, f"已生成发票 {bill.invoice_no}（金额 {bill.total}）。", level=messages.SUCCESS)
        return redirect(f"../../{pk}/change/")
