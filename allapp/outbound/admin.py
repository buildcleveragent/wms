from __future__ import annotations
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.db import transaction

from django.shortcuts import redirect
from django.utils import timezone
from django.urls import reverse
from allapp.outbound import services as ob_services
from .models import OutboundOrder, OutboundOrderLine,FuncWaveGenerate, FuncLabelBatch, FuncShippingBoard
from ..tasking.models import WmsTask


# —— 权限判断：货主业务员（或超管）才允许“提交” —— #
def _as_owner_buyers(self, request):
    return request.user.is_superuser or request.user.has_perm("inbound.submit_as_owner_buyers")

# ========= 多租户隔离（非超管仅看自己 owner）的通用混入 =========
class OwnerScopedAdminMixin(admin.ModelAdmin):
    """
    通过 owner_path 指定过滤路径：
      - 头表: "owner"
      - 行表: "order__owner" / "shipment__owner" / "outbound_return__owner" 等
    """
    owner_path = "owner"

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        owner_id = getattr(request.user, "owner_id", None)
        if not owner_id:
            return qs.none()
        return qs.filter(**{f"{self.owner_path}_id": owner_id})

# ========= Inlines =========
class OutboundOrderLineInline(admin.TabularInline):
    model = OutboundOrderLine
    extra = 0
    autocomplete_fields = ["product"]
    fields = ("product", "base_qty", "base_uom", "base_price")

# ========= 出库订单 =========
@admin.register(OutboundOrder)
class OutboundOrderAdmin(admin.ModelAdmin):
    inlines = [OutboundOrderLineInline]
    """出库订单 Admin（遵循最佳实践）

    - 使用 @admin.register
    - 自动记录 `created_by`
    - 提供常用批量动作：提交/回退草稿、货主审核（通过/驳回）、仓库审核（通过/驳回）、取消、关闭/重开
    - 读写边界：审核人/时间、系统编号/时间只读；其余业务字段可编辑
    - 选择器：owner/warehouse/customer/supplier 使用 autocomplete（需在对应 Admin 中设置 search_fields）
    """

    # —— 列表配置 —— #
    list_display = (
        "order_no", "biz_date", "outbound_type", "owner", "customer", "supplier",
        "submit_status", "approval_status", "delivery_method", "is_closed",
        "created_by", "created_at",
    )
    list_select_related = ("owner", "warehouse", "customer", "supplier", "created_by")
    list_filter = (
        "outbound_type", "submit_status", "approval_status", "delivery_method", "is_closed",
        ("biz_date", admin.DateFieldListFilter), "owner", "customer", "supplier",
    )
    search_fields = (
        "order_no", "src_bill_no", "ship_to", "contact", "contact_phone",
        "customer__name", "supplier__name",
    )
    ordering = ("-biz_date", "-id")
    date_hierarchy = "biz_date"
    show_full_result_count = False

    # —— 表单配置 —— #
    autocomplete_fields = ("owner",  "customer", "supplier",)
    readonly_fields = (
        "order_no", "created_at", "created_by",
        "approved_by_ownermanager", "approved_at_ownermanager",
        "approved_by_warehouse", "approved_at_warehouse",
    )

    # 使用简单的 fields（用户此前偏好），避免 fieldsets 造成冗长切分
    fields = (
        ("order_no", "biz_date", "outbound_type"),
        ("owner", ),
        ("customer", "supplier"),
        ("delivery_method", "etd"),
        ("submit_status", "approval_status"),
        ("is_closed", "close_reason"),
        "src_bill_no",
        ("ship_to",),
        ("contact", "contact_phone"),
        "memo",
        ("approved_by_ownermanager", "approved_at_ownermanager"),
        ("approved_by_warehouse", "approved_at_warehouse"),
        ("created_by", "created_at"),
    )

    def _as_owner_mgr(self, request):
        return request.user.is_superuser or request.user.has_perm("inbound.approve_as_owner_manager")

    # —— 保存钩子 —— #
    def save_model(self, request, obj: OutboundOrder, form, change):  # type: ignore[override]
        if not change and not obj.created_by_id:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    # —— 批量动作 —— #
    actions = (
        "action_wh_full_approve_and_release",  # ⭐ 新增一键确认
        "action_submit", "action_revert_draft",
        "action_owner_approve", "action_owner_reject",
        "action_whs_approve", "action_whs_reject",
        "action_cancel", "action_close", "action_reopen",

    )
    # actions = ["action_atp_preview"]

    @admin.action(description="提交")
    def action_submit(self, request, queryset):
        self._transition_many(
            request,
            queryset,
            allow=lambda o: o.submit_status == "DRAFT",
            mutate=lambda o: setattr(o, "submit_status", "SUBMITTED"),
            success_msg="已提交",
        )

    @admin.action(description="撤销提交")
    def action_revert_draft(self, request, queryset):
        self._transition_many(
            request,
            queryset,
            allow=lambda o: (o.submit_status == "SUBMITTED" and not o.is_closed and o.approval_status != "CANCELLED"),
            mutate=lambda o: setattr(o, "submit_status", "DRAFT"),
            success_msg="已回退为草稿",
        )

    @admin.action(description="货主管理员确认")
    def action_owner_approve(self, request, queryset):
        if not self._as_owner_mgr(request):
            raise PermissionDenied("需要货主管理员权限。")

        ok, err = 0, []
        for order in queryset.select_related("owner", "warehouse"):
            try:
                # 统一走模型方法，里面负责：
                # - approval_status = OWNER_APPROVED
                # - 记录 approved_by_ownermanager / approved_at_ownermanager
                # - 调用 ob_services.allocate_inventory 冻结库存并生成 RESERVED 拣货任务
                order.owner_approve(by_user=request.user, allow_backorder=True)
                ok += 1
            except Exception as e:
                err.append(f"{getattr(order, 'order_no', order.pk)}: {e}")

        if ok:
            self.message_user(request, f"已货主确认并分配 {ok} 张出库单（生成保留拣货任务）。", level=messages.SUCCESS)
        if err:
            self.message_user(request, "；".join(err)[:2000], level=messages.ERROR)

    @admin.action(description="货主管理员审核驳回")
    def action_owner_reject(self, request, queryset):
        def mutate(o: OutboundOrder):
            o.approval_status = "OWNER_REJECTED"
            o.approved_by_ownermanager = request.user
            o.approved_at_ownermanager = timezone.now()

        self._transition_many(
            request,
            queryset,
            allow=lambda o: o.approval_status == "OWNER_PENDING",
            mutate=mutate,
            success_msg="已置为:货主管理员审核驳回",
        )

    def _as_wh_mgr(self, request):
        return request.user.is_superuser or request.user.has_perm("inbound.approve_as_wh_manager")

    @admin.action(description="仓库管理员确认（审核 + 生成拣货任务）")
    def action_whs_approve(self, request, queryset):
        if not self._as_wh_mgr(request):
            raise PermissionDenied("需要仓库管理员权限。")

        ok, err = 0, []
        for order in queryset.select_related("owner", "warehouse"):
            try:
                if order.approval_status not in {"OWNER_APPROVED", "WHS_PENDING"}:
                    continue

                # 1) 审核状态更新为 WHS_APPROVED
                order.approval_status = "WHS_APPROVED"
                order.approved_by_warehouse = request.user
                order.approved_at_warehouse = timezone.now()
                order.save(update_fields=["approval_status", "approved_by_warehouse", "approved_at_warehouse"])

                # 2) 将 RESERVED 拣货任务提升为 DRAFT（生成拣货任务草稿）
                ob_services.promote_reserved_pick(order, new_status="RELEASED")
                ok += 1
            except Exception as e:
                err.append(f"{getattr(order, 'order_no', order.pk)}: {e}")

        if ok:
            self.message_user(request, f"仓库确认完成：{ok} 张出库单已生成拣货任务草稿。", level=messages.SUCCESS)
        if err:
            self.message_user(request, "；".join(err)[:2000], level=messages.ERROR)

    @admin.action(description="仓库管理员驳回（释放冻结 & 取消拣货任务）")
    def action_whs_reject(self, request, queryset):
        if not self._as_wh_mgr(request):
            raise PermissionDenied("需要仓库管理员权限。")

        ok, err = 0, []
        for order in queryset:
            try:
                # 1) 释放冻结 & 取消相关任务
                ob_services.unallocate_for_order(order)

                # 2) 审核状态更新为 WHS_REJECTED
                order.approval_status = "WHS_REJECTED"
                order.approved_by_warehouse = request.user
                order.approved_at_warehouse = timezone.now()
                order.save(update_fields=["approval_status", "approved_by_warehouse", "approved_at_warehouse"])

                ok += 1
            except Exception as e:
                err.append(f"{getattr(order, 'order_no', order.pk)}: {e}")

        if ok:
            self.message_user(request, f"仓库已拒绝：{ok} 张出库单已释放冻结并取消任务。", level=messages.SUCCESS)
        if err:
            self.message_user(request, "；".join(err)[:2000], level=messages.ERROR)

    # -------- 取消 / 关闭 / 重开 -------- #

    @admin.action(description="取消订单")
    def action_cancel(self, request, queryset):
        self._transition_many(
            request,
            queryset,
            allow=lambda o: not o.is_closed,
            mutate=lambda o: setattr(o, "approval_status", "CANCELLED"),
            success_msg="已取消",
        )

    @admin.action(description="关闭订单")
    def action_close(self, request, queryset):
        def mutate(o: OutboundOrder):
            o.is_closed = True
            if not o.close_reason:
                o.close_reason = "Closed by admin"

        self._transition_many(
            request,
            queryset,
            allow=lambda o: o.approval_status != "CANCELLED",
            mutate=mutate,
            success_msg="已关闭",
        )

    @admin.action(description="重开订单")
    def action_reopen(self, request, queryset):
        def mutate(o: OutboundOrder):
            o.is_closed = False
            o.close_reason = ""

        self._transition_many(
            request,
            queryset,
            allow=lambda o: o.is_closed,
            mutate=mutate,
            success_msg="已重开",
        )

    @admin.action(description="仓库管理员一键确认（货主+仓库审核并发布拣货任务）")
    def action_wh_full_approve_and_release(self, request, queryset):
        """
        仓库管理员一键完成：
          1) 若尚未货主审核，则代为 owner_approve（审核+冻结库存+生成 RESERVED 拣货任务）
          2) 仓库审核通过（WHS_APPROVED）
          3) 将 RESERVED 的拣货任务直接提升为 RELEASED，PDA 立刻可见
        """
        # 只允许仓库管理员执行
        if not self._as_wh_mgr(request):
            raise PermissionDenied("需要仓库管理员权限。")

        ok, err = 0, []
        # 一把锁住，避免并发下重复 allocate / promote
        with transaction.atomic():
            for order in queryset.select_for_update().select_related("owner", "warehouse"):
                try:
                    # 1) 若仍在 OWNER_PENDING/OWNER_REJECTED，则代为货主审核（会冻结库存 + 生成 RESERVED 拣货任务）
                    if order.approval_status in ("OWNER_PENDING"):
                        # 建议你按前面修正过的 owner_approve 实现：
                        #   - approval_status = OWNER_APPROVED
                        #   - 记录 approved_by_ownermanager / approved_at_ownermanager
                        #   - ob_services.allocate_inventory(...)
                        order.owner_approve(by_user=request.user, allow_backorder=True)

                    # 2) 仓库审核通过（如果还没 WHS_APPROVED）
                    if order.approval_status in ("OWNER_APPROVED", "WHS_PENDING"):
                        order.approval_status = "WHS_APPROVED"
                        order.approved_by_warehouse = request.user
                        order.approved_at_warehouse = timezone.now()
                        order.save(update_fields=[
                            "approval_status",
                            "approved_by_warehouse",
                            "approved_at_warehouse",
                        ])

                    # 3) 将 RESERVED 拣货任务直接提升为 RELEASED
                    #    注意：PickTaskViewSet.get_queryset 只看 status in [RELEASED, IN_PROGRESS]
                    ob_services.promote_reserved_pick(
                        order, new_status=WmsTask.Status.RELEASED
                    )

                    ok += 1
                except Exception as e:
                    err.append(f"{getattr(order, 'order_no', order.pk)}: {e}")

        if ok:
            self.message_user(
                request,
                f"一键确认完成：{ok} 张出库单,已通过货主+仓库审核,并发布拣货任务。",
                level=messages.SUCCESS,
            )
        if err:
            self.message_user(
                request,
                "；".join(err)[:2000],
                level=messages.ERROR,
            )

    # —— 工具：统一状态流转（逐条 save 以触发 clean/信号/审计） —— #
    def _transition_many(self, request, queryset, allow, mutate, success_msg: str):
        ok = 0
        with transaction.atomic():
            for obj in queryset.select_for_update():
                try:
                    if not allow(obj):
                        continue
                    mutate(obj)
                    obj.full_clean()
                    obj.save()
                    ok += 1
                except Exception as e:  # noqa: BLE001 - 呈现到 admin 消息
                    messages.warning(request, f"{obj}: 变更失败 - {e}")
        if ok:
            self.message_user(request, f"{success_msg}：{ok} 条", level=messages.SUCCESS)
        else:
            self.message_user(request, "无记录被变更", level=messages.WARNING)




class _FuncBaseAdmin(admin.ModelAdmin):
    """把 Proxy 当成“菜单锚点”，staff 可见，列表直接跳功能页"""
    # 让“出库管理”这个 app 出现在顶栏
    def has_module_permission(self, request):
        return True

    # 让这些“模型”出现在 app 下拉（至少一个 True 即可）
    def get_model_perms(self, request):
        is_staff = True
        return {"view": is_staff, "change": is_staff}

    # 允许访问列表页（随后重定向到功能页）
    def has_view_permission(self, request, obj=None):
        return True

    # 禁用通过该入口增删改（它只是锚点）
    def has_add_permission(self, request): return True
    def has_change_permission(self, request, obj=None): return True
    def has_delete_permission(self, request, obj=None): return True

    # 列表页 -> 直接跳到你的功能页（URL name 见下）
    target_url_name = None
    def changelist_view(self, request, extra_context=None):
        return redirect(reverse(self.target_url_name))
