# -*- coding: utf-8 -*-
"""
DRF serializers for allapp.tasking models

- 面向读写：任务头/行、Assignments/Logs、ScanLog，以及各类型 Extra（头/行）
- 读：包含 choices 的中文 display 字段；可选嵌套行与 Extra
- 写：支持创建任务头 + 行（可带对应 Extra）；更新时默认不处理行的增删改，避免复杂事务

如需更强的嵌套写入/并发控制，请在 Service 层实现并在此调用。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Type

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from rest_framework import serializers

from .models import (
    # 基类
    WmsTask, WmsTaskLine, TaskAssignment, TaskStatusLog, TaskScanLog,
    # Extra - 头
    ReceiveTaskExtra, PutawayTaskExtra, PickTaskExtra, PackTaskExtra, LoadTaskExtra,
    DispatchTaskExtra, ReplenishTaskExtra, RelocTaskExtra,
    # Extra - 行
    ReceiveLineExtra, PutawayLineExtra, PickLineExtra, PackLineExtra, LoadLineExtra,
    DispatchLineExtra, ReplenishLineExtra, RelocLineExtra,
)

# --------- Helpers ---------

TASK_EXTRA_MAP: Dict[str, Type[serializers.ModelSerializer]] = {}
LINE_EXTRA_MAP: Dict[str, Type[serializers.ModelSerializer]] = {}


def task_extra_model(task_type: str):
    return {
        "RECEIVE": ReceiveTaskExtra,
        "PUTAWAY": PutawayTaskExtra,
        "PICK": PickTaskExtra,
        "PACK": PackTaskExtra,
        "LOAD": LoadTaskExtra,
        "DISPATCH": DispatchTaskExtra,
        "REPLEN": ReplenishTaskExtra,
        "RELOC": RelocTaskExtra,
    }.get((task_type or "").upper())


def line_extra_model(task_type: str):
    return {
        "RECEIVE": ReceiveLineExtra,
        "PUTAWAY": PutawayLineExtra,
        "PICK": PickLineExtra,
        "PACK": PackLineExtra,
        "LOAD": LoadLineExtra,
        "DISPATCH": DispatchLineExtra,
        "REPLEN": ReplenishLineExtra,
        "RELOC": RelocLineExtra,
    }.get((task_type or "").upper())


class ChoiceDisplayMixin:
    """在序列化输出中附带 *_display 字段。"""

    def _display_field(self, instance, field_name: str) -> Optional[str]:
        method = getattr(instance, f"get_{field_name}_display", None)
        if callable(method):
            try:
                return method()
            except Exception:
                return None
        return None


# --------- Atomic/primitive serializers ---------

class TaskAssignmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = TaskAssignment
        fields = [
            "id", "task", "assignee", "accepted_at", "finished_at",
        ]
        read_only_fields = ["id"]


class TaskStatusLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = TaskStatusLog
        fields = [
            "id", "task", "old_status", "new_status", "changed_by", "changed_at", "note",
        ]
        read_only_fields = ["id", "changed_at"]


class TaskScanLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = TaskScanLog
        fields = [
            "id", "task", "task_line", "product", "location",
            "method", "source", "by_user",
            "barcode", "label_key",
            "code_type", "uom_code", "pack_qty",
            "qty_aux", "qty_base", "qty_base_delta",
            "lot_no", "mfg_date", "exp_date", "container_no",
            "review_status", "reason_code", "remark",
            "reviewed_by", "reviewed_at",
            "fp", "created_at",
        ]
        read_only_fields = ["id", "created_at"]


# --------- Extra (Head) ---------

class ReceiveTaskExtraSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReceiveTaskExtra
        fields = ["id", "task", "receive_mode", "appt_start", "appt_end", "dock_code", "vehicle_no", "qc_required"]
        read_only_fields = ["id"]


class PutawayTaskExtraSerializer(serializers.ModelSerializer):
    class Meta:
        model = PutawayTaskExtra
        fields = ["id", "task", "strategy"]
        read_only_fields = ["id"]


class PickTaskExtraSerializer(serializers.ModelSerializer):
    class Meta:
        model = PickTaskExtra
        fields = ["id", "task", "wave_no", "pick_mode", "route_code", "ship_date", "cutoff_at"]
        read_only_fields = ["id"]


class PackTaskExtraSerializer(serializers.ModelSerializer):
    class Meta:
        model = PackTaskExtra
        fields = [
            "id", "task",
            "default_carrier_code", "default_service_level", "label_tpl_code", "default_pack_code",
            "check_policy", "check_ratio",
        ]
        read_only_fields = ["id"]


class LoadTaskExtraSerializer(serializers.ModelSerializer):
    class Meta:
        model = LoadTaskExtra
        fields = ["id", "task", "trip_no", "vehicle_no", "dock_code", "depart_eta"]
        read_only_fields = ["id"]


class DispatchTaskExtraSerializer(serializers.ModelSerializer):
    class Meta:
        model = DispatchTaskExtra
        fields = ["id", "task", "manifest_no", "carrier_code", "service_level", "wave_no"]
        read_only_fields = ["id"]


class ReplenishTaskExtraSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReplenishTaskExtra
        fields = ["id", "task", "trigger", "src_zone", "dst_zone", "policy_code"]
        read_only_fields = ["id"]


class RelocTaskExtraSerializer(serializers.ModelSerializer):
    class Meta:
        model = RelocTaskExtra
        fields = ["id", "task", "src_zone", "dst_zone", "policy_code", "reason_code"]
        read_only_fields = ["id"]


# --------- Extra (Line) ---------

class ReceiveLineExtraSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReceiveLineExtra
        fields = [
            "id", "line", "from_lpn", "lot_no", "mfg_date", "exp_date",
            "qty_ok", "qty_damage", "qty_reject",
            "damage_reason_code", "reject_reason_code",
        ]
        read_only_fields = ["id"]


class PutawayLineExtraSerializer(serializers.ModelSerializer):
    class Meta:
        model = PutawayLineExtra
        fields = [
            "id", "line", "plan_to_location", "to_location", "from_lpn", "to_lpn", "qty_moved",
        ]
        read_only_fields = ["id"]


class PickLineExtraSerializer(serializers.ModelSerializer):
    class Meta:
        model = PickLineExtra
        fields = [
            "id", "line", "from_location", "from_lpn", "to_container_no",
            "qty_picked", "qty_short", "short_reason",
        ]
        read_only_fields = ["id"]


class PackLineExtraSerializer(serializers.ModelSerializer):
    class Meta:
        model = PackLineExtra
        fields = ["id", "line", "to_container", "to_container_no", "aux_uom", "ratio"]
        read_only_fields = ["id"]


class LoadLineExtraSerializer(serializers.ModelSerializer):
    class Meta:
        model = LoadLineExtra
        fields = [
            "id", "line", "to_container", "to_container_no", "container_seal_no",
            "loaded_at", "loaded_by", "gross_weight_kg",
        ]
        read_only_fields = ["id"]


class DispatchLineExtraSerializer(serializers.ModelSerializer):
    class Meta:
        model = DispatchLineExtra
        fields = [
            "id", "line", "package_container", "package_lpn",
            "carrier_code", "waybill_no", "route_code",
            "piece_no", "piece_total", "handed_over_at", "handed_over_by",
        ]
        read_only_fields = ["id"]


class ReplenishLineExtraSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReplenishLineExtra
        fields = [
            "id", "line", "from_location", "to_location", "from_lpn", "to_lpn", "qty_move",
        ]
        read_only_fields = ["id"]


class RelocLineExtraSerializer(serializers.ModelSerializer):
    class Meta:
        model = RelocLineExtra
        fields = [
            "id", "line", "from_location", "to_location", "from_lpn", "to_lpn", "qty_move", "reason_code",
        ]
        read_only_fields = ["id"]


# 注册便于动态选择
TASK_EXTRA_MAP.update({
    "RECEIVE": ReceiveTaskExtraSerializer,
    "PUTAWAY": PutawayTaskExtraSerializer,
    "PICK": PickTaskExtraSerializer,
    "PACK": PackTaskExtraSerializer,
    "LOAD": LoadTaskExtraSerializer,
    "DISPATCH": DispatchTaskExtraSerializer,
    "REPLEN": ReplenishTaskExtraSerializer,
    "RELOC": RelocTaskExtraSerializer,
})

LINE_EXTRA_MAP.update({
    "RECEIVE": ReceiveLineExtraSerializer,
    "PUTAWAY": PutawayLineExtraSerializer,
    "PICK": PickLineExtraSerializer,
    "PACK": PackLineExtraSerializer,
    "LOAD": LoadLineExtraSerializer,
    "DISPATCH": DispatchLineExtraSerializer,
    "REPLEN": ReplenishLineExtraSerializer,
    "RELOC": RelocLineExtraSerializer,
})


# --------- Core line & task serializers ---------

class WmsTaskLineSerializer(serializers.ModelSerializer):
    """行：支持读取/写入 Extra。

    写入时可传：{"extra": {...}}，系统会依据所属任务的 task_type 写入对应 Extra 表。
    读取时：在 extra 字段中返回匹配类型的 Extra；其余类型不返回。
    """

    # Generic FK(只暴露基础 id，避免直接跨表展开)
    bound_content_type = serializers.PrimaryKeyRelatedField(
        queryset=ContentType.objects.all(), allow_null=True, required=False
    )
    bound_object_id = serializers.IntegerField(required=False, allow_null=True)

    # extra 容器
    extra = serializers.SerializerMethodField(read_only=True)
    extra_payload = serializers.DictField(write_only=True, required=False)

    class Meta:
        model = WmsTaskLine
        fields = [
            "id", "task", "product", "from_location", "to_location",
            "qty_plan", "qty_done", "remark",
            "src_model", "src_id", "rule_key", "plan_meta",
            "bound_content_type", "bound_object_id",
            # 扩展
            "extra", "extra_payload",
        ]
        read_only_fields = ["id", "extra"]

    # ---- read ----
    def get_extra(self, obj: WmsTaskLine) -> Optional[Dict[str, Any]]:
        ttype = getattr(obj.task, "task_type", None)
        ser_cls = LINE_EXTRA_MAP.get((ttype or "").upper())
        if not ser_cls:
            return None
        # related_name 使用类名，需通过 OneToOne 反向拿到实例
        rel_name = ser_cls.Meta.model.__name__  # e.g. ReceiveLineExtra
        extra_obj = getattr(obj, rel_name, None)
        if not extra_obj:
            return None
        return ser_cls(extra_obj).data

    # ---- write ----
    def create(self, validated_data: Dict[str, Any]) -> WmsTaskLine:
        extra_payload = validated_data.pop("extra_payload", None)
        line = super().create(validated_data)
        self._upsert_line_extra(line, extra_payload)
        return line

    def update(self, instance: WmsTaskLine, validated_data: Dict[str, Any]) -> WmsTaskLine:
        extra_payload = validated_data.pop("extra_payload", None)
        line = super().update(instance, validated_data)
        if extra_payload is not None:
            self._upsert_line_extra(line, extra_payload)
        return line

    def _upsert_line_extra(self, line: WmsTaskLine, payload: Optional[Dict[str, Any]]):
        if not payload:
            return
        ttype = getattr(line.task, "task_type", None)
        model_cls = line_extra_model(ttype)
        ser_cls = LINE_EXTRA_MAP.get((ttype or "").upper())
        if not (model_cls and ser_cls):
            raise serializers.ValidationError({"extra_payload": f"不支持的行扩展类型: {ttype}"})

        # OneToOne 存在则更新，不存在则创建
        rel_name = model_cls.__name__
        existing = getattr(line, rel_name, None)
        data = {"line": line.pk, **payload}
        if existing:
            ser = ser_cls(existing, data=data, partial=True, context=self.context)
        else:
            ser = ser_cls(data=data, context=self.context)
        ser.is_valid(raise_exception=True)
        ser.save()


class WmsTaskSerializer(serializers.ModelSerializer, ChoiceDisplayMixin):
    """任务头：可选嵌套行与头部 Extra。"""

    # display 字段
    task_type_display = serializers.SerializerMethodField()
    status_display = serializers.SerializerMethodField()
    priority_display = serializers.SerializerMethodField()

    # 嵌套：行
    lines = WmsTaskLineSerializer(many=True, read_only=True)

    # 头部 Extra（读）
    extra = serializers.SerializerMethodField(read_only=True)

    # 头部 Extra（写）
    extra_payload = serializers.DictField(write_only=True, required=False)

    class Meta:
        model = WmsTask
        fields = [
            "id", "owner", "warehouse",
            "task_group_no", "released_at",
            "task_no", "task_type", "status", "priority",
            "planned_start", "planned_end", "started_at", "finished_at",
            "ref_no", "source_app", "source_model", "source_pk",
            "remark", "created_by", "updated_by", "created_at", "updated_at",
            # display
            "task_type_display", "status_display", "priority_display",
            # 嵌套
            "lines",
            # Extra
            "extra", "extra_payload",
        ]
        read_only_fields = ["id", "created_at", "updated_at", "task_type_display", "status_display", "priority_display", "extra", "lines"]

    # ---- display helpers ----
    def get_task_type_display(self, obj):
        return self._display_field(obj, "task_type")

    def get_status_display(self, obj):
        return self._display_field(obj, "status")

    def get_priority_display(self, obj):
        return self._display_field(obj, "priority")

    # ---- Extra (read) ----
    def get_extra(self, obj: WmsTask) -> Optional[Dict[str, Any]]:
        ttype = getattr(obj, "task_type", None)
        ser_cls = TASK_EXTRA_MAP.get((ttype or "").upper())
        if not ser_cls:
            return None
        # related_name 即模型类名，如 ReceiveTaskExtra
        rel_name = ser_cls.Meta.model.__name__
        extra_obj = getattr(obj, rel_name, None)
        if not extra_obj:
            return None
        return ser_cls(extra_obj).data

    # ---- write: create/update Extra（简单 upsert）----
    def _upsert_task_extra(self, task: WmsTask, payload: Optional[Dict[str, Any]]):
        if not payload:
            return
        ttype = getattr(task, "task_type", None)
        model_cls = task_extra_model(ttype)
        ser_cls = TASK_EXTRA_MAP.get((ttype or "").upper())
        if not (model_cls and ser_cls):
            raise serializers.ValidationError({"extra_payload": f"不支持的任务扩展类型: {ttype}"})

        rel_name = model_cls.__name__
        existing = getattr(task, rel_name, None)
        data = {"task": task.pk, **payload}
        if existing:
            ser = ser_cls(existing, data=data, partial=True, context=self.context)
        else:
            ser = ser_cls(data=data, context=self.context)
        ser.is_valid(raise_exception=True)
        ser.save()

    @transaction.atomic
    def create(self, validated_data: Dict[str, Any]) -> WmsTask:
        extra_payload = validated_data.pop("extra_payload", None)
        task = super().create(validated_data)
        self._upsert_task_extra(task, extra_payload)
        return task

    @transaction.atomic
    def update(self, instance: WmsTask, validated_data: Dict[str, Any]) -> WmsTask:
        extra_payload = validated_data.pop("extra_payload", None)
        task = super().update(instance, validated_data)
        if extra_payload is not None:
            self._upsert_task_extra(task, extra_payload)
        return task


# --------- Convenience list/detail serializers ---------

class WmsTaskBriefSerializer(serializers.ModelSerializer, ChoiceDisplayMixin):
    task_type_display = serializers.SerializerMethodField()
    status_display = serializers.SerializerMethodField()
    priority_display = serializers.SerializerMethodField()

    class Meta:
        model = WmsTask
        fields = [
            "id", "task_no", "task_type", "task_type_display", "status", "status_display",
            "priority", "priority_display", "owner", "warehouse", "planned_start", "planned_end",
            "created_at", "updated_at",
        ]
        read_only_fields = fields

    def get_task_type_display(self, obj):
        return self._display_field(obj, "task_type")

    def get_status_display(self, obj):
        return self._display_field(obj, "status")

    def get_priority_display(self, obj):
        return self._display_field(obj, "priority")


class WmsTaskDetailSerializer(WmsTaskSerializer):
    """Detail = Task + Lines + Head Extra (只读)。"""
    lines = WmsTaskLineSerializer(many=True, read_only=True)

    class Meta(WmsTaskSerializer.Meta):
        read_only_fields = list(set(WmsTaskSerializer.Meta.read_only_fields + ["extra_payload"]))


# --------- Collections for secondary entities ---------

class TaskAssignmentBriefSerializer(TaskAssignmentSerializer):
    class Meta(TaskAssignmentSerializer.Meta):
        read_only_fields = TaskAssignmentSerializer.Meta.read_only_fields


class TaskStatusLogBriefSerializer(TaskStatusLogSerializer):
    class Meta(TaskStatusLogSerializer.Meta):
        read_only_fields = TaskStatusLogSerializer.Meta.read_only_fields


class TaskScanLogBriefSerializer(TaskScanLogSerializer):
    class Meta(TaskScanLogSerializer.Meta):
        read_only_fields = TaskScanLogSerializer.Meta.read_only_fields


__all__ = [
    # Core
    "WmsTaskSerializer", "WmsTaskBriefSerializer", "WmsTaskDetailSerializer",
    "WmsTaskLineSerializer",
    # Secondary
    "TaskAssignmentSerializer", "TaskAssignmentBriefSerializer",
    "TaskStatusLogSerializer", "TaskStatusLogBriefSerializer",
    "TaskScanLogSerializer", "TaskScanLogBriefSerializer",
    # Extra
    "ReceiveTaskExtraSerializer", "PutawayTaskExtraSerializer", "PickTaskExtraSerializer",
    "PackTaskExtraSerializer", "LoadTaskExtraSerializer", "DispatchTaskExtraSerializer",
    "ReplenishTaskExtraSerializer", "RelocTaskExtraSerializer",
    "ReceiveLineExtraSerializer", "PutawayLineExtraSerializer", "PickLineExtraSerializer",
    "PackLineExtraSerializer", "LoadLineExtraSerializer", "DispatchLineExtraSerializer",
    "ReplenishLineExtraSerializer", "RelocLineExtraSerializer",
]


class ReceiveWithoutOrderSerializer(serializers.Serializer):
    product_id = serializers.IntegerField(required=True)
    qty = serializers.DecimalField(max_digits=18, decimal_places=4, required=True)

    def validate_qty(self, value):
        if value <= 0:
            raise serializers.ValidationError("接收数量必须大于零")
        return value
