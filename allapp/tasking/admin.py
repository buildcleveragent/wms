# allapp/tasking/admin.py
from __future__ import annotations
import logging
from datetime import date

logger = logging.getLogger(__name__)

from django.apps import apps
from django.db.models import OneToOneRel
from .services import generate_count_lines
from django.shortcuts import redirect, render, get_object_or_404
from django.db import models
from allapp.core.admin_widgets import TrimDecimalWidget
from allapp.core.choices import ZoneType
from allapp.core.admin_mixins import DecimalPrettyInitialMixin
from allapp.outbound import services as ob_services
from collections import OrderedDict
from dataclasses import dataclass
from typing import Optional, List, Dict, Type
from typing_extensions import OrderedDict

from django.contrib.admin.widgets import AdminDateWidget
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError, PermissionDenied, ObjectDoesNotExist
from django.db.models import Count, Q, Sum, Exists, OuterRef
from django.forms import modelform_factory
from django.forms.models import BaseInlineFormSet
from django.shortcuts import redirect, get_object_or_404
from django.utils.html import format_html
from allapp.tasking.plugins.handlers import get_posting_handler
from . import services  # 放着 save_receiving_snapshot
from allapp.tasking import services as svc  # 内含 publish_using_inline / publish_task
from allapp.inventory.services import post_task
from .models import (ReviewTaskExtra,ReviewLineExtra, AdjustLineExtra,  AdjustTaskExtra, CountLineExtra, CountTaskExtra,  DispatchLineExtra, DispatchTaskExtra,
    LoadLineExtra, LoadTaskExtra,PackLineExtra, PackTaskExtra, PickLineExtra, PickTaskExtra, PutawayLineExtra,
    PutawayTaskExtra,QCLineExtra,QCTaskExtra,ReceiveLineExtra,ReceiveTaskExtra,RelocLineExtra,RelocTaskExtra,
    ReplenishLineExtra, ReplenishTaskExtra, TaskAssignment, TaskScanLog, TaskStatusLog, WmsTask, WmsTaskLine,)

from django.db import transaction
from urllib.parse import urlencode
from .models import WmsTask, WmsTaskLine

from django.contrib import admin, messages

from django.urls import path, reverse
from django.utils.translation import gettext_lazy as _
from allapp.locations.models import Warehouse, Location
from allapp.products.models import Product
from ..core.models import DocSequence
from django import forms
from django.utils import timezone

from allapp.baseinfo.models import Owner
from allapp.tasking.models import WmsTask  # 确保导入你的模型

def get_line_extra_generic(tl):
    """
    给定一条 WmsTaskLine，返回它对应的 *LineExtra 实例（已保存后的最新值）。
    先按 task_type → 模型名映射；不行再回退用一对一反向关系自动探测。
    """
    if not tl or not getattr(tl, "id", None):
        return None

    task = getattr(tl, "task", None)
    ttype = getattr(task, "task_type", None)

    # ① 按 task_type → 模型名（只写你项目里真实存在的名字）
    MAP = {
        # 收货 / 上架 / 拣货 / 复核 / 打包 / 装车 / 发运 / 补货 / 盘点 / 调整
        getattr(task.__class__.TaskType, "RECEIPT", None):     "ReceiveLineExtra",
        getattr(task.__class__.TaskType, "PUTAWAY", None):     "PutawayLineExtra",
        getattr(task.__class__.TaskType, "PICK", None):        "PickLineExtra",
        getattr(task.__class__.TaskType, "REVIEW", None):      "ReviewLineExtra",
        getattr(task.__class__.TaskType, "PACK", None):        "PackLineExtra",
        getattr(task.__class__.TaskType, "LOAD", None):        "LoadLineExtra",
        getattr(task.__class__.TaskType, "DISPATCH", None):    "DispatchLineExtra",
        getattr(task.__class__.TaskType, "REPLENISH", None):   "ReplenishLineExtra",
        getattr(task.__class__.TaskType, "COUNT", None):       "CountLineExtra",
        getattr(task.__class__.TaskType, "ADJUST", None):      "AdjustLineExtra",
    }
    model_name = MAP.get(ttype)
    if model_name:
        try:
            Model = apps.get_model("allapp.tasking", model_name)
            extra = (Model.objects
                     .select_related("line", "line__task")
                     .filter(line_id=tl.id)
                     .first())
            if extra:
                return extra
        except LookupError:
            pass  # 模型名映射失败时转入回退探测

    # ② 回退：遍历该行的 OneToOne 反向关系，找 *LineExtra
    for rel in tl._meta.related_objects:
        if not isinstance(rel, OneToOneRel):
            continue
        Model = rel.related_model
        if not Model.__name__.endswith("LineExtra"):
            continue
        accessor = rel.get_accessor_name()  # 如 countlineextra / picklineextra ...
        try:
            return getattr(tl, accessor)
        except Model.DoesNotExist:
            continue

    return None

class CountWizardForm(forms.Form):
    # —— 必填
    warehouse = forms.ModelChoiceField(label="仓库", queryset=Warehouse.objects.all(), required=True)
    owner = forms.ModelChoiceField(label="货主", queryset=Owner.objects.all(), required=True)

    # —— 细化筛选（全可选，按需要组合）
    zone_type = forms.ChoiceField(
        label="库区",
        choices=ZoneType.choices,  # 直接从 IntegerChoices 获取 choices
        required=False
    )
    location = forms.ModelChoiceField(label="指定库位", queryset=Location.objects.all(), required=False)
    location_prefix = forms.CharField(label="库位前缀", required=False, help_text="例如 A-01-；匹配 Location.code/name 前缀")
    product = forms.ModelChoiceField(label="商品（SKU）", queryset=Product.objects.all(), required=False)
    batch_no = forms.CharField(label="批次号", required=False)
    lpn = forms.CharField(label="LPN/容器号", required=False)

    exclude_zero_onhand = forms.BooleanField(label="忽略在库为 0 的明细", required=False, initial=True)
    max_lines = forms.IntegerField(label="最多生成行数", min_value=1, max_value=10000, initial=1000, required=True)
    task_remark = forms.CharField(label="任务备注", required=False)

    def clean(self):
        cd = super().clean()
        # 有 location_prefix 时，尽量提示“与zonetype/location 组合可更精准”，但不强制
        return cd


class CountScope(forms.TextInput):
    pass


class CountWizardScope:
    WAREHOUSE = "WAREHOUSE"                 # 整个仓库
    OWNER = "OWNER"                         # 仓库 + 货主全部
    OWNER_PRODUCT = "OWNER_PRODUCT"         # 仓库 + 货主 + SKU
    OWNER_PRODUCT_BATCH = "OWNER_PRODUCT_BATCH"  # 再加批次
    LOCATION = "LOCATION"                   # 指定库位（可选再加货主）


SCOPE_CHOICES = (
    (CountWizardScope.WAREHOUSE, "整个仓库"),
    (CountWizardScope.OWNER, "某货主的全部"),
    (CountWizardScope.OWNER_PRODUCT, "某货主的某个SKU"),
    (CountWizardScope.OWNER_PRODUCT_BATCH, "某货主的某SKU某批次"),
    (CountWizardScope.LOCATION, "指定库位"),
)

USE_SKIP_LOCKED = True  # 根据需要，避免锁等待

LINE_EXTRA_MODEL_MAP = {
    "RECEIVE": ReceiveLineExtra,
    "PUTAWAY": PutawayLineExtra,
    "PICK": PickLineExtra,
    "REVIEW": ReviewLineExtra,
    "PACK": PackLineExtra,
    "LOAD": LoadLineExtra,
    "DISPATCH": DispatchLineExtra,
    "REPLEN": ReplenishLineExtra,
    "RELOC": RelocLineExtra,
    "COUNT": CountLineExtra,
}

# ====== 1) 映射：TaskType -> 行级 Extra（rel_attr 按你冻结包里的小写反向名） ======
@dataclass
class LineExtraEntry:
    extra_model: Type
    rel_attr: str                      # 反向访问器（如 'receivelineextra'）
    include: Optional[List[str]] = None
    readonly: Optional[List[str]] = None

LINE_EXTRA_REGISTRY: Dict[str, LineExtraEntry] = {
    "RECEIVE":  LineExtraEntry(ReceiveLineExtra,  "receivelineextra"),
    "PUTAWAY":  LineExtraEntry(PutawayLineExtra,  "putawaylineextra"),
    "PICK":     LineExtraEntry(PickLineExtra,     "picklineextra"),
    "REVIEW":   LineExtraEntry(ReviewLineExtra,   "reviewlineextra"),
    "PACK":     LineExtraEntry(PackLineExtra,     "packlineextra"),
    "LOAD":     LineExtraEntry(LoadLineExtra,     "loadlineextra"),
    "DISPATCH": LineExtraEntry(DispatchLineExtra, "dispatchlineextra"),
    "REPLEN":   LineExtraEntry(ReplenishLineExtra,"replenishlineextra"),
    "RELOC":    LineExtraEntry(RelocLineExtra,    "reloclineextra"),
    "COUNT":    LineExtraEntry(CountLineExtra,    "countlineextra"),
}
# ====== 2) 工具：自动探测 Extra 模型上指向 WmsTaskLine 的 FK / O2O 字段名 ======
def _detect_fk_to_taskline(model):
    from allapp.tasking.models import WmsTaskLine
    candidates = []
    for f in model._meta.get_fields():
        is_o2o = f.get_internal_type() == "OneToOneField"
        is_fk  = f.get_internal_type() == "ForeignKey"
        if not (is_o2o or is_fk):
            continue
        try:
            if f.remote_field and f.remote_field.model is WmsTaskLine:
                candidates.append(f.name)
        except Exception:
            pass
    if "line" in candidates:
        return "line"
    if candidates:
        return candidates[0]
    raise AssertionError(f"{model.__name__} 未找到指向 WmsTaskLine 的外键/一对一字段")

# ====== 3) 统一 select_related（覆盖所有可能的 rel_attr） ======
def _select_related_all_extras(qs):
    for e in LINE_EXTRA_REGISTRY.values():
        qs = qs.select_related(e.rel_attr)
    return qs

def make_extra_inline(entry):
    from django.contrib import admin
    BaseInline = admin.StackedInline  # or TabularInline
    extra_model = entry.extra_model

    # —— 计算 fk_name（优先 entry.fk_name，否则自动扫描，否则 'line'）——
    fk = getattr(entry, "fk_name", None)
    if not fk:
        fk = next(
            (f.name for f in extra_model._meta.fields
             if getattr(getattr(f, "remote_field", None), "model", None) is WmsTaskLine),
            "line",
        )

    # —— 计算 include / exclude / readonly —— #
    default_exclude = (
        "line", "id", "pk",
        "created_at", "updated_at", "deleted_at",
        "created_by", "updated_by", "deleted_by",
        "is_deleted",
    )
    fields_cfg   = tuple(entry.include) if entry.include else None
    exclude_cfg  = None if fields_cfg else default_exclude
    readonly_cfg = tuple(entry.readonly) if entry.readonly else ()

    class _ExtraInline(BaseInline):
        extra       = 0
        max_num     = 1
        can_delete  = False

        # —— 没记录时给 1 个空表单 —— #
        def get_extra(self, request, obj=None, **kwargs):
            if obj is None:
                return 0
            rel_attr = getattr(self, "rel_attr_name", None)
            if not rel_attr:
                return 0
            try:
                val = getattr(obj, rel_attr)
                if hasattr(val, "exists"):      # 反向 manager（FK）
                    return 1 if not val.exists() else 0
                return 0 if val else 1          # OneToOne 对象或 None
            except Exception:
                return 1

        # —— 关键：强制把 formset 的 queryset 绑定到 (fk = 当前行ID) —— #
        def get_formset(self, request, obj=None, **kwargs):
            FormSet = super(_ExtraInline, self).get_formset(request, obj, **kwargs)
            parent_pk = getattr(obj, "pk", None)
            if parent_pk is None:
                return FormSet

            fk_name_local = self.fk_name
            model_local   = self.model

            class BoundFormSet(FormSet):
                def __init__(self, *args, **kws):
                    super().__init__(*args, **kws)
                    try:
                        self.queryset = model_local._default_manager.filter(**{fk_name_local: parent_pk})
                    except Exception:
                        # 即使 fk_name 配错，也不要让页面崩；退回默认行为
                        pass

            return BoundFormSet

        # —— 保证可见/可改（避免权限或自定义 admin 误拦）—— #
        def has_view_permission(self, request, obj=None):
            return True

        def has_change_permission(self, request, obj=None):
            return True

        def has_add_permission(self, request, obj):
            # 只在不存在时允许新增
            rel_attr = getattr(self, "rel_attr_name", None)
            if obj and rel_attr:
                try:
                    val = getattr(obj, rel_attr)
                    if hasattr(val, "exists"):
                        return not val.exists()
                    return val is None
                except Exception:
                    return True
            return True

        def formfield_for_dbfield(self, db_field, request, **kwargs):
            formfield = super().formfield_for_dbfield(db_field, request, **kwargs)
            initial_map = getattr(self, "_initial_for_fields", None)
            if initial_map and db_field.name in initial_map:
                formfield.initial = initial_map[db_field.name]
            return formfield

    # —— 类体外设置依赖外部变量的类属性（避免 NameError） —— #
    _ExtraInline.__name__  = f"{extra_model.__name__}Inline"
    _ExtraInline.model     = extra_model
    _ExtraInline.fk_name   = fk
    _ExtraInline.rel_attr_name = entry.rel_attr  # 例如 'reviewlineextra'

    if fields_cfg is not None:
        _ExtraInline.fields = fields_cfg
        if hasattr(_ExtraInline, "exclude"):
            delattr(_ExtraInline, "exclude")
    else:
        _ExtraInline.exclude = exclude_cfg
    if readonly_cfg:
        _ExtraInline.readonly_fields = readonly_cfg

    return _ExtraInline

def is_wh_operator(user):
    return user.is_superuser or user.has_perm("tasking.claim_task_as_wh_operator")

def is_wh_manager(user):
    return user.is_superuser or user.has_perm("tasking.taskconfirm_as_wh_manager")

class ReceiveLineExtraForm(forms.ModelForm):
    # 明确允许的输入格式；根据你现场习惯可再加 2025/09/14、2025.09.14 等
    mfg_date = forms.DateField(
        label="生产日期", required=False,
        input_formats=['%Y-%m-%d', '%Y/%m/%d', '%Y.%m.%d'],
        widget=AdminDateWidget
    )
    exp_date = forms.DateField(
        label="有效期至", required=False,
        input_formats=['%Y-%m-%d', '%Y/%m/%d', '%Y.%m.%d'],
        widget=AdminDateWidget
    )

    class Meta:
        from allapp.tasking.models import ReceiveLineExtra  # 按你项目路径调整
        model = ReceiveLineExtra
        exclude = ("line",)
# ========= 共用 Inline =========
class TaskAssignmentInlineFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        # 校验：同一任务最多一个“头级活动指派”（line 为空 且 finished_at 为空）
        active_head_count = 0
        seen_active_lines = set()

        for form in self.forms:
            if not hasattr(form, "cleaned_data"):
                continue
            if form.cleaned_data.get("DELETE", False):
                continue

            line = form.cleaned_data.get("line")
            finished_at = form.cleaned_data.get("finished_at")

            is_active = not finished_at
            if is_active and line is None:
                active_head_count += 1
                if active_head_count > 1:
                    raise ValidationError("同一任务最多只能有一个‘头级活动指派’。")

            if is_active and line is not None:
                # 表单内重复行级活动指派检查
                if line.pk in seen_active_lines:
                    raise ValidationError(f"任务行 #{line.pk} 已存在活动指派。")
                seen_active_lines.add(line.pk)

                # 数据库层已有活动指派检查（排除当前编辑这条）
                qs = TaskAssignment.objects.filter(line=line, finished_at__isnull=True)
                if form.instance.pk:
                    qs = qs.exclude(pk=form.instance.pk)
                if qs.exists():
                    raise ValidationError(f"任务行 #{line.pk} 已被其他记录活动指派。")

        # 头级活动指派唯一性的数据库级兜底（防止只改时间戳绕过）
        if active_head_count == 1:
            # 找到本任务其他“头级活动指派”
            task_id = self.instance.pk if hasattr(self, "instance") else None

class TaskAssignmentInline(admin.TabularInline):
    model = TaskAssignment
    extra = 0
    # autocomplete_fields = ["assignee"]
    readonly_fields = ["accepted_at", "finished_at"]
    fields = ("assignee", "line","accepted_at", "finished_at")
    show_change_link = True

    formset = TaskAssignmentInlineFormSet

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "line":
            # 仅列出当前任务的行
            obj_id = request.resolver_match.kwargs.get("object_id")
            if obj_id:
                kwargs["queryset"] = WmsTaskLine.objects.filter(task_id=obj_id)
            else:
                kwargs["queryset"] = WmsTaskLine.objects.none()

        if db_field.name == "assignee":
            User = get_user_model()
            app_label = "tasking"
            codename = "claim_task_as_wh_operator"
            kwargs["queryset"] = (
                User.objects.filter(is_active=True, is_staff=True)
                .filter(
                    Q(user_permissions__content_type__app_label=app_label,
                      user_permissions__codename=codename)
                    |
                    Q(groups__permissions__content_type__app_label=app_label,
                      groups__permissions__codename=codename)
                )
                .distinct()
            )

        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related("assignee", "line")
        if is_wh_manager(request.user):
            return qs
        if not is_wh_operator(request.user):
            return qs.none()
        # 只展示“我自己的活动指派”；别人/历史都不显示
        return qs.filter(assignee=request.user, finished_at__isnull=True)

        # 操作员禁止新增/删除

    def has_add_permission(self, request, obj=None):
        return is_wh_manager(request.user)

    def has_delete_permission(self, request, obj=None):
        return is_wh_manager(request.user)

    def has_change_permission(self, request, obj=None):
        # 操作员仅能结束自己那条（例如通过动作/服务），不在 inline 里改人
        return is_wh_manager(request.user)

class TaskStatusLogInline(admin.TabularInline):
    model = TaskStatusLog
    extra = 0
    can_delete = False
    readonly_fields = ("old_status", "new_status", "changed_by", "changed_at", "note")
    fields = ("old_status", "new_status", "changed_by", "changed_at", "note")
    ordering = ("-changed_at",)

class WmsTaskLineInline(admin.TabularInline):
    model = WmsTaskLine
    extra = 0
    autocomplete_fields = ["product", "from_location", "to_location"]
    template = "admin/tasking/wmstaskline_inline_with_extra.html"  # 你现在这版 s5 模板
    fields = (
        "product",
        "from_location",
        "to_location",
        "qty_plan",
        "qty_done",
        "src_model",
        "src_id",
        "rule_key",
    )

    def get_formset(self, request, obj=None, **kwargs):
        """给每个行表单挂 extra_form（用整页 POST 绑定），并在保存时把 line 赋回去。"""
        task_type = getattr(obj, "task_type", None) or request.POST.get("task_type") or request.GET.get("task_type")
        extra_model = LINE_EXTRA_MODEL_MAP.get(task_type)

        BaseFormSet = super().get_formset(request, obj, **kwargs)
        req = request  # 关进闭包，保证绑定用的是整页 POST/FILES

        class LineWithExtraFormSet(BaseFormSet):
            def __init__(self, *args, **kw):
                self.extra_model = extra_model
                if extra_model:
                    # 生成 extra 的 ModelForm（不含 line 字段）
                    self.extra_form_class = modelform_factory(extra_model, exclude=("line",))
                    # 提供表头字段给模板使用（你那版模板会读这个）
                    proto = self.extra_form_class(prefix="__proto__")
                    self.extra_form_fields = list(proto.visible_fields())
                else:
                    self.extra_form_class = None
                    self.extra_form_fields = []
                super().__init__(*args, **kw)

            def add_fields(self, form, index):
                """给每个行表单挂上 form.extra_form；prefix = f"{form.prefix}-extra"。"""
                super().add_fields(form, index)
                if not self.extra_form_class:
                    return
                # 已有关联则取出；没有则建一个“空实例”（便于 has_changed 判定）
                inst = None
                if form.instance and form.instance.pk:
                    inst = self.extra_model.objects.filter(line=form.instance).first()
                if inst is None:
                    inst = self.extra_model()
                form.extra_form = self.extra_form_class(
                    data=(req.POST if req.method == "POST" else None),
                    files=(req.FILES if req.method == "POST" else None),
                    instance=inst,
                    prefix=f"{form.prefix}-extra",  # 和模板里的 name/id 完全对齐
                )

            @property
            def empty_form(self):
                """empty 行也挂一个未绑定的 extra_form，供“添加一行”时克隆。"""
                f = super().empty_form
                if self.extra_form_class:
                    f.extra_form = self.extra_form_class(prefix=f"{f.prefix}-extra")
                return f

            # —— 把 extra 一起保存（新建/更新/删除） ——
            def save_new(self, form, commit=True):
                obj = super().save_new(form, commit)  # obj 已有 pk
                ef = getattr(form, "extra_form", None)
                if ef and ef.is_valid() and (ef.has_changed() or getattr(ef.instance, "pk", None)):
                    e = ef.save(commit=False)
                    e.line = obj
                    e.save()
                return obj

            def save_existing(self, form, instance, commit=True):
                obj = super().save_existing(form, instance, commit)
                ef = getattr(form, "extra_form", None)
                if not ef:
                    return obj
                # 删除父行时，联动删 extra
                if form.cleaned_data.get("DELETE"):
                    if self.extra_model:
                        self.extra_model.objects.filter(line=obj).delete()
                    return obj
                if ef.is_valid() and (ef.has_changed() or getattr(ef.instance, "pk", None)):
                    e = ef.save(commit=False)
                    if not getattr(e, "line_id", None):
                        e.line = obj
                    e.save()
                return obj

            def save_existing_objects(self, commit=True):
                """
                让“只改了 extra、主行未改”的情况也能保存，
                同时确保返回列表以满足 Django 的加法拼接。
                """
                # 先让父类保存“主行有变更”的表单；确保拿到的是 list
                saved = super().save_existing_objects(commit) or []

                if not getattr(self, "extra_form_class", None):
                    return saved

                # 遍历所有已有行（无论主行是否变化）
                for form in self.initial_forms:
                    # 被标记删除：联动删 extra，继续
                    if form.cleaned_data.get("DELETE"):
                        if getattr(self, "extra_model", None):
                            self.extra_model.objects.filter(line=form.instance).delete()
                        continue

                    ef = getattr(form, "extra_form", None)
                    if not ef:
                        continue

                    # 只在 extra 有变化，或本来就存在一条 extra 记录时处理
                    if ef.has_changed() or getattr(ef.instance, "pk", None):
                        if not ef.is_valid():
                            # 如需把错误冒泡，可在此处 form.add_error(None, "…")
                            continue
                        e = ef.save(commit=False)
                        if not getattr(e, "line_id", None):
                            e.line = form.instance
                        e.save()

                # 非常重要：必须返回 list
                return saved

        return LineWithExtraFormSet

class _BaseHeadExtraInline(admin.StackedInline):
    extra = 0
    max_num = 1
    can_delete = True

class ReceiveTaskExtraInline(_BaseHeadExtraInline):
    model = ReceiveTaskExtra

class PutawayTaskExtraInline(_BaseHeadExtraInline):
    model = PutawayTaskExtra

class PickTaskExtraInline(_BaseHeadExtraInline):
    model = PickTaskExtra

class ReviewTaskExtraInline(_BaseHeadExtraInline):
    model = ReviewTaskExtra
    fields = ['from_location', 'qty_reviewed', 'qty_picked', 'discrepancy_reason']
    extra = 1  # 至少显示一个空行

class PackTaskExtraInline(_BaseHeadExtraInline):
    model = PackTaskExtra

class LoadTaskExtraInline(_BaseHeadExtraInline):
    model = LoadTaskExtra

class DispatchTaskExtraInline(_BaseHeadExtraInline):
    model = DispatchTaskExtra

class ReplenishTaskExtraInline(_BaseHeadExtraInline):
    model = ReplenishTaskExtra

class RelocTaskExtraInline(_BaseHeadExtraInline):
    model = RelocTaskExtra

class CountTaskExtraInline(_BaseHeadExtraInline):
    model = CountTaskExtra

class QCTaskExtraInline(_BaseHeadExtraInline):
    model = QCTaskExtra

class AdjustTaskExtraInline(_BaseHeadExtraInline):
    model = AdjustTaskExtra

_TASK_HEAD_INLINE_MAP = {
    "RECEIVE": ReceiveTaskExtraInline,
    "PUTAWAY": PutawayTaskExtraInline,
    "PICK": PickTaskExtraInline,
    "PACK": PackTaskExtraInline,
    "LOAD": LoadTaskExtraInline,
    "DISPATCH": DispatchTaskExtraInline,
    "REPLEN": ReplenishTaskExtraInline,
    "RELOC": RelocTaskExtraInline,  # 规范值
    "Reloc": RelocTaskExtraInline,  # 兼容当前模型里 choices 的 "Reloc"
    "COUNT": CountTaskExtraInline,
    "QC": QCTaskExtraInline,
    "ADJUST": AdjustTaskExtraInline,  # 你模型里若未提供该类型，可忽略
}

def _err_to_text(e: ValidationError) -> str:
    if getattr(e, "message_dict", None):
        return "；".join(f"{k}: {', '.join(map(str, v))}" for k, v in e.message_dict.items())
    return "；".join(getattr(e, "messages", [str(e)]))

# ========= 任务头 Admin =========
def _bool_from_post(request, key, default=False):
    """
    从 POST 取布尔值：支持 '1'/'true'/'on'/'yes' 为 True
    """
    val = request.POST.get(key, None)
    if val is None:
        return default
    return str(val).lower() in {"1", "true", "on", "yes"}

def _stats_brief(stats: dict) -> str:
    """
    把 publish_* 返回的统计信息做个简短摘要，便于 message_user 呈现
    """
    parts = []
    m = {
        "created_head": "头级指派",
        "activated_lines": "行级指派",
        "seeded_lines": "落地行",
        "ended_head": "结束头指派",
        "ended_lines": "结束行指派",
        "to_pool_lines": "进抢单行",
    }
    for k, label in m.items():
        v = int(stats.get(k, 0) or 0)
        if v:
            parts.append(f"{label}{v}")
    if not parts:
        return ""
    return "（" + "，".join(parts) + "）"

@admin.register(WmsTask)
class WmsTaskAdmin(admin.ModelAdmin):
    # —— 批量动作：状态流转 + 记录日志 —— #
    actions_selection_counter = True
    actions = ["action_wave_release_pick","action_approve", "action_reject","action_post","action_release", "action_start", "action_complete", "action_cancel"]
    list_display = (
        "task_no_clip","task_type", "status", "review_status","posting_status","priority",
        "owner", "released_at", "started_at", "finished_at","picked_by",
        "lines_count", "qty_plan_total", "qty_done_total","approved_at","posted_at",
    )
    list_filter = (
        "owner", "task_type", "status", "priority",

    )
    search_fields = (
        "task_no", "ref_no", "task_type", "source_app", "source_model", "source_pk", "remark",
    )
    date_hierarchy = "created_at"
    ordering = ("-created_at", "-id")
    list_select_related = ("owner", "warehouse")
    autocomplete_fields = ["owner", ]
    # inlines = (TaskAssignmentInline, TaskStatusLogInline, WmsTaskLineInline)
    inlines = []  # 动态决定

    formfield_overrides = {
        models.CharField: {'widget': forms.TextInput(attrs={'style': 'white-space: nowrap; width: auto;'})},
        models.TextField: {'widget': forms.Textarea(attrs={'style': 'white-space: nowrap; width: auto;'})},
    }

    class Media:
        css = {
            'all': ('css/admin_custom.css',)
        }


    @admin.display(description="任务号", ordering="task_no")
    def task_no_clip(self, obj):
        # 用 span 包起来，悬停显示完整任务号
        return format_html(
            '<span class="nowrap-ellipsis" title="{}">{}</span>',
            obj.task_no, obj.task_no
        )

    class Media:
        css = {"all": ("admin/custom/changelist.css",)}

    def get_inlines(self, request, obj):
        # 主管/超管：显示完整行 Inline
        if is_wh_manager(request.user):
            return [TaskAssignmentInline, TaskStatusLogInline, WmsTaskLineInline]  # 你的原有 inline
        # 操作员：不显示行 Inline（避免看见别人的）
        return []  # 或直接 []
    readonly_fields = ()

    fieldsets = (
        ("基本信息", {"fields": ("task_no", "task_type", "status", "priority","owner", "task_group_no", "released_at","remark")}),
        ("计划与实际", { "fields": (("planned_start", "planned_end"),("started_at", "finished_at"))}),
        ("来源快照", {"classes": ("collapse",),"fields": (("ref_no", "source_app"), ("source_model", "source_pk"))}),)
    # 任务头 Admin：按任务类型追加“头部 Extra” Inline
    change_form_template = "admin/tasking/wmstask/change_form.html"  # 若已自定义，保持一致

    def get_inline_instances(self, request, obj=None):
        instances = super().get_inline_instances(request, obj)

        # 1) 编辑页：直接读 obj.task_type
        # 2) 新建页：支持通过 ?task_type=RECEIVE 这样的参数控制
        t = None
        if obj:
            t = obj.task_type
        else:
            t = request.GET.get("task_type")

        inline_cls = _TASK_HEAD_INLINE_MAP.get(t)
        if inline_cls:
            # 注意需要实例化 Inline 类
            instances.append(inline_cls(self.model, self.admin_site))
        return instances

    def get_queryset(self, request):
        # ① 基础 + 你原来的聚合
        qs = (super().get_queryset(request)
        .select_related("owner", "warehouse")
        .annotate(
            _lines=Count("lines", distinct=True),
            _qty_plan=Sum("lines__qty_plan"),
            _qty_done=Sum("lines__qty_done"),
        ))

        # ② 管理员看全量（保留聚合后的 qs）
        if is_wh_manager(request.user):
            return qs
        # 非仓库操作员无权查看
        if not is_wh_operator(request.user):
            return qs.none()

        # ③ 操作员：仅看到“与我相关”或“含抢单行”的任务
        # 我在头级/行级的活动指派
        mine_head = TaskAssignment.objects.filter(
            task_id=OuterRef("pk"), line__isnull=True,
            assignee=request.user, finished_at__isnull=True
        )
        mine_line = TaskAssignment.objects.filter(
            task_id=OuterRef("pk"), line__isnull=False,
            assignee=request.user, finished_at__isnull=True
        )
        # 该任务是否存在“抢单行”：行上无活动行级指派；且任务头也无活动头级指派
        active_head_any = TaskAssignment.objects.filter(
            task_id=OuterRef("pk"), line__isnull=True, finished_at__isnull=True
        )
        # 子查询：有“无人认领”的行
        pool_line = (WmsTaskLine.objects
                     .filter(task_id=OuterRef("pk"))
                     .annotate(_has_line=Exists(
            TaskAssignment.objects.filter(line_id=OuterRef("pk"),
                                          finished_at__isnull=True)
        ))
                     .filter(_has_line=False))

        qs = qs.annotate(
            _mine_head=Exists(mine_head),
            _mine_line=Exists(mine_line),
            _has_head=Exists(active_head_any),
            _has_pool_line=Exists(pool_line),
        )

        # 可见任务：我负责（头或行） 或 含有抢单行（任务状态需为 RELEASED 且无头级活动指派）
        return qs.filter(
            Q(_mine_head=True) |
            Q(_mine_line=True) |
            (Q(status=WmsTask.Status.RELEASED) & Q(_has_head=False) & Q(_has_pool_line=True))
        )

    @admin.display(description="行数", ordering="_lines")
    def lines_count(self, obj):
        return obj._lines or 0

    @admin.display(description="计划数合计", ordering="_qty_plan")
    def qty_plan_total(self, obj):
        return obj._qty_plan or 0

    @admin.display(description="完成数合计", ordering="_qty_done")
    def qty_done_total(self, obj):
        return obj._qty_done or 0

    def _bulk_transition(self, request, queryset, to_status, ts_field=None):
        now = timezone.now()
        updated = 0
        for task in queryset:
            old = task.status
            if old == to_status:
                continue
            task.status = to_status
            if ts_field == "released_at" and not task.released_at:
                task.released_at = now
            if ts_field == "started_at" and not task.started_at:
                task.started_at = now
            if ts_field == "finished_at":
                task.finished_at = now
            task.save(update_fields=["status", ts_field] if ts_field else ["status"])
            TaskStatusLog.objects.create(
                task=task, old_status=old, new_status=to_status,
                changed_by=request.user, note="admin bulk action"
            )
            updated += 1
        self.message_user(request, f"已更新 {updated} 条任务状态为 {to_status}。")

    def _as_wh_mgr(self, request):
        return request.user.is_superuser or request.user.has_perm("tasking.taskconfirm_as_wh_manager")

    def get_actions(self, request):
        actions = super().get_actions(request)
        if not self._as_wh_mgr(request):
            actions.pop("action_release", None)

        order = ["action_release","action_approve", "action_reject", "action_post",  "action_start", "action_complete", "action_cancel","delete_selected"]

        ordered = OrderedDict()
        for name in order:
            if name == "delete_selected" and "delete_selected" not in actions:
                # 没有删除权限时，不加入
                continue
            if name in actions:
                ordered[name] = actions[name]

        # 若仍有其他动作（第三方注入），附在最后
        for k, v in actions.items():
            if k not in ordered:
                ordered[k] = v
        return ordered

    @admin.action(description="发布所选的任务")
    def action_release(self, request, queryset):
        if not self._as_wh_mgr(request):
            raise PermissionDenied

        # 可从前端表单传入；不传则默认 True/False
        seed_lines = _bool_from_post(request, "seed_lines", default=True)
        overwrite = _bool_from_post(request, "overwrite", default=False)

        pks = list(queryset.values_list("pk", flat=True))
        ok = fail = skipped = 0
        fail_details = []

        with transaction.atomic():
            # 在事务里加行锁，防并发修改
            qs = (WmsTask.objects
                  .select_for_update(skip_locked=USE_SKIP_LOCKED)
                  .filter(pk__in=pks))

            locked_ids = set(qs.values_list("pk", flat=True))
            skipped = len(pks) - len(locked_ids)

            pk_to_obj = {obj.pk: obj for obj in qs}
            for pk in locked_ids:
                obj = pk_to_obj[pk]
                try:
                    # 关键：按当前 inline(活动指派) 发布
                    # - 若无任何活动指派 => 发布为抢单
                    # - 若有头/行指派 => 按“行级优先、头级兜底”发布
                    stats = svc.publish_using_inline(
                        obj,
                        seed_lines=seed_lines,  # True=把头级兜底落地到行；False=只保留兜底语义
                        overwrite=overwrite  # True=覆盖全部行；False=仅补“无人”的行
                    )
                    ok += 1
                    brief = _stats_brief(stats)
                    if brief:
                        self.message_user(
                            request,
                            f"{getattr(obj, 'task_no', obj.pk)} 发布成功：{brief}",
                            level=messages.SUCCESS,
                        )
                except ValidationError as e:
                    fail += 1
                    fail_details.append(f"{getattr(obj, 'task_no', obj.pk)}：{_err_to_text(e)}")

        if ok:
            self.message_user(request, _(f"已发布 {ok} 条。"), level=messages.SUCCESS)
        if fail:
            for msg in fail_details[:20]:
                self.message_user(request, msg, level=messages.ERROR)
            if len(fail_details) > 20:
                self.message_user(request, f"还有 {len(fail_details) - 20} 条失败原因已省略。", level=messages.WARNING)
        if skipped:
            self.message_user(request, f"{skipped} 条记录被其他事务锁定，已跳过。", level=messages.INFO)

    @admin.action(description="取消所选的任务")
    def action_cancel(self, request, queryset):
        self._bulk_transition(request, queryset, "CANCELLED")

    # ===== 单条操作按钮 =====
    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path("<int:task_id>/print-dispatch/", self.admin_site.admin_view(self.goto_print),name="task_print_dispatch"),
            path("<int:task_id>/print-dispatch-save/", self.admin_site.admin_view(self.goto_print_save), name="task_print_dispatch_save"),
            path("<int:task_id>/dispatch-pdf/", self.admin_site.admin_view(self.goto_pdf), name="task_print_dispatch_pdf"),
            path("<int:object_id>/approve/", self.admin_site.admin_view(self.approve_view),name="tasking_wmstask_approve"),
            path("<int:object_id>/post/", self.admin_site.admin_view(self.post_view),name="tasking_wmstask_post"),
            path("count_wizard/", self.admin_site.admin_view(self.count_wizard),name="tasking_wmstask_count_wizard",),
        ]
        return custom + urls

    def goto_print(self, request, task_id: int):
        return redirect("/reports/dispatch/%d/" % task_id)

    def goto_print_save(self, request, task_id: int):
        return redirect(f"/reports/dispatch/{task_id}/?save=1")

    def goto_pdf(self, request, task_id: int):
        return redirect("/reports/dispatch/%d/pdf/" % task_id)

    def ops_buttons(self, obj: WmsTask):
        approve_url = reverse("admin:tasking_wmstask_approve", args=[obj.pk])
        post_url = reverse("admin:tasking_wmstask_post", args=[obj.pk])
        return format_html(
            '<a class="button" href="{}" style="margin-right:6px;">审核</a>'
            '<a class="button" href="{}">过账</a>',
            approve_url, post_url
        )
    ops_buttons.short_description = "操作"

    # ===== 批量动作 =====
    @admin.action(description="审核通过所选的任务")
    def action_approve(self, request, queryset):
        if not self._as_wh_mgr(request):
            raise PermissionDenied

        # 兼容 TextChoices / 纯字符串
        ReviewStatus = getattr(WmsTask, "ReviewStatus", None)
        PostingStatus = getattr(WmsTask, "PostingStatus", None)
        RS_PENDING = getattr(ReviewStatus, "PENDING", "PENDING") if ReviewStatus else "PENDING"
        RS_APPROVED = getattr(ReviewStatus, "APPROVED", "APPROVED") if ReviewStatus else "APPROVED"
        PS_PENDING = getattr(PostingStatus, "PENDING", "PENDING") if PostingStatus else "PENDING"

        with transaction.atomic():
            qs = queryset.select_for_update().filter(review_status=RS_PENDING)
            updated = qs.update(review_status=RS_APPROVED, posting_status=PS_PENDING)


        skipped = queryset.count() - updated
        self.message_user(
            request,
            f"已将任务头 审核状态 由 待审核 改为 通过审核通过：{updated} 条；跳过（非 待审核）：{skipped} 条。",
            level=messages.SUCCESS
        )

    @admin.action(description="驳回所选的任务")
    def action_reject(self, request, queryset):
        if not self._as_wh_mgr(request):
            raise PermissionDenied

        # 兼容 TextChoices / 纯字符串
        ReviewStatus = getattr(WmsTask, "ReviewStatus", None)

        RS_PENDING = getattr(ReviewStatus, "PENDING", "PENDING") if ReviewStatus else "PENDING"
        RS_REJECTED = getattr(ReviewStatus, "REJECTED", "REJECTED") if ReviewStatus else "REJECTED"


        with transaction.atomic():
            qs = queryset.select_for_update().filter(review_status=RS_PENDING)
            updated = qs.update(review_status=RS_REJECTED)


        skipped = queryset.count() - updated
        self.message_user(
            request,
            f"已将任务头 审核状态 由 待审核 改为 通过驳回：{updated} 条；跳过（非 待审核）：{skipped} 条。",
            level=messages.SUCCESS
        )

    def approve_view(self, request, object_id: int):
        if not self._as_wh_mgr(request):
            raise PermissionDenied
        task = get_object_or_404(WmsTask, pk=object_id)
        if task.status not in {"READY", "RELEASED"}:
            self.message_user(request, f"当前状态({task.status})不可审核。", level=messages.WARNING)
            return redirect(self._change_url(task))
        WmsTask.objects.filter(pk=task.pk).update(status="APPROVED")
        self.message_user(request, "审核成功。", level=messages.SUCCESS)
        return redirect(self._change_url(task))

    def post_view(self, request, object_id: int):
        if not self._as_wh_mgr(request):
            raise PermissionDenied
        task = get_object_or_404(WmsTask, pk=object_id)
        if task.status != "APPROVED":
            self.message_user(request, f"当前状态({task.status})不可过账。", level=messages.WARNING)
            return redirect(self._change_url(task))
        try:
            res = post_task(task=task, user=request.user, posting_batch=None)
            if res.get("ok"):
                self.message_user(request, "过账成功。", level=messages.SUCCESS)
            else:
                self.message_user(request, f"过账失败：{res.get('warnings') or res}", level=messages.ERROR)
        except Exception as e:
            self.message_user(request, f"过账异常：{e}", level=messages.ERROR)
        return redirect(self._change_url(task))

    def _change_url(self, obj: WmsTask):
        return reverse("admin:tasking_wmstask_change", args=[obj.pk])

    @admin.action(description="过账所选的任务")
    def action_post(self, request, queryset):
        if not self._as_wh_mgr(request):
            raise PermissionDenied

        handler = get_posting_handler()
        ok, fail = 0, 0

        # 预拉常用外键，减少 N+1
        queryset = queryset.select_related("owner", "warehouse")

        for task in queryset:
            # 前置校验：必须“审核通过”方可过账
            if getattr(task, "review_status", None) != getattr(WmsTask.ReviewStatus, "APPROVED", "APPROVED"):
                fail += 1
                self.message_user(
                    request,
                    f"{getattr(task, 'task_no', task.pk)} 未审核通过，已跳过。",
                    level=messages.WARNING,
                )
                continue

            try:
                # 每条单独事务，避免一条失败拖累其他
                with transaction.atomic():
                    # 让处理器自动查“可过账扫描”（不传 scans 即可）
                    print("handler.handle task.id task by_user:::",task.id,task,request.user)
                    handler.handle(task=task, scans=None, note="ADMIN", by_user=request.user)
                ok += 1
            except Exception as e:
                fail += 1
                # 可视情况少量透出失败原因
                self.message_user(
                    request,
                    f"{getattr(task, 'task_no', task.pk)} 过账失败：{e}",
                    level=messages.ERROR,
                )
        self.message_user(request, f"过账成功 {ok} 条，失败 {fail} 条。", level=messages.INFO)

    def action_wave_release_pick(self, request, queryset):
        pick_type = getattr(WmsTask.TaskType, "PICK", "PICK")
        ids = list(queryset.filter(task_type=pick_type, status="DRAFT").values_list("id", flat=True))
        if not ids:
            self.message_user(request, "没有可放行的拣货草稿。", level=messages.WARNING)
            return
        updated = ob_services.wave_release(ids)  # 之前已提供
        self.message_user(request, f"已放行 {updated} 个拣货任务为 READY。", level=messages.SUCCESS)

    action_wave_release_pick.short_description = "放行拣货任务"

    # 让“新增页面”读取我们通过 GET 传来的初始值
    def get_changeform_initial_data(self, request):
        initial = super().get_changeform_initial_data(request)
        q = request.GET
        if "task_type" in q:
            initial["task_type"] = q.get("task_type")
        if "owner" in q:
            initial["owner"] = q.get("owner")
        if "warehouse" in q:
            initial["warehouse"] = q.get("warehouse")
        return initial

    # 在“任务列表页”注入新增盘点任务按钮链接
    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        # 只有有新增权限才展示
        extra_context["has_add_permission"] = self.has_add_permission(request)
        add_url = reverse("admin:tasking_wmstask_add")
        # 预选 task_type=COUNT
        extra_context["add_count_url"] = f"{add_url}?{urlencode({'task_type': WmsTask.TaskType.COUNT})}"
        extra_context["count_wizard_url"] = reverse("admin:tasking_wmstask_count_wizard")
        return super().changelist_view(request, extra_context=extra_context)

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        extra_context = extra_context or {}
        extra_context["count_wizard_url"] = reverse("admin:tasking_wmstask_count_wizard")
        return super().changeform_view(request, object_id, form_url, extra_context=extra_context)

    def count_wizard(self, request,*args, **kwargs):
            form = CountWizardForm(request.POST or None)
            if form.is_valid():
                # form = CountWizardForm(request.POST)
                if not form.is_valid():
                    return render(request, "admin/tasking/count_wizard.html", {"form": form})

                owner = form.cleaned_data["owner"]
                warehouse = form.cleaned_data["warehouse"]

                # 用你系统里已在用的 DocSequence 生成单号
                task_no = DocSequence.next_code(
                    doc_type="PD",  # 你们的盘点代号，按需改
                    warehouse=warehouse,
                    owner=owner,
                    biz_date=date.today(),
                )

                remark_val = (form.cleaned_data.get("memo") or "").strip()
                task = WmsTask.objects.create(
                    task_no=task_no,  # 你的编号逻辑
                    task_type=WmsTask.TaskType.COUNT,
                    owner=form.cleaned_data["owner"],
                    warehouse=form.cleaned_data["warehouse"],
                    status=WmsTask.Status.DRAFT,
                    remark =remark_val,  # ← 等号！
                    created_by=request.user,
                )

                created = generate_count_lines(
                    task,
                    zone_type=form.cleaned_data.get("zone_type"),
                    location=form.cleaned_data.get("location"),
                    location_prefix=form.cleaned_data.get("location_prefix"),
                    product=form.cleaned_data.get("product"),
                    batch_no=form.cleaned_data.get("batch_no"),
                    ignore_zero=form.cleaned_data.get("ignore_zero", True),
                    limit=form.cleaned_data.get("max_lines") or 1000,
                    # 向导如未提供盘点方式，则使用默认 BLIND
                )

                messages.success(request, f"已创建盘点任务 {task.task_no}，生成行数：{created}")
                return redirect("admin:tasking_wmstask_change", object_id=task.pk)

            # GET：展示表单
            return render(request, "admin/tasking/count_wizard.html", {"form": CountWizardForm()})

    def _render_wizard(self, request, context):
        from django.template.response import TemplateResponse
        return TemplateResponse(request, "admin/tasking/wmstask/count_wizard.html", context)

# ========= 行级 Extra Inlines（OneToOne） =========
class _BaseLineExtraInline(admin.StackedInline):
    extra = 0
    max_num = 1
    can_delete = True

    # 保底：没有自定义表单时也用我们的 widget
    formfield_overrides = {
        models.DecimalField: {"widget": TrimDecimalWidget(attrs={"step": "any"})}
    }

    # 强制覆盖任何自定义 NumberInput
    def get_formset(self, request, obj=None, **kwargs):
        FormSet = super().get_formset(request, obj, **kwargs)
        Form = FormSet.form
        for f in Form.base_fields.values():
            if isinstance(f, forms.DecimalField):
                f.widget = TrimDecimalWidget(attrs={"step": "any"})
                if obj is None and f.initial in (None, "", 0):
                    f.initial = 0
        return FormSet

class ReceiveLineExtraInline(_BaseLineExtraInline):
    model = ReceiveLineExtra

class PutawayLineExtraInline(_BaseLineExtraInline):
    model = PutawayLineExtra

class PickLineExtraInline(_BaseLineExtraInline):
    model = PickLineExtra

class ReviewLineExtraInline(_BaseLineExtraInline):
    model = ReviewLineExtra

# class ReviewLineExtraInline(admin.StackedInline):
#     model = ReviewLineExtra
#     fields = ['from_location', 'qty_reviewed', 'qty_picked', 'discrepancy_reason']
#     extra = 1  # 至少显示一个空行
#
#     def get_form(self, request, obj=None, **kwargs):
#         # 调用父类的 get_form 方法来获取表单
#         form = super().get_form(request, obj, **kwargs)
#
#         # 如果是 "REVIEW" 类型任务，初始化表单数据
#         if obj and obj.task.task_type == "REVIEW":
#             reviewlineextra_data = ReviewLineExtra.objects.filter(line=obj).first()
#             if reviewlineextra_data:
#                 form.initial = {
#                     'qty_reviewed': reviewlineextra_data.qty_reviewed,
#                     'qty_picked': reviewlineextra_data.qty_picked,
#                 }
#                 print("Form initial data:", form.initial)  # 调试输出，检查初始化数据
#         return form

class PackLineExtraInline(_BaseLineExtraInline):
    model = PackLineExtra

class LoadLineExtraInline(_BaseLineExtraInline):
    model = LoadLineExtra

class DispatchLineExtraInline(_BaseLineExtraInline):
    model = DispatchLineExtra

class ReplenishLineExtraInline(_BaseLineExtraInline):
    model = ReplenishLineExtra

class RelocLineExtraInline(_BaseLineExtraInline):
    model = RelocLineExtra

from django.contrib import admin
from allapp.tasking.models import WmsTaskLine, CountLineExtra

def _is_blind_count(obj: WmsTaskLine) -> bool:
    """
    判断该行是否处于复盘/三盘（SECOND/THIRD）。
    obj 是父对象 WmsTaskLine。利用一对一反向关系拿到扩展。
    """
    print("0 _is_blind_count")
    if not obj:
        print("1 not obj _is_blind_count")
        return False
    try:
        print("2  obj _is_blind_count")
        order = obj.countlineextra.countorder
    except CountLineExtra.DoesNotExist:
        print("3 except CountLineExtra.DoesNotExist")
        return False
    print("4 return order _is_blind_count")
    return order in (CountLineExtra.CountOrder.SECOND, CountLineExtra.CountOrder.THIRD)

class CountLineExtraInline(admin.StackedInline):
    model = CountLineExtra
    extra = 0
    # 给出一个完整顺序，等会儿按需剔除
    fields = (
        "lot_no", "exp_date", "lpn_no",
        "qty_counted", "qty_book", "qty_diff",
        "count_status", "method", "countorder",
    )
    # 注意：这里不要把 qty_book/qty_diff 放到 readonly_fields！否则很难动态移除
    readonly_fields = ()

    # 1) 从“渲染用字段集”里剔除（模板层一定会调用 get_fieldsets）
    def get_fieldsets(self, request, obj=None):
        print("get_fieldsets")
        fs = super().get_fieldsets(request, obj)
        if not _is_blind_count(obj):
            return fs
        new = []
        for name, opts in fs:
            fields = list(opts.get("fields", ()))
            for nm in ("qty_book", "qty_diff"):
                if nm in fields:
                    fields.remove(nm)
            new.append((name, {"fields": tuple(fields)}))
        return new

    # 2) 从“表单字段”里剔除（避免被提交/校验）
    def get_formset(self, request, obj=None, **kwargs):
        print("get_formset")
        FormSet = super().get_formset(request, obj, **kwargs)
        if not _is_blind_count(obj):
            return FormSet
        base_form = FormSet.form

        class BlindForm(base_form):
            def __init__(self, *args, **kw):
                super().__init__(*args, **kw)
                # 二次确保表单层也没有
                for nm in ("qty_book", "qty_diff"):
                    self.fields.pop(nm, None)

        FormSet.form = BlindForm
        return FormSet

# —— 首盘用：完整字段 —— #
class CountLineExtraInlineFull(admin.StackedInline):
    model = CountLineExtra
    extra = 0
    can_delete = False
    fields = (
        "lot_no", "exp_date", "lpn_no",
        "qty_counted", "qty_book", "qty_diff",
        "count_status", "method", "countorder",
    )
    # 注意：不要把 qty_* 放 readonly_fields 里，否则很难动态控制
    readonly_fields = ()

# —— 复盘/三盘用：不渲染账面数与差异 —— #
class CountLineExtraInlineBlind(admin.StackedInline):
    model = CountLineExtra
    extra = 0
    can_delete = False
    fields = (
        "lot_no", "exp_date", "lpn_no",
        "qty_counted",
        "count_status", "method", "countorder",
    )
    readonly_fields = ()

class QCLineExtraInline(_BaseLineExtraInline):
    model = QCLineExtra

class AdjustLineExtraInline(_BaseLineExtraInline):
    model = AdjustLineExtra

_LINE_EXTRA_MODEL_MAP = {
    "RECEIVE": ReceiveLineExtra,
    "PUTAWAY": PutawayLineExtra,
    "PICK": PickLineExtra,
    "REVIEW": ReviewLineExtra,
    "PACK": PackLineExtra,
    "LOAD": LoadLineExtra,
    "DISPATCH": DispatchLineExtra,
    "REPLEN": ReplenishLineExtra,
    "RELOC": RelocLineExtra,
    "Reloc": RelocLineExtra,  # 兼容旧枚举
    "COUNT": CountLineExtra,
    "QC": QCLineExtra,
    "ADJUST": AdjustLineExtra,
}

# ========= 其余模型 Admin =========
#人工处理一行多桶
class TaskScanLogInline(admin.TabularInline):
    model = TaskScanLog
    extra = 2
    fields = ("qty_base", "lot_no", "exp_date", "location",  "remark")
    readonly_fields = ()
    # 只显示本行，默认手工录入
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.filter(method="MANUAL")  # 只露出手工行，扫描的也可一并显示就去掉这行

    def get_formset(self, request, obj=None, **kwargs):
        formset = super().get_formset(request, obj, **kwargs)
        # 将 method/source 设默认值
        # formset.form.base_fields["method"].initial = "MANUAL"
        # formset.form.base_fields["source"].initial = "WEB"
        return formset

#任务行admin
# ====== 你的 WmsTaskLineAdmin（在此基础上融合行级 Extra Inline） ======
@admin.register(WmsTaskLine)
class WmsTaskLineAdmin(DecimalPrettyInitialMixin,admin.ModelAdmin):
    # inlines = [TaskScanLogInline]
    # inlines = [ReviewLineExtraInline, TaskScanLogInline]
    actions = ["act_claim_lines"]
    list_display = ("id", "task", "status","product", "from_location", "to_location", "qty_plan", "qty_done", "src_model", "src_id")
    list_filter = ("task__task_type", "task__status", "product", "from_location", "to_location")
    search_fields = ("task__task_no", "src_model", "src_id", "rule_key")
    list_select_related = ("task", "product", "from_location", "to_location")
    autocomplete_fields = ["task", "product", "from_location", "to_location", "bound_content_type"]
    readonly_fields = ()
    ordering = ("-id",)
    fields = ("task", "product","from_location", "to_location", "qty_plan", "qty_done", "remark")

    class Media:
        js  = ("tasking/count_blind.js",)   # ← 改个版本号
    # 任务行 Admin：按所属任务类型追加“行 Extra” Inline（仅 1 个）
    def get_fields(self, request, obj=None):
        f = list(super().get_fields(request, obj))
        if _is_blind_count(obj):
            print("tasklineadmin get fields _is_blind_count")
            # 复盘/三盘：不渲染账面数和差异
            for name in ("qty_book", "qty_diff","qty_plan"):
                if name in f:
                    f.remove(name)
            print("tasklineadmin get fields _is_blind_count f=",f)
        return f
    def get_inline_instances(self, request, obj=None):
        instances = super().get_inline_instances(request, obj)

        # —— 尽力获取任务类型 —— #
        t = None
        if obj and getattr(obj, "task_id", None):
            t = obj.task.task_type
        else:
            task_id = request.GET.get("task")
            if task_id:
                try:
                    t = WmsTask.objects.only("task_type").get(pk=task_id).task_type
                except WmsTask.DoesNotExist:
                    pass
            if not t:
                t = request.GET.get("task__task_type")

        entry = LINE_EXTRA_REGISTRY.get(t)

        # 若映射缺失，但这条行已有某个 Extra，也尝试“反查命中”
        if not entry and obj:
            for e in LINE_EXTRA_REGISTRY.values():
                if hasattr(obj, e.rel_attr) and getattr(obj, e.rel_attr) is not None:
                    entry = e
                    break

        if entry:
            inline_cls = make_extra_inline(entry)
            instances.append(inline_cls(self.model, self.admin_site))
        return instances

    def get_queryset(self, request):
        """
        收货员视图规则（合并显示）：
        - 我的单（优先展示）：行级指派=我，或 头级指派=我 且 该行无人
        - 可抢单：任务=RELEASED 且 头级无人 且 该行无人
        - 排序：我的在前（行级>头级兜底），再到可抢单，组内按 id 逆序
        经理：全量；非仓库操作：空
        """
        # 1) 外键预取
        qs = (super().get_queryset(request)
              .select_related("task", "product", "from_location", "to_location"))
        qs = _select_related_all_extras(qs)

        # 2) 角色分流
        if is_wh_manager(request.user):
            return qs.order_by("-id")
        if not is_wh_operator(request.user):
            return qs.none()

        me = request.user

        # 3) 公用“活动指派”子查询
        active_line_any = TaskAssignment.objects.filter(
            line_id=OuterRef("pk"), finished_at__isnull=True
        )
        active_line_me = active_line_any.filter(assignee=me)

        active_head_any = TaskAssignment.objects.filter(
            task_id=OuterRef("task_id"), line__isnull=True, finished_at__isnull=True
        )
        active_head_me = active_head_any.filter(assignee=me)

        # 4) 注解布尔位：我的/可抢
        qs = qs.annotate(
            _mine_line=Exists(active_line_me),  # 我在行级被指派
            _mine_head=Exists(active_head_me),  # 我在头级被指派
            _has_line=Exists(active_line_any),  # 该行是否已有行级指派
            _has_head=Exists(active_head_any),  # 该任务头是否已有头级指派
        )

        # “我的单”条件：行级优先；否则头级=我且该行无人
        cond_mine = Q(_mine_line=True) | (Q(_mine_head=True) & Q(_has_line=False))

        # “可抢单”条件：任务 RELEASED + 无头级 + 该行无人
        # cond_pool = Q(task__status=WmsTask.Status.RELEASED) & Q(_has_head=False) & Q(_has_line=False)
        cond_pool = (
                Q(task__status__in=[WmsTask.Status.RELEASED, WmsTask.Status.IN_PROGRESS])  # 任务是 RELEASED 或 IN_PROCESS
                & Q(_has_head=False)  # 无头级指派
                & Q(_has_line=False)  # 该行无人
                & Q(assignments__finished_at__isnull=True)  # 任务指派未完成（finished_at 为空）
        )


        print("tasklineadmin cond_pool",cond_pool)
        # 5) 合并筛选：我的 ∪ 可抢
        qs = qs.filter(cond_mine | cond_pool)
        print("tasklineadmin qs", qs)
        # task=qs.filter() or None
        # if task:
        #     print("self.task.review_status,self.task.posting_statu",task.review_status,task.posting_statu)

        # 6) 排序：我的在前（行级>头级兜底），再可抢；组内按 id 逆序
        # 说明：Django 对布尔注解可直接排序；True>False，因此用降序把 True 放前面
        return qs.order_by(
            '-_mine_line',  # 我=行级指派优先（True 在前）
            '-_mine_head',  # 我=头级兜底其次
            '-id',  # 组内按 id 倒序
        )

    @admin.action(description="认领所选任务行")
    def act_claim_lines(self, request, queryset):
        if not is_wh_operator(request.user):
            raise PermissionDenied

        line_ids = list(queryset.values_list("pk", flat=True))
        ok = fail = skipped = 0
        details = []

        with transaction.atomic():
            # 锁住选中的行，避免并发重复认领
            qs = (WmsTaskLine.objects
                  .select_for_update(skip_locked=USE_SKIP_LOCKED)
                  .select_related("task")
                  .filter(pk__in=line_ids))
            locked_ids = set(qs.values_list("pk", flat=True))
            skipped = len(line_ids) - len(locked_ids)

            # 预取“是否有头级活动指派 / 行级活动指派”
            active_head_any = TaskAssignment.objects.filter(
                task_id=OuterRef("task_id"),
                line__isnull=True,
                finished_at__isnull=True,
            )
            active_line_any = TaskAssignment.objects.filter(
                line_id=OuterRef("pk"),
                finished_at__isnull=True,
            )

            qs = qs.annotate(
                _has_head=Exists(active_head_any),
                _has_line=Exists(active_line_any),
            )

            now = timezone.now()

            for line in qs:
                t = line.task
                # 规则校验
                if t.status != WmsTask.Status.RELEASED:
                    fail += 1
                    details.append(f"{getattr(t, 'task_no', t.pk)}-行{line.pk}：任务非抢单状态。")
                    continue
                if line._has_head:
                    fail += 1
                    details.append(f"{getattr(t, 'task_no', t.pk)}-行{line.pk}：存在头级指派，不能抢单。")
                    continue
                if line._has_line:
                    fail += 1
                    details.append(f"{getattr(t, 'task_no', t.pk)}-行{line.pk}：该行已被指派。")
                    continue

                # 创建/激活行级指派（幂等）
                ta, _created = TaskAssignment.objects.get_or_create(
                    task=t, line=line, assignee=request.user,
                    defaults={"accepted_at": now}
                )
                if ta.finished_at is not None:
                    ta.finished_at = None
                    ta.accepted_at = ta.accepted_at or now
                    ta.save(update_fields=["finished_at", "accepted_at"])

                # 任务由 RELEASED → ASSIGNED
                if t.status == WmsTask.Status.RELEASED:
                    t._allow_status_write = True
                    t.status = getattr(WmsTask.Status, "ASSIGNED", WmsTask.Status.RELEASED)
                    t.save(update_fields=["status"])

                ok += 1

        if ok:
            self.message_user(request, f"成功认领 {ok} 条。", level=messages.SUCCESS)
        if fail:
            for m in details[:20]:
                self.message_user(request, m, level=messages.WARNING)
            if len(details) > 20:
                self.message_user(request, f"还有 {len(details) - 20} 条失败原因已省略。", level=messages.INFO)
        if skipped:
            self.message_user(request, f"{skipped} 条记录被其他事务锁定，已跳过。", level=messages.INFO)

    # @transaction.atomic
    # def save_related(self, request, form, formsets, change):
    #     """
    #     让 Admin 先把 inlines 落库，再读取最新 1:1 扩展值，重建快照生成手工 ScanLog。
    #     """
    #     print("Saving related data...")
    #     if form.is_valid():
    #         print(form.cleaned_data)  # 检查 qty_reviewed 和 qty_picked 是否传递
    #
    #     print("1 taskline save_related aaa")
    #     super().save_related(request, form, formsets, change)
    #     # print("request, form, formsets, change 2 taskline save_related",request, form, formsets, change)
    #
    #     tl: WmsTaskLine = form.instance
    #     if not tl.task_id:
    #         return
    #
    #     print("0 # 已进入审核/过账阶段，不覆盖，直接返回")
    #     # 已进入审核/过账阶段，不覆盖，直接返回
    #     if getattr(tl.task, "review_status", "") == "APPROVED" or \
    #        getattr(tl.task, "posting_status", "") in ("PENDING", "POSTED"):
    #         return
    #     print("1 # 已进入审核/过账阶段，不覆盖，直接返回")
    #
    #     # models_to_check = ["ReceiveLineExtra", "PutawayLineExtra","PickLineExtra","ReviewLineExtra","PackLineExtra","LoadLineExtra","DispatchLineExtra","ReplenishLineExtra","CountLineExtra","AdjustLineExtra"]
    #     # extra_obj = None
    #     # for fs in formsets:
    #     #     print("2 Extra save_related fs",fs)
    #     #     # 找到那个 inline（模型类名按你实际导入修改）
    #     #     # if getattr(getattr(fs, "model", None), "__name__", "") == "ReceiveLineExtra":
    #     #     if getattr(getattr(fs, "model", None), "__name__", "") in models_to_check:
    #     #         print("3  getattr Extra save_related ",getattr(getattr(fs, "model", None), "__name__", ""))
    #     #         # fs.save() 已由 super().save_related 调过，这里拿实例更稳
    #     #         # cleaned_data 里可能包含删除/空表单，过滤一下
    #     #         for f in fs.forms:
    #     #             # print("f in fs.forms",f)
    #     #             print("f.cleaned_data", f.cleaned_data)
    #     #             if f.cleaned_data and not f.cleaned_data.get("DELETE"):
    #     #                 extra_obj = f.instance  # ← 本次提交后的最新值
    #     #                 break
    #     #         break
    #
    #     # ★ 通用：按任务类型/一对一反查拿扩展
    #     extra_obj = get_line_extra_generic(tl)
    #
    #     print("4 Extra save_related")
    #     if not extra_obj:
    #         print("not extra_obj aaa Extra save_related")
    #         return  # 没有扩展就不生成
    #     print("5 Extra save_related")
    #     # 3) 用“最新实例”取字段，避免 relation 缓存
    #     product  = getattr(tl, "product", None)
    #     location = getattr(extra_obj, "to_location", None) or getattr(extra_obj, "location", None) or getattr(tl, "to_location", None)
    #     lot_no   = getattr(extra_obj, "lot_no", None)
    #     expiry   = getattr(extra_obj, "exp_date", None) or getattr(extra_obj, "expiry_date", None)
    #     serial   = getattr(extra_obj, "serial_no", None)
    #     qty_ok   = (   getattr(extra_obj, "qty_ok", None)
    #                 or getattr(extra_obj, "qty_moved", None)
    #                 or getattr(extra_obj, "qty_picked", None)
    #                 or getattr(extra_obj, "qty_dispatch", None)
    #                 or getattr(extra_obj, "qty_counted", None))
    #
    #     print("50 qty_ok=",qty_ok)
    #     if not qty_ok:
    #         return  # 无合格数量不建 ScanLog
    #
    #     print("6 taskline save_related")
    #     # 没有数量就不建 ScanLog
    #     if not qty_ok or not product:
    #         return
    #
    #     # 组装 items（其余归属信息在服务层由 tl.task 统一补齐）
    #     items = [{
    #         "product":      product,            # Product 实例
    #         "location":     location,           # Location 或 None
    #         "lot_no":       lot_no,             # 批次或 None
    #         "expiry_date":  expiry,             # 日期或 None
    #         "serial_no":    serial,             # 可选
    #         "qty_ok":       qty_ok,             # Decimal
    #     }]
    #     print("1403 taskline save_related item items",len(items),items)
    #     # —— 重建快照（软删旧 READY + 行版本自增 + 新建 READY 日志）——
    #     try:
    #         services.save_receiving_snapshot(task_line_id=tl.id,items=items,
    #             operator=request.user,   # by_user
    #             source="ADMIN",)
    #     except ValueError as e:
    #         # 业务性异常：用消息提示并吞掉，避免 500
    #         self.message_user(request, str(e), level=messages.WARNING)
    #         return
    #     except Exception as e:
    #         # 非预期异常：记录日志并提示
    #         logger.exception("save_receiving_snapshot failed (line=%s)", tl.id)
    #         self.message_user(request, "生成 ScanLog 失败，请联系管理员。", level=messages.ERROR)
    #         return

    @transaction.atomic
    def save_related(self, request, form, formsets, change):
        from allapp.tasking.services import is_task_locked,build_scanlog_items,FINALIZERS
        # 先让 Django 把父表单 & inline 都落库
        super().save_related(request, form, formsets, change)

        tl: WmsTaskLine = form.instance
        task = getattr(tl, "task", None)
        if not getattr(tl, "task_id", None) or not task:
            return

        # 头部锁定就跳过（更友好：消息提示而非 500）

        if is_task_locked(task):
            self.message_user(request, "任务已进入审核/过账阶段，跳过生成日志。", level=messages.INFO)
            return

        # 通用：取对应的 LineExtra（不依赖 formsets 顺序/是否为空）
        extra = get_line_extra_generic(tl)
        if not extra:
            return

        # 通用：拼装 scanlog items（按不同扩展的字段自动兼容）
        items = build_scanlog_items(tl, extra)
        if not items:
            return

        # 写快照/ScanLog —— 兜住异常，避免请求被 ValueError 终止
        try:
            services.save_receiving_snapshot(
                task_line_id=tl.id,
                items=items,
                operator=request.user,
                source="ADMIN",
            )
        except ValueError as e:
            self.message_user(request, str(e), level=messages.WARNING)
            return
        except Exception:
            logger.exception("save_receiving_snapshot failed (line=%s)", tl.id)
            self.message_user(request, "生成扫描日志失败，请联系管理员。", level=messages.ERROR)
            return

        # 按任务类型做“收尾”（比如盘点：若全部已盘则生成下一轮/推进状态）
        finalizer = FINALIZERS.get(getattr(task, "task_type", None))
        if callable(finalizer):
            try:
                finalizer(task, by_user=request.user)
            except Exception:
                logger.exception("finalizer failed (task=%s)", task.id)
                self.message_user(request, "任务收尾失败，请联系管理员。", level=messages.WARNING)

def get_inline_instances(self, request, obj=None):
    # 显式两参 super，规避 __class__ cell 问题
    instances = super(WmsTaskLineAdmin, self).get_inline_instances(request, obj)

    # —— 1) 判定 task_type —— #
    t = None
    if obj and getattr(obj, "task_id", None):
        t = obj.task.task_type
    else:
        task_id = request.GET.get("task")
        if task_id:
            try:
                t = WmsTask.objects.only("task_type").get(pk=task_id).task_type
            except WmsTask.DoesNotExist:
                pass
        if not t:
            t = request.GET.get("task__task_type")

    # —— 2) 取注册项；失败则“反查已有 extra”容错 —— #
    entry = LINE_EXTRA_REGISTRY.get(t)
    if not entry and obj:
        for e in LINE_EXTRA_REGISTRY.values():
            try:
                val = getattr(obj, e.rel_attr)
                if hasattr(val, "exists"):
                    if not val.exists():
                        val = None
            except Exception:
                val = None
            if val is not None:
                entry = e
                break

    # —— 3) 生成（仅一次）并追加 inline 实例 —— #
    inline_inst = None
    if entry:
        inline_cls = getattr(entry, "_inline_cls", None)
        if inline_cls is None:
            inline_cls = make_extra_inline(entry)
            entry._inline_cls = inline_cls  # 缓存，避免重复造类
        inline_inst = inline_cls(self.model, self.admin_site)
        instances.append(inline_inst)

    # —— 4) 若没有现成 Extra，按任务类型注入 initial —— #
    if obj and inline_inst and entry:
        need_initial = False
        try:
            rel_obj = getattr(obj, entry.rel_attr)
            if hasattr(rel_obj, "exists"):
                need_initial = not rel_obj.exists()
            else:
                need_initial = rel_obj is None
        except ObjectDoesNotExist:
            need_initial = True
        except Exception:
            need_initial = True

        if need_initial:
            initial = {}
            if t == "REVIEW":
                e = ReviewLineExtra.objects.filter(line=obj).first()
                if e:
                    initial.update(
                        qty_reviewed=e.qty_reviewed,
                        qty_plan_origin=e.qty_plan_origin,
                        qty_picked_origin=e.qty_picked_origin,
                    )
            elif t == "PICK":
                e = PickLineExtra.objects.filter(line=obj).first()
                if e:
                    initial.update(
                        qty_picked=e.qty_picked,
                        qty_short=e.qty_short,
                    )

            if initial:
                inline_inst._initial_for_fields = initial
    if not _is_blind_count(obj):
        return instances
    print("123 tasklineadmin _is_blind_count(obj) ")

    # 盲盘：把“模型=CountLineExtra”的 inline 全部替换成 Blind
    new_instances = []
    for inline in instances:
        # 调试：看看到底拿到的是什么类
        # print("inline class:", inline.__class__.__name__, " model:", getattr(inline, "model", None))

        if getattr(inline, "model", None) is CountLineExtra:
            new_instances.append(CountLineExtraInlineBlind(self.model, self.admin_site))
            # print("→ replaced with Blind")
        else:
            new_instances.append(inline)
            # print("→ kept:", inline.__class__.__name__)
    return new_instances

    return instances

WmsTaskLineAdmin.get_inline_instances = get_inline_instances

@admin.register(TaskAssignment)
class TaskAssignmentAdmin(admin.ModelAdmin):
    list_display = ("task", "line","assignee", "accepted_at", "finished_at")
    list_filter = ("assignee", "accepted_at", "finished_at")
    search_fields = ("task__task_no", "assignee__username", "assignee__first_name", "assignee__last_name")
    autocomplete_fields = ["task", "assignee"]
    date_hierarchy = "created_at"
    ordering = ("-created_at",)

    def get_model_perms(self, request):
        # 操作员看到这个模型=0 权限（彻底从菜单消失）
        if is_wh_operator(request.user) and not is_wh_manager(request.user) and not request.user.is_superuser:
            return {}
        return super().get_model_perms(request)

    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related("task", "line", "assignee")
        if is_wh_manager(request.user):
            return qs
        if not is_wh_operator(request.user):
            return qs.none()
        # 保险：即使知道 URL 也只能看到“自己的活动指派”
        return qs.filter(assignee=request.user)

@admin.register(TaskStatusLog)
class TaskStatusLogAdmin(admin.ModelAdmin):
    list_display = ("task", "old_status", "new_status", "changed_by", "changed_at", "note")
    list_filter = ("new_status", "old_status", "changed_by")
    search_fields = ("task__task_no", "note")
    autocomplete_fields = ["task", "changed_by"]
    date_hierarchy = "changed_at"
    ordering = ("-changed_at",)

@admin.register(TaskScanLog)
class TaskScanLogAdmin(admin.ModelAdmin):
    list_display = (
        "id", "task", "task_line", "product", "location",
        "barcode", "label_key", "qty_base_delta",
        "method", "source", "by_user",
        "review_status", "reviewed_at", "created_at",
    )
    list_filter = ("method", "source", "review_status", "by_user", "product", "location")
    search_fields = ("task__task_no", "barcode", "label_key", "reason_code", "remark")
    list_select_related = ("task", "task_line", "product", "location", "by_user")
    autocomplete_fields = ["task", "task_line", "product", "location", "by_user"]
    date_hierarchy = "created_at"
    ordering = ("-created_at", "-id")
    actions = ["mark_reviewed", "mark_pending"]

    @admin.action(description="标记为已通过复核")
    def mark_reviewed(self, request, queryset):
        n = queryset.update(review_status="APPROVED", reviewed_by=request.user, reviewed_at=timezone.now())
        self.message_user(request, f"已标记 {n} 条为 APPROVED。")

    @admin.action(description="标记为待复核")
    def mark_pending(self, request, queryset):
        n = queryset.update(review_status="PENDING", reviewed_by=None, reviewed_at=None)
        self.message_user(request, f"已标记 {n} 条为 PENDING。")

@admin.register(ContentType)
class _HiddenContentTypeAdmin(admin.ModelAdmin):
    # autocomplete 必须有 search_fields
    search_fields = ("app_label", "model")
    list_display = ("app_label", "model")

    # 返回空权限字典：让它不出现在侧边栏/索引里，但路由仍可用
    def get_model_perms(self, request):
        return {}


