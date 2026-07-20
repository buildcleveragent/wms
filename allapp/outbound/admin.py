from __future__ import annotations
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.db import transaction

from django.shortcuts import redirect
from django.utils import timezone
from django.urls import reverse
from allapp.accounts.access import AccessScope
from allapp.accounts.audit import record_audit_event
from allapp.outbound import services as ob_services
from allapp.outbound.authz import strict_order_queryset
from .models import OutboundOrder, OutboundOrderLine
from ..tasking.models import WmsTask


# —— 权限判断：货主业务员（或超管）才允许“提交” —— #
def _as_owner_buyers(self, request):
    return request.user.is_superuser or request.user.has_perm(
        "outbound.submit_outbound_as_owner_buyers"
    )

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
        owner_id = AccessScope.for_user(request.user).single_owner_id
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
    - 提供常用批量动作：提交/回退草稿、货主审核（通过/驳回）、仓库审核（通过/驳回）、取消
    - 关闭由完整 DISPATCH 自动驱动，不能通过后台手工关闭或重开
    - 读写边界：审核人/时间、系统编号/时间只读；其余业务字段可编辑
    - 选择器：owner/warehouse/customer/supplier 使用 autocomplete（需在对应 Admin 中设置 search_fields）
    """

    # —— 列表配置 —— #
    list_display = (
        "order_no", "biz_date", "outbound_type", "owner", "warehouse", "customer", "supplier",
        "submit_status", "approval_status", "processing_mode", "delivery_method", "is_closed",
        "created_by", "created_at",
    )
    list_select_related = ("owner", "warehouse", "customer", "supplier", "created_by")
    list_filter = (
        "outbound_type", "submit_status", "approval_status", "processing_mode",
        "delivery_method", "is_closed",
        ("biz_date", admin.DateFieldListFilter), "owner", "warehouse", "customer", "supplier",
    )
    search_fields = (
        "order_no", "src_bill_no", "ship_to", "contact", "contact_phone",
        "customer__name", "supplier__name",
    )
    ordering = ("-biz_date", "-id")
    date_hierarchy = "biz_date"
    show_full_result_count = False

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return strict_order_queryset(qs, request.user)

    @staticmethod
    def _in_owner_scope(request, order):
        scope = AccessScope.for_user(request.user)
        return bool(
            scope.is_global
            or (
                scope.owner_ids
                and scope.allows(
                    owner_id=order.owner_id,
                    warehouse_id=order.warehouse_id,
                )
            )
        )

    @staticmethod
    def _in_warehouse_scope(request, order):
        scope = AccessScope.for_user(request.user)
        return bool(
            scope.is_global
            or (
                scope.warehouse_ids
                and scope.allows(
                    owner_id=order.owner_id,
                    warehouse_id=order.warehouse_id,
                )
            )
        )

    # —— 表单配置 —— #
    autocomplete_fields = ("owner", "warehouse", "customer", "supplier",)
    readonly_fields = (
        "order_no", "created_at", "created_by",
        # These are workflow facts, not editable metadata.  They are changed
        # only by the controlled actions/services below, so a crafted Admin
        # form submission cannot turn an unshipped order into a closed one.
        "submit_status", "approval_status", "is_closed", "close_reason",
        "approved_by_ownermanager", "approved_at_ownermanager",
        "approved_by_warehouse", "approved_at_warehouse",
        "processing_mode", "assisted_by", "assisted_at",
        "assistance_reason", "assistance_request_id",
    )

    # 使用简单的 fields（用户此前偏好），避免 fieldsets 造成冗长切分
    fields = (
        ("order_no", "biz_date", "outbound_type"),
        ("owner", "warehouse"),
        ("customer", "supplier"),
        ("delivery_method", "etd"),
        ("submit_status", "approval_status"),
        ("processing_mode", "assistance_reason"),
        ("assisted_by", "assisted_at"),
        "assistance_request_id",
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
        return request.user.is_superuser or request.user.has_perm(
            "outbound.approve_outbound_as_owner_manager"
        )

    @staticmethod
    def _require_explicit_final_scope(request, order: OutboundOrder) -> None:
        """Prove the *post-edit* tenant identity before an Admin save.

        A queryset protects only the row as it was fetched.  Without this
        check, an Admin user could load an allowed order and submit a different
        owner or warehouse in the change form.  Writes intentionally reject the
        optional legacy fallback even if it is temporarily enabled for a read
        migration: production order edits require an explicit role scope.
        """

        scope = AccessScope.for_user(request.user)
        if not scope.is_valid or (
            not scope.is_global and scope.source != "user_role_scope"
        ):
            raise PermissionDenied("必须配置有效的显式角色范围后才能保存出库订单。")
        if not scope.allows(
            owner_id=order.owner_id,
            warehouse_id=order.warehouse_id,
        ):
            raise PermissionDenied("无权保存到该货主或仓库范围。")

    # —— 保存钩子 —— #
    def save_model(self, request, obj: OutboundOrder, form, change):  # type: ignore[override]
        self._require_explicit_final_scope(request, obj)
        if not change and obj.is_closed:
            raise PermissionDenied(
                "不能在后台直接创建已关闭订单；订单必须由 DISPATCH 完成后自动关闭。"
            )
        if change:
            persisted = type(obj).objects.only(
                "submit_status", "approval_status", "is_closed", "close_reason"
            ).get(pk=obj.pk)
            changed_workflow_fields = []
            for field_name in ("submit_status", "approval_status", "is_closed"):
                if getattr(obj, field_name) != getattr(persisted, field_name):
                    changed_workflow_fields.append(field_name)
            if (obj.close_reason or "") != (persisted.close_reason or ""):
                changed_workflow_fields.append("close_reason")
            if changed_workflow_fields:
                raise PermissionDenied(
                    "订单工作流状态只能通过受控业务动作变更："
                    + "、".join(changed_workflow_fields)
                )
        if not change and not obj.created_by_id:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    # —— 批量动作 —— #
    actions = (
        "action_submit", "action_revert_draft",
        "action_owner_approve", "action_owner_reject",
        "action_whs_approve", "action_whs_reject",
        "action_cancel",

    )
    # actions = ["action_atp_preview"]

    @admin.action(description="提交")
    def action_submit(self, request, queryset):
        if not _as_owner_buyers(self, request):
            raise PermissionDenied("需要货主业务员权限。")
        self._transition_many(
            request,
            queryset,
            allow=lambda o: o.submit_status == "DRAFT",
            mutate=lambda o: setattr(o, "submit_status", "SUBMITTED"),
            success_msg="已提交",
        )

    @admin.action(description="撤销提交")
    def action_revert_draft(self, request, queryset):
        if not _as_owner_buyers(self, request):
            raise PermissionDenied("需要货主业务员权限。")
        ok, errors = 0, []
        for order in queryset:
            try:
                if not self._in_owner_scope(request, order):
                    raise PermissionDenied("禁止撤回其他货主的订单。")
                ob_services.withdraw_order(order, by_user=request.user)
                ok += 1
            except Exception as exc:  # noqa: BLE001 - display per-order failure
                errors.append(f"{order.order_no}: {exc}")
        if ok:
            self.message_user(request, f"已安全撤回并释放分配：{ok} 张", messages.SUCCESS)
        if errors:
            self.message_user(request, "；".join(errors)[:2000], messages.ERROR)

    @admin.action(description="货主管理员确认")
    def action_owner_approve(self, request, queryset):
        if not self._as_owner_mgr(request):
            raise PermissionDenied("需要货主管理员权限。")

        ok, err = 0, []
        for order in queryset.select_related("owner", "warehouse"):
            try:
                if not self._in_owner_scope(request, order):
                    raise PermissionDenied("禁止审核其他货主或仓库的订单。")
                with transaction.atomic():
                    order = type(order).objects.select_for_update().get(pk=order.pk)
                    # Lock first, then recheck both tenant scope and business
                    # state so a stale Admin list selection cannot approve a
                    # withdrawn, cancelled or closed order.
                    if not self._in_owner_scope(request, order):
                        raise PermissionDenied("禁止审核其他货主或仓库的订单。")
                    ob_services.validate_owner_approval_preconditions(order)
                    before = {"approval_status": order.approval_status}
                    # 统一走模型方法，里面负责：
                    # - approval_status = OWNER_APPROVED
                    # - 记录 approved_by_ownermanager / approved_at_ownermanager
                    # - 调用 ob_services.allocate_inventory 冻结库存并生成 RESERVED 拣货任务
                    order.owner_approve(by_user=request.user, allow_backorder=True)
                    order.refresh_from_db()
                record_audit_event(
                    action="outbound.order.owner_approve",
                    module="outbound",
                    request=request,
                    obj=order,
                    before=before,
                    after={"approval_status": order.approval_status},
                    metadata={"channel": "django_admin"},
                )
                ok += 1
            except Exception as e:
                err.append(f"{getattr(order, 'order_no', order.pk)}: {e}")

        if ok:
            self.message_user(request, f"已货主确认并分配 {ok} 张出库单（生成保留拣货任务）。", level=messages.SUCCESS)
        if err:
            self.message_user(request, "；".join(err)[:2000], level=messages.ERROR)

    @admin.action(description="货主管理员审核驳回")
    def action_owner_reject(self, request, queryset):
        if not self._as_owner_mgr(request):
            raise PermissionDenied("需要货主管理员权限。")

        def mutate(o: OutboundOrder):
            o.approval_status = "OWNER_REJECTED"
            o.approved_by_ownermanager = request.user
            o.approved_at_ownermanager = timezone.now()

        self._transition_many(
            request,
            queryset,
            allow=lambda o: (
                self._in_owner_scope(request, o)
                and o.approval_status == "OWNER_PENDING"
            ),
            mutate=mutate,
            success_msg="已置为:货主管理员审核驳回",
        )

    def _as_wh_mgr(self, request):
        return request.user.is_superuser or request.user.has_perm(
            "outbound.approve_outbound_as_wh_manager"
        )

    @admin.action(description="仓库管理员确认（审核 + 生成拣货任务）")
    def action_whs_approve(self, request, queryset):
        if not self._as_wh_mgr(request):
            raise PermissionDenied("需要仓库管理员权限。")

        ok, err = 0, []
        for order in queryset.select_related("owner", "warehouse"):
            try:
                if not self._in_warehouse_scope(request, order):
                    raise PermissionDenied("禁止确认其他仓库的订单。")
                with transaction.atomic():
                    order = type(order).objects.select_for_update().get(pk=order.pk)
                    if order.approval_status not in {"OWNER_APPROVED", "WHS_PENDING"}:
                        continue

                    before = {"approval_status": order.approval_status}
                    order.approval_status = "WHS_APPROVED"
                    order.approved_by_warehouse = request.user
                    order.approved_at_warehouse = timezone.now()
                    order.save(
                        update_fields=[
                            "approval_status",
                            "approved_by_warehouse",
                            "approved_at_warehouse",
                        ]
                    )
                    ob_services.promote_reserved_pick(
                        order,
                        new_status=WmsTask.Status.RELEASED,
                        by_user=request.user,
                    )
                    record_audit_event(
                        action="outbound.order.warehouse_approve",
                        module="outbound",
                        request=request,
                        obj=order,
                        before=before,
                        after={"approval_status": order.approval_status},
                        metadata={"channel": "django_admin"},
                    )
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
                if not self._in_warehouse_scope(request, order):
                    raise PermissionDenied("禁止驳回其他仓库的订单。")
                with transaction.atomic():
                    order = type(order).objects.select_for_update().get(pk=order.pk)
                    before = {"approval_status": order.approval_status}
                    ob_services.unallocate_for_order(order, by_user=request.user)
                    order.approval_status = "WHS_REJECTED"
                    order.approved_by_warehouse = request.user
                    order.approved_at_warehouse = timezone.now()
                    order.save(
                        update_fields=[
                            "approval_status",
                            "approved_by_warehouse",
                            "approved_at_warehouse",
                        ]
                    )
                    record_audit_event(
                        action="outbound.order.warehouse_reject",
                        module="outbound",
                        request=request,
                        obj=order,
                        before=before,
                        after={"approval_status": order.approval_status},
                        metadata={"channel": "django_admin"},
                    )

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
        if not self._as_owner_mgr(request):
            raise PermissionDenied("需要货主管理员权限。")
        ok, errors = 0, []
        for order in queryset:
            try:
                if not self._in_owner_scope(request, order):
                    raise PermissionDenied("禁止取消其他货主的订单。")
                before = {"approval_status": order.approval_status}
                order = ob_services.cancel_order(order, by_user=request.user)
                record_audit_event(
                    action="outbound.order.cancel",
                    module="outbound",
                    request=request,
                    obj=order,
                    before=before,
                    after={"approval_status": order.approval_status},
                    metadata={"channel": "django_admin"},
                )
                ok += 1
            except Exception as exc:  # noqa: BLE001 - display per-order failure
                errors.append(f"{order.order_no}: {exc}")
        if ok:
            self.message_user(request, f"已取消并释放分配：{ok} 张", messages.SUCCESS)
        if errors:
            self.message_user(request, "；".join(errors)[:2000], messages.ERROR)

    @admin.action(description="关闭订单")
    def action_close(self, request, queryset):
        raise PermissionDenied(
            "订单关闭只能在 DISPATCH 完成且发运数量覆盖订单需求后由系统自动执行。"
        )

    @admin.action(description="重开订单")
    def action_reopen(self, request, queryset):
        raise PermissionDenied(
            "已发运关闭的订单不能通过后台重开；请走受控的逆向业务流程。"
        )

    @admin.action(description="仓库管理员一键确认（货主+仓库审核并发布拣货任务）")
    def action_wh_full_approve_and_release(self, request, queryset):
        """Retired insecure legacy shortcut.

        Keep the method only so a stale bookmarked/admin POST fails closed;
        it is intentionally absent from ``actions``.  Standard orders require
        the distinct owner and warehouse approvals.
        """

        raise PermissionDenied(
            "已停用仓库一键货主审批/发布；请先由货主管理员审核，再执行仓库确认。"
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
