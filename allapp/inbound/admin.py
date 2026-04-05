# inbound/admin.py
import logging

from django.urls import reverse
from django.contrib import admin, messages
from django.contrib.admin import SimpleListFilter
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.core.exceptions import PermissionDenied, ValidationError

from allapp.core.admin_base import AdvancedAdminBase, BaseReadonlyAdmin
from allapp.products.models import Product

from .models import (
    InboundOrder, InboundOrderLine,
    InboundReceipt, InboundReceiptLine,
    ReturnInspection,InboundOrderReturnInfo, Lot
)

# === 顶部 import（补充） ===
from django.contrib import admin
from django import forms
from django.http import JsonResponse
from django.urls import path, reverse
from django.core.exceptions import ValidationError
from django.db import models as dj_models

from allapp.baseinfo.models import Supplier
from allapp.products.models import Product
from .models import InboundOrder, InboundOrderLine

logger = logging.getLogger(__name__)

# === Admin 表单：一次性加载联动 JS ===
class InboundOrderAdminForm(forms.ModelForm):
    class Meta:
        model = InboundOrder
        fields = "__all__"

    class Media:
        # 一个脚本同时处理“供应商下拉”和“订单行产品下拉”的联动
        js = ("admin/inbound_owner_linkage.js",)

class InboundOrderLineInline(admin.TabularInline):
    model = InboundOrderLine

    extra = 1
    readonly_fields = ("get_base_uom","line_no",)
    fields = [
        "line_no",
        "product",
        "base_qty", "get_base_uom", "base_price",
        "aux_qty", "aux_uom", "aux_price",
        "lot_no", "min_remaining_days", "expiry_not_earlier_than",
        "note",
    ]

    # autocomplete_fields = ["product"]

    def get_base_uom(self, obj):
        return obj.product.base_uom if obj and obj.product else None

    get_base_uom.short_description = "基本单位"

    def get_formset(self, request, obj=None, **kwargs):
        FormSet = super().get_formset(request, obj, **kwargs)

        if obj:  # 当前正在编辑的 InboundOrder 已经保存
            owner_id = obj.owner_id  # 获取当前 InboundOrder 对象的货主 ID
            logger.debug("inbound.admin.inline_formset order_id=%s owner_id=%s", obj.id, owner_id)

            # 自定义表单类
            class FilteredForm(FormSet.form):
                def __init__(self, *args, **kw):
                    super().__init__(*args, **kw)
                    if "product" in self.fields:
                        # 根据货主ID过滤商品
                        self.fields["product"].queryset = Product.objects.filter(owner_id=owner_id)
                        logger.debug(
                            "inbound.admin.inline_formset.products_filtered owner_id=%s queryset_count=%s",
                            owner_id,
                            self.fields["product"].queryset.count(),
                        )

            FormSet.form = FilteredForm  # 替换表单类

        return FormSet

    class Media:
        # 冗余加载同一脚本（主表单已加载，此处可有可无，不会重复执行）
        js = ("admin/inbound_owner_linkage.js",)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "product":
            # 从主 Admin 挂载的 request._inbound_owner_id 取 owner
            owner_id = getattr(request, "_inbound_owner_id", None) \
                       or request.POST.get("owner") \
                       or request.GET.get("owner") \
                       or request.GET.get("owner__id__exact")

            if owner_id:
                kwargs["queryset"] = Product.objects.filter(owner_id=owner_id)
            else:
                kwargs["queryset"] = Product.objects.none()

            # 为前端 JS 注入数据源 URL + 统一 class 标记
            widget = (kwargs.get("widget") or db_field.formfield().widget)
            try:
                url = reverse("admin:inbound_inboundorder_product_options")
                widget.attrs["data-source-url"] = url
                widget.attrs["class"] = (widget.attrs.get("class", "") + " vProductByOwner").strip()
                kwargs["widget"] = widget
            except Exception:
                pass

        return super().formfield_for_foreignkey(db_field, request, **kwargs)



class InboundOrderLineAdmin(admin.ModelAdmin):

    # 使用 formfield_for_foreignkey 来过滤 product 字段
    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "product":
            # 获取当前的 InboundOrder 对象
            inbound_order_id = request.GET.get("inbound_order")  # 获取 URL 中的 inbound_order_id
            if inbound_order_id:
                inbound_order = InboundOrder.objects.get(id=inbound_order_id)
                owner_id = inbound_order.owner_id
                kwargs["queryset"] = Product.objects.filter(owner_id=owner_id)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

USE_SKIP_LOCKED = True


@admin.register(InboundOrder)
class InboundOrderAdmin(AdvancedAdminBase,BaseReadonlyAdmin):
    inlines = [InboundOrderLineInline]
    # 显示的字段
    list_display = (
        "order_no", "owner", "warehouse", "supplier", "biz_date",
        "submit_status", "approval_status", "is_closed", "approved_by_ownermanager", "approved_at_ownermanager"
    )
    # 显示在搜索框中的字段
    search_fields = ("order_no", "owner__name", "supplier__name", "order_no")

    # 筛选功能
    list_filter = (
        "submit_status", "approval_status", "is_closed",
        "owner", "warehouse", "supplier", "biz_date"
    )

    # 可编辑的字段
    list_editable = ()

    # 表单中字段的显示顺序
    fields = (
        "order_no", "inbound_type", "owner", "warehouse", "supplier", "biz_date",)

    # fields = (
    #     "order_no",  "inbound_type", "owner", "supplier", "biz_date","approval_status",
    #     "address", "memo", "eta", "delivery_method",
    #     "approved_by_ownermanager", "approved_at_ownermanager", "approved_by_warehouse", "approved_at_warehouse",
    #     "is_closed", "close_reason"
    # )
    readonly_fields = ['order_no',"submit_status","approval_status","approved_by_ownermanager", "approved_at_ownermanager", "approved_by_warehouse", "approved_at_warehouse",
        "is_closed", "close_reason"]
    # 使用 `ordering` 进行默认排序
    ordering = ("-biz_date", "-id")

    def _as_owner_mgr(self, request):
        return request.user.is_superuser or request.user.has_perm("inbound.approve_as_owner_manager")

    # —— 权限判断：货主业务员（或超管）才允许“提交” —— #
    def _as_owner_buyers(self, request):
        return request.user.is_superuser or request.user.has_perm("inbound.submit_as_owner_buyers")


    def _as_wh_mgr(self, request):
        return request.user.is_superuser or request.user.has_perm("inbound.approve_as_wh_manager")

    # ——动作：仅权限用户看得到——
    actions = ["action_owner_approve", "action_owner_reject", "action_wh_confirm", "action_wh_reject","action_owner_buyers_submit"]

    def get_actions(self, request):
        actions = super().get_actions(request)
        if not self._as_owner_mgr(request):
            actions.pop("action_owner_approve", None)
            actions.pop("action_owner_reject", None)
        if not self._as_wh_mgr(request):
            actions.pop("action_wh_confirm", None)
            actions.pop("action_wh_reject", None)

        if not self._as_owner_buyers(request):
            actions.pop("action_owner_buyers_submit", None)

        return actions

    @admin.action(description="货主管理员：审核通过")
    def action_owner_approve(self, request, queryset):
        if not self._as_owner_mgr(request):
            raise PermissionDenied

        ok, fail = 0, 0
        # 先取 pk，避免 queryset 在进入事务前被评估
        pks = list(queryset.values_list("pk", flat=True))

        with transaction.atomic():
            # 注意：select_for_update 必须在事务里
            qs = InboundOrder.objects.select_for_update().filter(pk__in=pks)
            for obj in qs:
                try:
                    obj.owner_approve(request.user)
                    ok += 1
                except ValidationError:
                    fail += 1

        if ok:   self.message_user(request, _(f"已通过 {ok} 条。"), level=messages.SUCCESS)
        if fail: self.message_user(request, _(f"{fail} 条不在可审核状态，已跳过。"), level=messages.WARNING)

    @admin.action(description="货主管理员：驳回")
    def action_owner_reject(self, request, queryset):
        if not self._as_owner_mgr(request):
            raise PermissionDenied

        pks = list(queryset.values_list("pk", flat=True))
        ok = fail = 0

        with transaction.atomic():
            qs = InboundOrder.objects.select_for_update(
                skip_locked=USE_SKIP_LOCKED
            ).filter(pk__in=pks)

            locked_count = qs.count()  # 统计拿到锁的数量（便于提示被跳过的行）
            for obj in qs:
                try:
                    obj.owner_reject(request.user)
                    ok += 1
                except ValidationError:
                    fail += 1

        skipped = len(pks) - locked_count
        if ok:
            self.message_user(request, _(f"已驳回 {ok} 条。"), level=messages.SUCCESS)
        if fail:
            self.message_user(request, _(f"{fail} 条状态不满足，已跳过。"), level=messages.WARNING)
        if skipped > 0:
            self.message_user(request, _(f"{skipped} 条记录被其他事务锁定，已跳过。"), level=messages.INFO)

    @admin.action(description="仓库管理员：确认通过")
    def action_wh_confirm(self, request, queryset):
        if not self._as_wh_mgr(request):
            raise PermissionDenied

        pks = list(queryset.values_list("pk", flat=True))
        ok = fail = 0
        fail_details = []  # 逐条记录失败原因

        with transaction.atomic():
            qs = InboundOrder.objects.select_for_update(
                skip_locked=USE_SKIP_LOCKED
            ).filter(pk__in=pks)

            locked_count = qs.count()
            for obj in qs:
                try:
                    obj.wh_confirm(request.user)
                    ok += 1
                except ValidationError as e:
                    fail += 1
                    # e.messages 是list，拼成一句；带上单号更直观
                    fail_details.append(f"{obj.order_no}：{'；'.join(e.messages)}")

        skipped = len(pks) - locked_count
        if ok:
            self.message_user(request, _(f"已确认通过 {ok} 条。"), level=messages.SUCCESS)
        # if fail:
        #     self.message_user(request, _(f"{fail} 条状态不满足，已跳过。"), level=messages.WARNING)

        if fail:
            # 逐条展示失败原因（多条消息更易读；如很多可分批发）
            for msg in fail_details[:20]:  # 避免一次消息过长，必要时截断
                self.message_user(request, msg, level=messages.ERROR)
            if len(fail_details) > 20:
                self.message_user(request, f"还有 {len(fail_details) - 20} 条失败原因已省略。", level=messages.WARNING)

        if skipped > 0:
            self.message_user(request, _(f"{skipped} 条记录被其他事务锁定，已跳过。"), level=messages.INFO)

    @admin.action(description="仓库管理员：驳回")
    def action_wh_reject(self, request, queryset):
        if not self._as_wh_mgr(request):
            raise PermissionDenied

        pks = list(queryset.values_list("pk", flat=True))
        ok = fail = 0

        with transaction.atomic():
            qs = InboundOrder.objects.select_for_update(
                skip_locked=USE_SKIP_LOCKED
            ).filter(pk__in=pks)

            locked_count = qs.count()
            for obj in qs:
                try:
                    obj.wh_reject(request.user)
                    ok += 1
                except ValidationError:
                    fail += 1

        skipped = len(pks) - locked_count
        if ok:
            self.message_user(request, _(f"已驳回 {ok} 条。"), level=messages.SUCCESS)
        if fail:
            self.message_user(request, _(f"{fail} 条状态不满足，已跳过。"), level=messages.WARNING)
        if skipped > 0:
            self.message_user(request, _(f"{skipped} 条记录被其他事务锁定，已跳过。"), level=messages.INFO)

    @admin.action(description="提交（货主业务员）")
    def action_owner_buyers_submit(self, request, queryset):
        if not self._as_owner_buyers(request):
            raise PermissionDenied

        pks = list(queryset.values_list("pk", flat=True))
        ok = fail = 0

        with transaction.atomic():
            qs = InboundOrder.objects.select_for_update(
                skip_locked=USE_SKIP_LOCKED
            ).filter(pk__in=pks)

            locked_count = qs.count()
            for obj in qs:
                try:
                    obj.submit_by_owner_buyers(request.user)
                    ok += 1
                except ValidationError as e:
                    fail += 1

        skipped = len(pks) - locked_count
        if ok:
            self.message_user(request, _(f"已提交 {ok} 条。"), level=messages.SUCCESS)
        if fail:
            self.message_user(request, _(f"{fail} 条状态不满足，已跳过。"), level=messages.WARNING)
        if skipped > 0:
            self.message_user(request, _(f"{skipped} 条记录被其他事务锁定，已跳过。"), level=messages.INFO)

    # 自定义保存行为
    def save_model(self, request, obj, form, change):
        if not obj.pk:
            # 创建时触发生成订单号
            obj.save()

        touched = set(form.changed_data)
        owner_fields = {"approved_by_ownermanager","approved_at_ownermanager"}
        wh_fields    = {"approved_by_warehouse","approved_at_warehouse"}
        status_field = {"approval_status"}

        # 1) 禁止任何人手改 approval_status（系统动作专属）
        if touched.intersection(status_field):
            raise PermissionDenied("状态由系统动作维护，禁止手工修改。")

        # 2) 只有“货主管理员”能改 owner 审核字段（实际上我们让其只读，正常不会触发）
        if touched.intersection(owner_fields) and not self._as_owner_mgr(request):
            raise PermissionDenied("无权限修改货主审核字段。")

        # 3) 只有“仓库管理员”能改 wh 审核字段（同上）
        if touched.intersection(wh_fields) and not self._as_wh_mgr(request):
            raise PermissionDenied("无权限修改仓库审核字段。")

        if obj.supplier_id and obj.owner_id:
            if obj.supplier.owner_id != obj.owner_id:
                form.add_error("supplier", "所选供应商不属于该货主")
                return

        super().save_model(request, obj, form, change)

    # 只显示 `已关闭` 的订单可以通过 `close_reason` 提供理由
    def has_change_permission(self, request, obj=None):
        if obj and obj.is_closed:
            return False
        return super().has_change_permission(request, obj)

    # 列表页带筛选进入新增页时，预置 owner
    def get_changeform_initial_data(self, request):
        initial = super().get_changeform_initial_data(request)
        owner_id = request.GET.get("owner") or request.GET.get("owner__id__exact")
        if owner_id:
            initial["owner"] = owner_id
        return initial

    # 把 owner_id 挂到 request，给 Inline 使用；并给“供应商”注入 data-source-url 与初始收窄
    def get_form(self, request, obj=None, **kwargs):
        # 推断 owner_id：优先对象 → POST → GET
        owner_id = None
        if obj and getattr(obj, "owner_id", None):
            owner_id = obj.owner_id
        else:
            owner_id = (request.POST.get("owner")
                        or request.GET.get("owner")
                        or request.GET.get("owner__id__exact"))
        # 给 Inline 读取
        request._inbound_owner_id = owner_id

        Form = super().get_form(request, obj, **kwargs)

        class _Form(Form):
            def __init__(self, *args, **kw):
                super().__init__(*args, **kw)
                if "supplier" in self.fields:
                    # 注入供应商接口 URL
                    self.fields["supplier"].widget.attrs["data-source-url"] = reverse(
                        "admin:inbound_inboundorder_supplier_options"
                    )
                    # 初始收窄：未知 owner 则不给选项，避免误选
                    if owner_id:
                        self.fields["supplier"].queryset = Supplier.objects.filter(owner_id=owner_id)
                    else:
                        self.fields["supplier"].queryset = Supplier.objects.none()

        return _Form

    # Admin 内部 JSON 接口：供应商 & 产品（统一在此类里暴露）
    def get_urls(self):
        urls = super().get_urls()
        my = [
            path(
                "supplier-options/",
                self.admin_site.admin_view(self.supplier_options_view),
                name="inbound_inboundorder_supplier_options",
            ),
            path(
                "product-options/",
                self.admin_site.admin_view(self.product_options_view),
                name="inbound_inboundorder_product_options",
            ),
        ]
        return my + urls

    def supplier_options_view(self, request):
        """
        GET ?owner=<id>&q=<keyword>
        返回: {"results":[{"id": pk, "text": "显示名"}, ...]}
        """
        owner_id = request.GET.get("owner")
        q = (request.GET.get("q") or "").strip()

        qs = Supplier.objects.none()
        if owner_id:
            qs = Supplier.objects.filter(owner_id=owner_id)
        if q:
            qs = qs.filter(name__icontains=q)
        qs = qs.order_by("name")[:200]

        return JsonResponse({"results": [{"id": s.pk, "text": str(s)} for s in qs]})

    def product_options_view(self, request):
        """
        GET ?owner=<id>&q=<keyword>
        返回: {"results":[{"id": pk, "text": "显示名"}, ...]}
        """
        owner_id = request.GET.get("owner")
        q = (request.GET.get("q") or "").strip()

        qs = Product.objects.none()
        if owner_id:
            qs = Product.objects.filter(owner_id=owner_id)
        if q:
            qs = qs.filter(
                dj_models.Q(name__icontains=q) |
                dj_models.Q(sku__icontains=q) |
                dj_models.Q(gtin__icontains=q)
            )
        qs = qs.order_by("name")[:200]

        return JsonResponse({"results": [{"id": p.pk, "text": str(p)} for p in qs]})



    # 服务器端兜底：行商品必须隶属该货主
    def save_formset(self, request, form, formset, change):
        if formset.model is InboundOrderLine:
            ok = True
            owner_id = form.instance.owner_id
            for f in formset.forms:
                if not getattr(f, "cleaned_data", None) or f.cleaned_data.get("DELETE"):
                    continue
                line = f.instance
                if line.product_id and owner_id:
                    try:
                        if line.product.owner_id != owner_id:
                            f.add_error("product", "所选商品不属于该货主")
                            ok = False
                    except Product.DoesNotExist:
                        f.add_error("product", "无效的商品")
                        ok = False
            if not ok:
                raise ValidationError("有订单行的商品不属于所选货主，请修正后再保存")
        return super().save_formset(request, form, formset, change)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        """
        统一在这里收窄 supplier 与注入 data-source-url。
        即使 get_form 的包装类未执行，也能保证前端联动拿到接口。
        """
        # —— 供应商下拉：按 owner 收窄 + 注入数据源 URL ——
        if db_field.name == "supplier":
            owner_id = (
                getattr(request, "_inbound_owner_id", None)
                or request.POST.get("owner")
                or request.GET.get("owner")
                or request.GET.get("owner__id__exact")
            )
            if owner_id:
                kwargs["queryset"] = Supplier.objects.filter(owner_id=owner_id)
            else:
                kwargs["queryset"] = Supplier.objects.none()

            # 注入给前端 JS 的数据源 URL
            widget = kwargs.get("widget") or db_field.formfield().widget
            try:
                widget.attrs["data-source-url"] = reverse("admin:inbound_inboundorder_supplier_options")
            except Exception:
                pass
            kwargs["widget"] = widget

        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    # class Media:
    #     css = {
    #         'all': ('css/admin_custom.css',)  # 你的自定义 CSS 文件路径
    #     }


class MyTaskFilter(SimpleListFilter):
    title = "任务归属"
    parameter_name = "mine"

    def lookups(self, request, model_admin):
        return (("1", "我的任务"), ("0", "全部"),)

    def queryset(self, request, queryset):
        # 默认对“收货员”只显示自己的任务；管理员/有特权者可看全部
        if request.user.is_superuser or request.user.has_perm("inbound.view_all_receiving"):
            if self.value() == "1":
                return queryset.filter(assigned_to=request.user)
            return queryset  # 全部
        # 普通收货员：强制只看到自己的
        return queryset.filter(assigned_to=request.user)

class InboundReceiptLineInline(admin.TabularInline):
    model = InboundReceiptLine
    extra = 0

@admin.register(InboundReceipt)
class InboundReceiptAdmin(AdvancedAdminBase,BaseReadonlyAdmin):
    admin_priority =3
    list_display = ("receipt_no", "biz_date", "owner", "warehouse", "supplier", "submit_status", "approved_by", "approved_at")
    list_filter = ("submit_status", "owner", "warehouse", "supplier")
    search_fields = ("receipt_no", "order_no", "src_bill_no")
    inlines = [InboundReceiptLineInline]
    readonly_fields = ("approved_at",)

@admin.register(Lot)
class LotAdmin(AdvancedAdminBase,BaseReadonlyAdmin):
    admin_priority =10
    list_display = ("owner", "product_code", "lot_no", "supplier", "mfg_date", "exp_date")
    list_filter = ("owner",)
    search_fields = ("product_code", "lot_no")

# ========= InboundOrderReturnInfo =========

@admin.register(InboundOrderReturnInfo)
class InboundOrderReturnInfoAdmin(AdvancedAdminBase,BaseReadonlyAdmin):
    admin_priority =5
    list_display = (
        "order", "rma_no", "source_channel",
        "orig_outbound_order_no", "reason_code",
        "photos_required", "refund_amount", "refund_status",
        "created_at", "updated_at",
    )
    list_select_related = ("order",)
    list_filter = ("source_channel", "refund_status", "photos_required")
    search_fields = (
        "rma_no", "orig_outbound_order_no", "reason_code",
        "order__order_no", "order__external_ref",
    )
    autocomplete_fields = ("order",)
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        ("关联", {"fields": ("order",)}),
        ("退货信息", {
            "fields": (
                ("rma_no", "source_channel"),
                ("orig_outbound_order_no", "reason_code"),
                ("photos_required",),
            )
        }),
        ("退款", {"fields": (("refund_amount", "refund_status"),)}),
        ("系统", {"fields": (("created_at", "updated_at"),)}),
    )


# ========= ReturnInspection =========

@admin.action(description="批量审核（OPEN → APPROVED）")
def action_approve(modeladmin, request, queryset):
    qs = queryset.filter(status="OPEN")
    now = timezone.now()
    updated = qs.update(status="APPROVED", approved_by=request.user, approved_at=now)
    modeladmin.message_user(request, f"已审核 {updated} 条（其余非 OPEN 已跳过）。")

@admin.action(description="批量下发执行（APPROVED → POSTED）")
def action_post(modeladmin, request, queryset):
    qs = queryset.filter(status="APPROVED")
    updated = qs.update(status="POSTED")
    modeladmin.message_user(request, f"已下发 {updated} 条（其余非 APPROVED 已跳过）。")

class ReturnInspectionInline(admin.StackedInline):  # 也可以使用 admin.TabularInline
    model = ReturnInspection
    list_display = (
        "order_line", "owner",
        "disposition", "condition", "qty",
        "lot_no", "serial_no", "exp_date",
        "status", "inspected_by", "inspected_at",
        "approved_by", "approved_at",
    )
    list_select_related = ("order_line", "owner", "inspected_by", "approved_by")
    list_filter = (
        "status", "disposition", "condition",
         "owner",
        ("exp_date", admin.DateFieldListFilter),
    )
    search_fields = (
        "serial_no", "lot_no",
        "order_line__order__order_no",
        "order_line__product__code", "order_line__product__name", "order_line__product__sku",
    )
    ordering = ("-created_at", "-id")
    list_per_page = 50
    date_hierarchy = "inspected_at"

    # 体量大时可改用 raw_id_fields（两者选其一）
    autocomplete_fields = ("order_line", "inspected_by", "approved_by", "owner", )
    # raw_id_fields = ("order_line", "inspected_by", "approved_by", "owner", )

    # owner/warehouse 在模型上 editable=False，这里放到只读里可显示
    readonly_fields = ("owner", "created_at", "updated_at")

    fieldsets = (
        ("关联", {"fields": ("order_line", ("owner", ))}),
        ("实物标识", {"fields": (("lot_no", "serial_no"), "exp_date")}),
        ("检验与处置", {"fields": (("condition", "disposition"), "qty", "note")}),
        ("流程", {"fields": (
            ("status",),
            ("inspected_by", "inspected_at"),
            ("approved_by", "approved_at"),
        )}),
        ("系统", {"fields": (("created_at", "updated_at"),)}),
    )

    actions = [action_approve, action_post]

    # 根据状态动态只读：审核后不允许再改关键字段
    def get_readonly_fields(self, request, obj=None):
        base_ro = list(super().get_readonly_fields(request, obj)) + ["owner", "created_at", "updated_at"]
        if not obj:
            return base_ro
        if obj.status in {"APPROVED", "POSTED"}:
            # 审核后锁关键业务字段，仅允许改备注/状态（如需）
            return base_ro + [
                "order_line", "lot_no", "serial_no", "exp_date",
                "condition", "disposition", "qty", "inspected_by", "inspected_at",
                "approved_by", "approved_at",
            ]
        return base_ro

    # 自动补齐检验/审核人和时间
    def save_model(self, request, obj, form, change):
        now = timezone.now()
        # 首次保存：若未填检验人时间，自动补
        if not change and not obj.inspected_by:
            obj.inspected_by = request.user
            obj.inspected_at = now
        # 表单把状态改为 APPROVED 时，如未填审核人时间，则补齐
        if "status" in form.changed_data and obj.status == "APPROVED":
            if not obj.approved_by:
                obj.approved_by = request.user
            if not obj.approved_at:
                obj.approved_at = now
        super().save_model(request, obj, form, change)

    def has_delete_permission(self, request, obj=None):
        # 已下发的不允许删除；其余按需
        if obj and obj.status == "POSTED":
            return False
        return super().has_delete_permission(request, obj)




