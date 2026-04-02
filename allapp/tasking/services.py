# allapp/tasking/services.py
from __future__ import annotations
from allapp.locations.models import Location
from allapp.products.models import Product
import logging
from django.apps import apps
from django.db.models import OneToOneRel
from allapp.tasking import services
logger = logging.getLogger(__name__)

from datetime import datetime
from allapp.tasking.models import DocSequence
from allapp.inventory.models import InventoryDetail
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, Optional, Union, Tuple
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.utils.module_loading import import_string
from django.conf import settings
import hashlib
import json
from django.core.exceptions import ValidationError, PermissionDenied
from allapp.tasking.models import WmsTask, WmsTaskLine, ReceiveLineExtra, PutawayLineExtra, TaskAssignment, TaskScanLog, \
    RelocLineExtra, CountLineExtra, PickLineExtra, ReplenishLineExtra, DispatchLineExtra

from django.db import transaction
from django.db.models import F
from rest_framework.exceptions import ValidationError
from django.utils import timezone
from django.db import transaction, IntegrityError

@transaction.atomic
def adjust_pick_line_qty(
    task_id: int,
    line_id: int,
    final_qty: Decimal | float | str,
    by_user=None,
    client_seq: str | None = None,
) -> dict:
    user_id = getattr(by_user, "id", None)

    # 1) 锁任务，校验类型 & 状态
    task = (
        WmsTask.objects
        .select_for_update()
        .get(id=task_id, task_type=WmsTask.TaskType.PICK)
    )
    if task.status in ("CANCELLED", "COMPLETED"):
        raise ValidationError("任务当前状态不允许调整拣货数。")
    if task.status not in ("RELEASED", "IN_PROGRESS", "RESERVED"):
        raise ValidationError(f"状态 {task.status} 不允许调整拣货数。")

    # 2) 锁这一行
    line = (
        WmsTaskLine.objects
        .select_for_update()
        .get(id=line_id, task_id=task.id)
    )

    final_qty_dec = _q3(final_qty)
    cur_qty = _q3(line.qty_done or 0)
    diff = final_qty_dec - cur_qty

    # 没变化就直接返回
    if diff == 0:
        return {
            "idempotent": True,
            "line_id": line.id,
            "qty_done": line.qty_done,
        }

    # 3) 超量控制（沿用你 _allow_overdone 规则）
    if not _allow_overdone(task.task_type):
        plan = _q3(line.qty_plan or 0)
        if final_qty_dec > plan:
            raise ValidationError(
                f"{task.task_type} 不允许超量：目标 {final_qty_dec} > 计划 {plan}"
            )

    # 4) 确定库位（跟 scan_task 保持一致优先级）
    # loc_id = (
    #     getattr(line, "from_location_id", None)
    #     or getattr(line, "to_location_id", None)
    # )

    loc_id = line.from_location_id

    # 5) 生成一个 fp，用 client_seq 做幂等（可复用 _compute_fp）
    fp = _compute_fp(
        task.id,
        f"MANUAL-{line.id}",  # 这里不是真条码，用一个稳定前缀即可
        diff,
        loc_id,
        user_id,
        client_seq,
    )

    # if TaskScanLog.objects.filter(task_id=task.id, fp=fp).exists():
    #     return {"idempotent": True, "line_id": line.id, "qty_done": line.qty_done}

    # ✅ 硬幂等：先插入 TaskScanLog（依赖 ux_tscan_fp 唯一约束）
    # 插入成功才继续做快照/改 qty_done；插入失败说明重复请求/重试
    try:
        scan = TaskScanLog.objects.create(
            scan_snapshot_rev=0,  # 先占位，保存快照后再回填
            owner_id=task.owner_id,
            warehouse_id=task.warehouse_id,
            task_id=task.id,
            task_line_id=line.id,
            product_id=line.product_id,
            location_id=loc_id,
            method="MANUAL",
            source="PDA",
            by_user_id=user_id,
            barcode="",            # 手工调整无条码
            label_key=None,
            code_type=None,
            uom_code=None,
            pack_qty=None,
            qty_aux=None,
            qty_base=None,
            qty_base_delta=diff,   # 关键：调整量（可正可负）
            lot_no=None,
            mfg_date=None,
            exp_date=None,
            container_no=None,
            fp=fp,
            reason_code="MANUAL_ADJUST",
            remark="PDA 手工调整拣货数量",
        )
    except IntegrityError:
        # fp 重复：同一次请求重试/连点 -> 幂等返回（不重复生效）
        if TaskScanLog.objects.filter(fp=fp).exists():
            line.refresh_from_db(fields=["qty_done"])
            return {"idempotent": True, "line_id": line.id, "qty_done": line.qty_done}
        raise


    # 6) 准备快照 items（和 scan_task 保持 style）
    p = Product.objects.only("id").get(id=line.product_id)

    # loc_obj = (
    #     getattr(line, "from_location", None)
    #     or getattr(line, "to_location", None)
    # )

    # 6) 准备快照 items（方向A：拣货一律使用 from_location）
    if not line.from_location_id:
        raise ValidationError("拣货行缺少 from_location_id，无法调整拣货数。")

    # line.from_location 未 select_related 也没关系，会自动懒加载
    loc_obj = getattr(line, "from_location", None)
    if loc_obj is None:
        # 保险兜底：按 id 再取一次
        loc_obj = Location.objects.only("id", "code").get(id=line.from_location_id)

    snap_items = [{
        "product": p,
        "location": loc_obj,
        "qty_ok": diff,             # 调整量
        "qty_base": float(diff),
        "qty": float(diff),
        "lot_no": None,
        "mfg_date": None,
        "exp_date": None,
        "uom_code": None,
        "pack_qty": None,
    }]

    rev = save_receiving_snapshot(
        task_line_id=line.id,
        items=snap_items,
        operator=by_user,
        source="PDA",
    )

    # 7) 写 TaskScanLog（method 用已有字段，不新增列）
    print("126 TaskScanLog.objects.create")

    #
    # scan = TaskScanLog.objects.create(
    #     scan_snapshot_rev=rev,
    #     owner_id=task.owner_id,
    #     task_id=task.id,
    #     task_line_id=line.id,
    #     product_id=line.product_id,
    #     location_id=loc_id,
    #     method="MANUAL",      # ✅ 新的枚举值，不是新字段
    #     source="PDA",
    #     by_user_id=user_id,
    #     barcode="",           # 无条码
    #     label_key=None,
    #     code_type=None,
    #     uom_code=None,
    #     pack_qty=None,
    #     qty_base_delta=diff,  # 关键：调整量，可以是正/负
    #     qty_base=None,
    #     lot_no=None,
    #     mfg_date=None,
    #     exp_date=None,
    #     fp=fp,
    #     reason_code="MANUAL_ADJUST",  # 用现有 reason_code 字段
    #     remark="PDA 手工调整拣货数量",
    # )

    # 7) 回填快照版本号（scan 在上面已创建，用于幂等）
    TaskScanLog.objects.filter(pk=scan.pk).update(scan_snapshot_rev=rev)


    # 8) 更新行上的快照数量
    line.qty_done = final_qty_dec
    line.save(update_fields=["qty_done"])

    return {
        "idempotent": False,
        "line_id": line.id,
        "qty_done": line.qty_done,
        # "scan_id": scan.id,
    }


def build_scan_fp(*, task_id, line_id, product_id, location_id, lot, expiry, serial, qty, rev) -> str:
    """
    生成稳定、长度固定的 fp（64 个十六进制字符）。
    - 先把关键字段做成规范化 JSON（键名排序、None→null）
    - 再 sha256.hexdigest()
    """
    payload = {
        "task_id": task_id,
        "line_id": line_id,
        "product_id": product_id,
        "location_id": location_id,
        "lot": lot or "",
        "expiry": "" if expiry in (None, "") else str(expiry),
        "serial": serial or "",
        "qty": str(qty),
        "rev": int(rev),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

@transaction.atomic
def _run_posting_handler(task_id: int, by_user=None, note: str = "过账"):
    task = WmsTask.objects.select_for_update().get(id=task_id)
    scans = list(TaskScanLog.objects.filter(task_id=task.id).order_by("id"))
    # 支持 settings 配字符串或可调用
    print("188  _run_posting_handler scans=",scans)
    handler_cfg = getattr(settings, "TASKING_POSTING_HANDLER", "allapp.tasking.plugins.handlers.DefaultPostingHandler")
    handler = import_string(handler_cfg)() if isinstance(handler_cfg, str) else handler_cfg()
    created = handler.handle(task=task, scans=scans, now=None, batch_no=None, note=note or "")
    return {"ok": True, "tx_created": created}

def _resolver():
    path = getattr(settings, "TASKING_BARCODE_RESOLVER", None)
    if not path:
        raise ImproperlyConfigured("缺少 settings.TASKING_BARCODE_RESOLVER")
    return import_string(path)

def _q3(x) -> Decimal:
    return Decimal(str(x)).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)

def _compute_fp(task_id: int, barcode: str, inc_qty: Decimal,
                loc_for_fp: Optional[int], user_id: Optional[int], client_seq: Optional[str]) -> str:
    s = "|".join([
        str(task_id), barcode or "", f"{_q3(inc_qty):.3f}",
        str(loc_for_fp or ""), str(user_id or ""), str(client_seq or ""),
    ])
    return hashlib.sha1(s.encode("utf-8")).hexdigest()

def _allow_overdone(task_type: str) -> bool:
    default = {
        "RECEIVE": True, "PUTAWAY": True, "RELOC": True, "COUNT": True,
        "PICK": False, "PACK": False, "DISPATCH": False, "LOAD": False,
    }
    cfg = getattr(settings, "TASKING_ALLOW_OVERDONE", None) or default
    return bool(cfg.get(task_type, False))

@transaction.atomic
def scan_task(
    task_id: int,
    barcode: str,
    qty: Decimal | float | str,
    location_id: Optional[int] = None,
    by_user=None,
    client_seq: Optional[str] = None,
) -> Dict[str, Any]:
    user_id = getattr(by_user, "id", None)

    # 1) 锁任务并校验状态
    task = WmsTask.objects.select_for_update().get(id=task_id)
    if task.status in ("CANCELLED", "COMPLETED"):
        raise ValidationError("任务当前状态不允许扫码。")
    if task.status not in ("RELEASED", "IN_PROGRESS","RESERVED"):
        raise ValidationError(f"状态 {task.status} 不允许扫码。")

    # 2) 解析条码
    resolve = _resolver()
    r: Dict[str, Any] = resolve(task.owner_id, barcode) or {}

    # —— 新增：把解析结果归一为 dict（兼容 SimpleNamespace / 对象 / pydantic）
    if not isinstance(r, dict):
        if hasattr(r, "dict") and callable(getattr(r, "dict", None)):  # pydantic/BaseModel 等
            r = r.dict()
        elif hasattr(r, "__dict__"):  # SimpleNamespace/普通对象
            r = vars(r)
        else:
           raise ValidationError(f"条码解析器返回不受支持的类型：{type(r).__name__}，期望 dict。")


    product_id = r.get("product_id")
    if not product_id:
        raise ValidationError("条码解析未识别商品。")
    pack_qty = r.get("pack_qty") or 1
    raw_label_key = (r.get("label_key") or "").strip() or None
    label_key = raw_label_key
    code_type = r.get("code_type")
    uom_code = r.get("uom_code")
    lot_no = r.get("lot_no")
    mfg_date = r.get("mfg_date")
    exp_date = r.get("exp_date")

    # —— 规则：只对“一箱一码 / 一件一码”的码使用 label_key —— #
    #   普通商品码（SKU/GTIN/ITEM/RAW）不占用 label_key，这样同一任务内可以多次扫码
    #   留给 LPN / SSCC / SN / 容器号等码使用 label_key 做“同任务唯一”控制
    if code_type in {"SKU", "GTIN", "ITEM", "RAW"}:
        label_key = None
    else:
        label_key = raw_label_key

    # 3) 锁行（RECEIVE/COUNT 可自动建行）
    line = (WmsTaskLine.objects.select_for_update()
            .filter(task_id=task.id, product_id=product_id)
            .order_by("id").first())
    if not line and task.task_type in ("RECEIVE", "COUNT"):
        line = WmsTaskLine.objects.create(task_id=task.id, product_id=product_id,
                                          qty_plan=Decimal("0"), qty_done=Decimal("0"))
    if not line:
        raise ValidationError("未找到匹配任务行（非 RECEIVE/COUNT 不自动建行）。")

    # 4) 计算增量与 FP
    qty = _q3(qty)
    if task.task_type == "RECEIVE":
        inc_qty = _q3(qty * Decimal(str(pack_qty)))
        loc_for_fp = location_id
    elif task.task_type == "COUNT":
        inc_qty = _q3(qty)  # 实盘总数
        loc_for_fp = location_id
    else:
        inc_qty = _q3(qty)
        loc_for_fp = location_id if task.task_type != "PUTAWAY" else (location_id or getattr(line, "to_location_id", None))

    # ---- 关键：统一出库扣减库位 ----
    task_type_u = (task.task_type or "").upper()

    # ISSUE/拣货类：必须从预留库位(from_location)扣
    if task_type_u in ("PICK", "DISPATCH", "LOAD", "SHIP"):
        if not getattr(line, "from_location_id", None):
            raise ValidationError("拣货行缺少 from_location，无法出库扣减")
        effective_location_id = line.from_location_id

        # 如果接口入参里有 location_id（有的实现会传），则强校验一致
        if location_id and int(location_id) != int(effective_location_id):
            raise ValidationError("扫描库位与预留库位不一致，请按任务行预留库位拣货")

    else:
        # 非 ISSUE：维持你原来的逻辑（这里给一个更安全的优先级示例）
        # 收货/上架/移库通常更关心 to_location
        effective_location_id = (
                location_id
                or getattr(line, "to_location_id", None)
                or getattr(line, "from_location_id", None)
        )

    if not effective_location_id:
        raise ValidationError("无法确定本次扫描的 location_id")

    # fp = _compute_fp(task.id, barcode, inc_qty, loc_for_fp, user_id, client_seq)
    fp = _compute_fp(task.id, barcode, inc_qty, effective_location_id, user_id, client_seq)

    # # FP 幂等
    # if TaskScanLog.objects.filter(task_id=task.id, fp=fp).exists():
    #     return {"idempotent": True, "line_id": line.id, "qty_done": line.qty_done}
    #
    # # 同任务 label_key 唯一（应用层兜底）
    # if label_key and TaskScanLog.objects.filter(task_id=task.id, label_key=label_key).exists():
    #     raise ValidationError("同一任务内 label_key 已存在。")

    # ✅ 幂等：先插入 TaskScanLog（依赖 ux_tscan_fp / ux_tscan_task_label 硬约束）
    # 插入成功才继续更新 qty_done；插入失败说明重复请求/重复 label_key
    try:
        scan = TaskScanLog.objects.create(
            scan_snapshot_rev=0,  # 先占位，后面保存快照后再回填
            owner_id=task.owner_id,
            warehouse_id=task.warehouse_id,
            task_id=task.id,
            task_line_id=line.id,
            product_id=product_id,
            # location_id=location_id
            #             or getattr(line, "to_location_id", None)
            #             or getattr(line, "from_location_id", None),
            location_id=effective_location_id,
            method="SCAN",
            source="PDA",
            by_user_id=user_id,
            barcode=barcode,
            label_key=label_key,
            code_type=code_type,
            uom_code=uom_code,
            pack_qty=pack_qty,
            qty_base_delta=inc_qty,
            qty_base=(inc_qty if task.task_type == "COUNT" else None),
            lot_no=lot_no,
            mfg_date=mfg_date,
            exp_date=exp_date,
            fp=fp,
        )
    except IntegrityError:
        # fp 重复：同一次请求重试/连点 -> 直接幂等返回（不重复累加）
        if TaskScanLog.objects.filter(fp=fp).exists():
            line.refresh_from_db(fields=["qty_done"])
            return {"idempotent": True, "line_id": line.id, "qty_done": line.qty_done}

        # label_key 冲突：同任务内重复的箱标/序列键
        if label_key and TaskScanLog.objects.filter(task_id=task.id, label_key=label_key).exists():
            raise ValidationError("同一任务内 label_key 已存在。")
        raise


    # 5) 累计/覆盖到行
    if task.task_type == "COUNT":
        WmsTaskLine.objects.filter(id=line.id).update(qty_done=inc_qty)
        line.refresh_from_db(fields=["qty_done"])
    else:
        WmsTaskLine.objects.filter(id=line.id).update(qty_done=F("qty_done") + inc_qty)
        line.refresh_from_db(fields=["qty_done"])

    # 超量策略
    if not _allow_overdone(task.task_type):
        plan = getattr(line, "qty_plan", Decimal("0"))
        if line.qty_done > plan:
            raise ValidationError(f"{task.task_type} 不允许超量：{line.qty_done} > 计划 {plan}")

    # （在循环前）先生成收货快照版本号

    # rev = save_receiving_snapshot(task_line_id=line.id, by_user=by_user)

    p = Product.objects.only("id").get(id=product_id)

    # 选一个库位：
    # - 优先用传入的 location_id
    # - 否则优先用行上的 from_location，再用 to_location
    loc_obj = None
    if location_id:
        try:
            loc_obj = Location.objects.only("id").get(id=location_id)
        except Location.DoesNotExist:
            loc_obj = getattr(line, "from_location", None) or getattr(line, "to_location", None)
    else:
        loc_obj = getattr(line, "from_location", None) or getattr(line, "to_location", None)


    snap_items = [{
        "product": p,  # 用 'product' 键，传实例最稳
        "location": loc_obj,
        "qty_ok": inc_qty,  # ★ 关键：save_receiving_snapshot 只看 qty_ok
        "qty_base": float(inc_qty),  # 用 float，避免在快照内部被过滤
        "qty": float(inc_qty),  # 兼容只认 qty 的实现
        "lot_no": lot_no,
        "mfg_date": mfg_date,
        "exp_date": exp_date,
        "uom_code": uom_code,
        "pack_qty": float(pack_qty) if pack_qty else None,
    }]

    rev = save_receiving_snapshot(
        task_line_id=line.id,
        items=snap_items,
        operator=by_user,  # 传用户对象
        source="PDA",
    )

    # 6) 回填当前扫描对应的快照版本号
    TaskScanLog.objects.filter(pk=scan.pk).update(scan_snapshot_rev=rev)
    scan.scan_snapshot_rev = rev

    return {"idempotent": False, "line_id": line.id, "qty_done": line.qty_done, "scan_id": scan.id}

def claim_task(task, *, by_user, allowed_wh_ids: set[int], to_status: str | None = None):
    """
    抢单：为 task 创建/激活一条未完成的 TaskAssignment（同一个任务同一时刻仅允许一个“未完成”指派）。
    - 仅允许在用户所属仓库范围内
    - 仅允许任务处于 RELEASED（可按需放宽）
    - 并发安全：行锁 + 存量检查
    """
    from allapp.tasking.models import TaskAssignment, WmsTask  # 避免循环依赖

    if task.warehouse_id not in allowed_wh_ids:
        raise ValidationError("非本人所属仓库，不能认领。")
    if task.status != WmsTask.Status.RELEASED:
        raise ValidationError(f"状态为 {task.status}，仅 RELEASED 可认领。")

    with transaction.atomic():
        # 锁任务行，避免并发
        locked = (type(task).objects.select_for_update().only("id", "status")
                  .get(pk=task.pk))
        # 再次确认状态
        if locked.status != WmsTask.Status.RELEASED:
            raise ValidationError("该任务已被他人操作。")

        # 是否已有“未完成”的指派（任何人）
        has_active = (TaskAssignment.objects
                      .select_for_update()
                      .filter(task=task, finished_at__isnull=True)
                      .exists())
        if has_active:
            raise ValidationError("任务已被他人认领。")

        # 为本用户拿/建一条记录并激活
        ta, _ = TaskAssignment.objects.get_or_create(task=task, assignee=by_user)
        if ta.finished_at is not None:
            ta.finished_at = None
        if ta.accepted_at is None:
            ta.accepted_at = timezone.now()
        ta.save(update_fields=["accepted_at", "finished_at"])

        # 认领后的目标状态：ASSIGNED（如无需变更，可置 None）
        if to_status:
            task._allow_status_write = True
            task.status = to_status
            task.save(update_fields=["status"])

    return ta


def unclaim_task(task, *, by_user, allowed_wh_ids: set[int], back_to_status: str | None = None):
    """
    放回池子：把当前用户的“未完成”指派标记为完成（finished_at），可将任务状态回滚到 RELEASED。
    """
    from allapp.tasking.models import TaskAssignment, WmsTask

    if task.warehouse_id not in allowed_wh_ids:
        raise ValidationError("非本人所属仓库，不能放回。")

    with transaction.atomic():
        (type(task).objects.select_for_update().only("id").get(pk=task.pk))
        ta = (TaskAssignment.objects.select_for_update()
              .filter(task=task, assignee=by_user, finished_at__isnull=True)
              .first())
        if not ta:
            raise ValidationError("你没有未完成的指派，无法放回。")

        ta.finished_at = timezone.now()
        ta.save(update_fields=["finished_at"])

        if back_to_status:
            task._allow_status_write = True
            task.status = back_to_status
            task.save(update_fields=["status"])

    return ta



def _assert_can_release(t: WmsTask) -> None:
    """发布前基础校验：状态 + 行项"""
    # 允许从 DRAFT/READY 发布；若你的枚举不同，自行扩展
    ok_status = {getattr(WmsTask.Status, "DRAFT", "DRAFT"),
                 getattr(WmsTask.Status, "READY", "READY"),
                 getattr(WmsTask.Status, "RESERVED", "RESERVED"),
                 }
    if t.status not in ok_status:
        raise ValidationError(f"当前状态为 {t.status}，仅 DRAFT/READY 可发布。")
    if not t.lines.exists():
        raise ValidationError("任务无明细，不能发布。")
    if not t.lines.filter(qty_plan__gt=0).exists():
        raise ValidationError("所有任务行计划数量为 0，不能发布。")


def _finish_other_head_assignments(task: WmsTask, keep_assignee) -> int:
    """结束除 keep_assignee 外的头级活动指派"""
    return (TaskAssignment.objects
            .select_for_update()
            .filter(task=task, line__isnull=True, finished_at__isnull=True)
            .exclude(assignee=keep_assignee)
            .update(finished_at=timezone.now()))


def _finish_active_line_assignment(line: WmsTaskLine, exclude_assignee=None) -> int:
    """结束该行的活动指派；可保留 exclude_assignee"""
    qs = (TaskAssignment.objects
          .select_for_update()
          .filter(line=line, finished_at__isnull=True))
    if exclude_assignee is not None:
        qs = qs.exclude(assignee=exclude_assignee)
    return qs.update(finished_at=timezone.now())


def _activate_head_assignment(task: WmsTask, assignee) -> TaskAssignment:
    """创建/激活 1 条头级活动指派（唯一）"""
    now = timezone.now()
    ta, _ = TaskAssignment.objects.get_or_create(task=task, line=None, assignee=assignee)
    fields = []
    if ta.accepted_at is None:
        ta.accepted_at = now
        fields.append("accepted_at")
    if ta.finished_at is not None:
        ta.finished_at = None
        fields.append("finished_at")
    if fields:
        ta.save(update_fields=fields)
    return ta


def _activate_line_assignment(line: WmsTaskLine, assignee) -> TaskAssignment:
    """创建/激活 1 条行级活动指派（每行唯一）"""
    now = timezone.now()
    ta, _ = TaskAssignment.objects.get_or_create(task=line.task, line=line, assignee=assignee)
    fields = []
    if ta.accepted_at is None:
        ta.accepted_at = now
        fields.append("accepted_at")
    if ta.finished_at is not None:
        ta.finished_at = None
        fields.append("finished_at")
    if fields:
        ta.save(update_fields=fields)
    return ta


def publish_task(
    task: WmsTask,
    *,
    head_assignee=None,                         # 头级指派给谁；None 表示不做头级指派
    line_map: Optional[Dict[Union[int, WmsTaskLine], object]] = None,  # 行→人 的映射；key 可是行对象或行ID
    seed_lines: bool = True,                    # 若有头级指派：是否把未指派的行复制为行级指派（“头级兜底 → 行级落地”）
    overwrite: bool = False,                    # seed_lines=True 时，是否覆盖所有行（True=所有行都指给头；False=只补“无人”的行）
    pool_status=None,                           # 默认用 WmsTask.Status.RELEASED
    assigned_status=None                        # 默认用 WmsTask.Status.ASSIGNED（若无则回退到 RELEASED）
) -> dict:
    """
    发布任务，并按需创建/激活 TaskAssignment。

    对应 4 种情形：
    1) 无任何指派参数 -> 发布为抢单（所有行进入抢单池）
    2) 仅 head_assignee=a1 -> 全部行默认归 a1（可 seed 到行）
    3) head_assignee=a1 + line_map 指定若干行给他人 -> 这些行归他人，其余归 a1
    4) 无头级、仅 line_map -> 指定的行归对应人员，其余行进抢单池

    返回字典包含统计信息，便于 Admin 提示。
    """
    pool_status = pool_status or getattr(WmsTask.Status, "RELEASED", "RELEASED")
    assigned_status = assigned_status or getattr(WmsTask.Status, "ASSIGNED", pool_status)

    now = timezone.now()
    stats = {
        "ended_head": 0,
        "ended_lines": 0,
        "created_head": 0,
        "activated_lines": 0,
        "seeded_lines": 0,
        "to_pool_lines": 0,
        "status": None,
    }

    with transaction.atomic():
        # 锁任务，防并发
        t = (WmsTask.objects
             .select_for_update()
             .get(pk=task.pk))
        _assert_can_release(t)

        # 规范化 line_map：行对象/ID 都接受
        norm_map: Dict[int, object] = {}
        if line_map:
            for k, v in line_map.items():
                line_id = k.pk if isinstance(k, WmsTaskLine) else int(k)
                norm_map[line_id] = v

        # 情形 1：既无头级也无行级指派 -> 发布为抢单
        if head_assignee is None and not norm_map:
            # 结束所有活动指派（幂等兜底）
            stats["ended_head"] += (TaskAssignment.objects
                                    .select_for_update()
                                    .filter(task=t, finished_at__isnull=True)
                                    .update(finished_at=now))
            # 状态：RELEASED
            t._allow_status_write = True
            t.status = pool_status
            if hasattr(t, "released_at") and not t.released_at:
                t.released_at = now
            t.save(update_fields=["status"] + (["released_at"] if hasattr(t, "released_at") else []))

            # 统计：进入抢单池的行（= 全部未被指派行）
            stats["to_pool_lines"] = t.lines.count()
            stats["status"] = t.status
            return stats

        # —— 以下是“存在某种指派”的分支 —— #
        # 处理头级指派
        if head_assignee is not None:
            stats["ended_head"] += _finish_other_head_assignments(t, keep_assignee=head_assignee)
            _activate_head_assignment(t, head_assignee)
            stats["created_head"] = 1

        # 处理行级指派（明确指定的行）
        if norm_map:
            # 一次性把涉及的行都锁住
            line_ids = list(norm_map.keys())
            line_qs = (WmsTaskLine.objects
                       .select_for_update()
                       .filter(task=t, id__in=line_ids)
                       .only("id", "task_id"))
            found = set()
            for line in line_qs:
                found.add(line.id)
                assignee = norm_map[line.id]
                # 覆盖该行其它活动指派
                stats["ended_lines"] += _finish_active_line_assignment(line, exclude_assignee=assignee)
                _activate_line_assignment(line, assignee)
                stats["activated_lines"] += 1

            # 有人传入了非本任务的行ID
            not_found = set(line_ids) - found
            if not_found:
                raise ValidationError(f"发现非本任务的行ID：{sorted(not_found)[:5]} ...")

        # seed：把未被任何人行级指派的行，复制给头级负责人
        if head_assignee is not None and seed_lines:
            # 选择要 seed 的行集合
            seed_qs = t.lines.select_for_update().only("id", "qty_plan")

            if not overwrite:
                # 只补“目前没有活动行级指派”的行
                seed_qs = seed_qs.exclude(assignments__finished_at__isnull=True)
            else:
                # 覆盖：先结束所有行的活动指派
                stats["ended_lines"] += (TaskAssignment.objects
                                         .select_for_update()
                                         .filter(task=t, line__isnull=False, finished_at__isnull=True)
                                         .update(finished_at=now))
            # 实施 seed（跳过 plan=0 的行）
            for line in seed_qs:
                # （覆盖模式）或（补空模式下，本行确实没人）
                _activate_line_assignment(line, head_assignee)
                stats["seeded_lines"] += 1

        # 任务状态：存在任意（头/行）活动指派 → 视为 ASSIGNED；否则 → RELEASED
        has_any_active = TaskAssignment.objects.filter(task=t, finished_at__isnull=True).exists()
        t._allow_status_write = True
        t.status = assigned_status if has_any_active else pool_status
        if hasattr(t, "released_at") and not t.released_at:
            t.released_at = now
        t.save(update_fields=["status"] + (["released_at"] if hasattr(t, "released_at") else []))

        # 统计“进入抢单池”的行数（仅当最终无头级，且存在未被行级指派行）
        if t.status == pool_status:
            pool_cnt = (t.lines
                        .exclude(assignments__finished_at__isnull=True)  # 无活动行级指派
                        .count())
            stats["to_pool_lines"] = pool_cnt

        stats["status"] = t.status
        return stats


def publish_using_inline(
    task: WmsTask,
    *,
    seed_lines: bool = True,
    overwrite: bool = False,
    pool_status=None,
    assigned_status=None,
) -> dict:
    """
    读取当前任务下 Admin Inline 已经录入的活动指派（finished_at IS NULL），
    自动推导 head_assignee 与 line_map，然后委托 publish_task。
    """
    with transaction.atomic():
        t = (WmsTask.objects
             .select_for_update()
             .get(pk=task.pk))

        active = list(TaskAssignment.objects
                      .select_for_update()
                      .filter(task=t, finished_at__isnull=True)
                      .select_related("assignee", "line"))

        # 头级（line is NULL）
        head = [a for a in active if a.line_id is None]
        if len(head) > 1:
            raise ValidationError("存在多条'头级活动指派'，请在发布前保留一条或清理冲突。")
        head_assignee = head[0].assignee if head else None

        # 行级（line 不为空）
        line_map: Dict[int, object] = {}
        for a in active:
            if a.line_id:
                line_map[a.line_id] = a.assignee

    # 交给 publish_task 统一处理（含 seed 覆盖策略）
    return publish_task(
        t,
        head_assignee=head_assignee,
        line_map=line_map or None,
        seed_lines=seed_lines,
        overwrite=overwrite,
        pool_status=pool_status,
        assigned_status=assigned_status,
    )



def _is_line_mine(user, line: WmsTaskLine) -> bool:
    """行归属判定：行级优先，头级兜底（且该行无人）"""
    if not user or not getattr(user, "is_authenticated", False):
        return True  # 系统触发/无人上下文 → 放宽；如需强制“必须有操作者”，改为 False
    has_line = TaskAssignment.objects.filter(line=line, finished_at__isnull=True, assignee=user).exists()
    if has_line:
        return True
    has_head_me = TaskAssignment.objects.filter(task=line.task, line__isnull=True,
                                                finished_at__isnull=True, assignee=user).exists()
    any_line = TaskAssignment.objects.filter(line=line, finished_at__isnull=True).exists()
    return bool(has_head_me and not any_line)

@transaction.atomic
def finalize_receive_line(line_id: int, *, by_user=None, trigger: str = "MANUAL"):
    """
    行完成总控：
      - 并发锁行
      - 校验数量与口径
      - 写行完成(时间/人)
      - 关闭该行活动指派
      - 过账（库存/流水）
      - 任务头汇总推进（如全部完成→COMPLETED）
    """
    # 1) 锁行 + 取任务
    line = (WmsTaskLine.objects
            .select_for_update()
            .select_related("task", "product", "from_location", "to_location")
            .get(pk=line_id))
    task = line.task

    # 2) 状态前置：任务需在可执行态
    allowed = {getattr(WmsTask.Status, "RELEASED", "RELEASED"),
               getattr(WmsTask.Status, "IN_PROGRESS", "IN_PROGRESS")}
    if task.status not in allowed:
        raise ValidationError("任务未在可执行状态，无法完成行。")

    # 3) 权限：如果有 by_user，校验“是否为负责人”
    if by_user and not _is_line_mine(by_user, line):
        raise PermissionDenied("仅当前负责人可完成该行。")

    # 4) 读取扩展并核对数量
    try:
        extra = line.receivelineextra  # 如果你的 related_name 是 receive_extra，请对应改名
    except ReceiveLineExtra.DoesNotExist:
        raise ValidationError("缺少收货扩展，无法完成。")

    total = Decimal(extra.qty_ok or 0) + Decimal(extra.qty_damage or 0) + Decimal(extra.qty_reject or 0)
    if total < 0:
        raise ValidationError("数量合计不能为负。")
    if line.qty_plan is not None and total < Decimal(line.qty_plan):
        raise ValidationError("数量未达计划，不能自动完成。")
    # 同步行进度（若模型层已同步，这里是幂等）
    type(line).objects.filter(pk=line.pk).update(qty_done=total)
    line.qty_done = total

    # 5) 标记行完成
    now = timezone.now()
    updates = {"finished_at": now}
    if by_user:
        updates["finished_by"] = by_user
    for f, v in updates.items():
        setattr(line, f, v)
    line.save(update_fields=["qty_done", *updates.keys()])

    # 6) 关闭该行活动指派
    TaskAssignment.objects.filter(line=line, finished_at__isnull=True)\
        .update(finished_at=now)

    # # 7) 过账（库存/流水）——可幂等（用幂等指纹防重复）
    # post_receive_for_line(line=line, extra=extra, by_user=by_user, reason=trigger)

    # 8) 任务头推进：ASSIGNED → IN_PROGRESS（首条完成触发）；全部完成 → COMPLETED
    if task.status == getattr(WmsTask.Status, "RELEASED", "RELEASED"):
        task._allow_status_write = True
        task.status = getattr(WmsTask.Status, "IN_PROGRESS", "IN_PROGRESS")
        task.save(update_fields=["status"])

    # 所有行都完成？
    all_done = not WmsTaskLine.objects.filter(task=task, finished_at__isnull=True).exists()
    if all_done:
        task._allow_status_write = True
        task.status = getattr(WmsTask.Status, "COMPLETED", "COMPLETED")
        task.review_status = getattr(WmsTask.review_status, "PENDING", "PENDING")
        task.save(update_fields=["status","review_status"])

    return {"line": line.pk, "qty_total": str(total), "task_status": task.status, "all_done": all_done}

def _mk_fp(line, extra) -> str:
    """幂等指纹：行ID + 批/效期 + 三类数量"""
    parts = [
        f"L{line.pk}",
        f"LOT{extra.lot_no or ''}",
        f"EXP{extra.exp_date or ''}",
        f"G{extra.qty_done or 0}",
        f"D{extra.qty_damage or 0}",
        f"R{extra.qty_reject or 0}",
    ]
    return "|".join(map(str, parts))

@transaction.atomic
# def post_receive_for_line(*, line: WmsTaskLine, extra, by_user=None, reason="AUTO"):
#     """
#     生成收货过账：InvPosting + InvPostingLine（GOOD/DMG/REJ）
#     幂等：若相同指纹已过账则直接返回
#     """
#     fp = _mk_fp(line, extra)
#
#     # 若已存在相同指纹的过账头，直接返回（避免重复）
#     if InvPosting.objects.filter(fp=fp).exists():
#         return
#
#     now = timezone.now()
#     posting = InvPosting.objects.create(
#         posting_type="RECEIVE",
#         warehouse=line.task.warehouse if hasattr(line.task, "warehouse") else None,
#         task=line.task,
#         line=line,
#         product=line.product,
#         fp=fp,
#         by_user=by_user,
#         remark=f"{reason} 收货过账",
#         posted_at=now,
#     )
#
#     def add_line(status_code: str, qty: Decimal):
#         if qty and qty > 0:
#             InvPostingLine.objects.create(
#                 posting=posting,
#                 product=line.product,
#                 location=line.to_location or line.from_location,
#                 lot_no=getattr(extra, "lot_no", None),
#                 exp_date=getattr(extra, "exp_date", None),
#                 status_code=status_code,         # 例：GOOD/DMG/REJ
#                 qty=qty,
#                 uom="EA",                        # 按你的口径
#             )
#
#     add_line("GOOD", Decimal(extra.qty_done or 0))
#     add_line("DMG",  Decimal(extra.qty_damage or 0))
#     add_line("REJ",  Decimal(extra.qty_reject or 0))
#
#     # 如需更新库存汇总/台账，在此调用你的库存服务（increase on-hand 等）

def _as_wh_mgr(user):
    return user.is_superuser or user.has_perm("tasking.taskconfirm_as_wh_manager")

@transaction.atomic
def approve_task(task_id, *, by_user, note=""):
    if not _as_wh_mgr(by_user):
        raise PermissionDenied("无审核权限。")

    t = (WmsTask.objects.select_for_update().get(pk=task_id))

    if t.status != WmsTask.Status.COMPLETED:
        raise ValidationError("任务未完工，不能审核。")
    if t.review_status not in [WmsTask.ReviewStatus.PENDING]:
        raise ValidationError("当前审核状态不允许再次审核。")

    t.review_status = WmsTask.ReviewStatus.APPROVED
    t.approved_by = by_user
    t.approved_at = timezone.now()
    t.approval_note = note or ""
    # 审核通过 → 待过账
    t.posting_status = WmsTask.PostingStatus.PENDING
    t.save(update_fields=["review_status","approved_by","approved_at","approval_note","posting_status"])
    return t

@transaction.atomic
def reject_task(task_id, *, by_user, note=""):
    if not _as_wh_mgr(by_user):
        raise PermissionDenied("无审核权限。")
    t = (WmsTask.objects.select_for_update().get(pk=task_id))

    if t.status != WmsTask.Status.COMPLETED:
        raise ValidationError("任务未完工，不能驳回。")
    if t.review_status not in [WmsTask.ReviewStatus.PENDING]:
        raise ValidationError("当前审核状态不允许驳回。")

    t.review_status = WmsTask.ReviewStatus.REJECTED
    t.approved_by = by_user
    t.approved_at = timezone.now()
    t.approval_note = note or ""
    # 驳回 → 过账流归 NONE
    t.posting_status = WmsTask.PostingStatus.NONE
    t.save(update_fields=["review_status","approved_by","approved_at","approval_note","posting_status"])
    return t

@transaction.atomic
# def post_task(task_id, *, by_user, note=""):
#     if not _as_wh_mgr(by_user):
#         raise PermissionDenied("无过账权限。")
#     t = (WmsTask.objects.select_for_update().get(pk=task_id))
#
#     if t.review_status != WmsTask.ReviewStatus.APPROVED:
#         raise ValidationError("未审核通过，不能过账。")
#     if t.posting_status == WmsTask.PostingStatus.POSTED:
#         return t  # 幂等
#
#     try:
#         # 过账（抛异常则捕获为 FAILED）
#         post_receive_for_task(task=t, by_user=by_user)
#     except Exception as e:
#         t.posting_status = WmsTask.PostingStatus.FAILED
#         t.posted_by = by_user
#         t.posted_at = timezone.now()
#         t.posting_note = f"{note or ''} {e}"
#         t.save(update_fields=["posting_status","posted_by","posted_at","posting_note"])
#         raise
#
#     t.posting_status = WmsTask.PostingStatus.POSTED
#     t.posted_by = by_user
#     t.posted_at = timezone.now()
#     t.posting_note = note or ""
#     t.save(update_fields=["posting_status","posted_by","posted_at","posting_note"])
#     return t

def save_receiving_snapshot(task_line_id: int, items, operator, source="WEB"):
    """
    用 items 代表的“最新快照”覆盖该行当前未过账的 ScanLog，并让行版本号 +1。
    items: 迭代的 dict；至少包含：
        product(Product实例), location(Location或None), lot_no, expiry_date, serial_no, qty_ok(Decimal)
    注意：owner/warehouse 一律由 tl.task 真源补齐，外部不允许写。
    """
    print("save_receiving_snapshot items=",items)
    from allapp.tasking.models import WmsTask, WmsTaskLine, TaskScanLog  # 依据你的实际 app 路径
    # 如果你表里没有 posted_at 字段，请使用 posting_journal__isnull 判断“未过账”
    print("task_line_id: int, items, operator",task_line_id, items, operator)
    with transaction.atomic():
        # 1) 锁行并拿到任务归属（owner/warehouse）
        tl = (WmsTaskLine.objects
              .select_for_update()
              .select_related("task", "product")
              .get(pk=task_line_id))

        # 2) 审核/过账后禁止覆盖
        if getattr(tl.task, "review_status", "") == "APPROVED" or \
           getattr(tl.task, "posting_status", "") in ("PENDING", "POSTED"):
            raise ValueError("该行已进入审核/过账阶段，禁止覆盖，请走冲销流程。")

        # 3) 软删除当前未过账事实（READY & 未关联过账）
        print("task_line_id=tl.id=",tl.id)
        # (TaskScanLog.objects
        #     .filter(task_line_id=tl.id, status__in=["READY", "RESERVED"], posting_journal__isnull=True)
        #     .update(status="IGNORED", remark="SNAPSHOT_REPLACED"))

        (TaskScanLog.objects
            .filter(task_id=tl.task_id,task_line_id=tl.id, posting_journal__isnull=True)
            .update(status="IGNORED", remark="SNAPSHOT_REPLACED"))

       #test
        # 查询所有记录
        print("111 task_line_id=tl.id=", tl.id)
        task_scan_logs = TaskScanLog.objects.filter(task_line_id=tl.id)

        # 打印每条记录的基本信息
        for task_scan_log in task_scan_logs:
            print(
                f"TaskScanLog(id={task_scan_log.id}, status={task_scan_log.status}, task_line_id={task_scan_log.task_line_id})")
        print("222 task_line_id=tl.id=", tl.id)
       #/test


        # 4) 原子自增版本号，并取新值
        (WmsTaskLine.objects
            .filter(pk=tl.pk)
            .update(scan_snapshot_rev=F("scan_snapshot_rev") + 1))
        tl.refresh_from_db(fields=["scan_snapshot_rev"])
        new_rev = tl.scan_snapshot_rev

        # ★ 判断是否 COUNT 任务
        is_count_task = (tl.task.task_type == WmsTask.TaskType.COUNT)

        # 5) 生成新日志（把归属信息 owner/warehouse 在此统一补齐）
        now = timezone.now()
        new_logs = []
        print("1 items",items)
        for it in items:
            qty = it.get("qty_ok")
            print("qty_ok qty_ok qty_ok", qty)
            if not qty:
                continue

            product     = it["product"]
            product_id  = getattr(product, "pk", None)
            location    = it.get("location")
            location_id = getattr(location, "pk", None) if location else None
            lot_no      = it.get("lot_no") or ""
            # 兼容键名：expiry_date / exp_date
            expiry      = it.get("expiry_date")
            if expiry is None:
                expiry = it.get("exp_date")
            serial      = it.get("serial_no") or ""

            fp = build_scan_fp(
                task_id=tl.task_id, line_id=tl.id,
                product_id=product_id, location_id=location_id,
                lot=lot_no, expiry=expiry, serial=serial,
                qty=str(qty), rev=new_rev,
            )

            # ★ 关键分支：COUNT 用 qty_base，其他用 qty_base_delta
            qty_kwargs = {"qty_base": qty} if is_count_task else {"qty_base_delta": qty}

            new_logs.append(TaskScanLog(
                # —— 归属信息：一律从任务真源带入 —— #
                owner=tl.task.owner,
                warehouse=tl.task.warehouse,

                # —— 业务维度 —— #
                task_id=tl.task_id,
                task_line_id=tl.id,
                product=product,
                location=location,
                lot_no=(lot_no or None),
                exp_date=expiry,
                # serial_no=serial,               # 若你已有此列再放开

                # —— 数量与状态 —— #
                # qty_base_delta=qty,       # 注意字段名对齐模型（qty_ok -> qty_base_delta）
                **qty_kwargs,  # ★ 就在这里生效
                status="OK",
                source=source,
                method="MANUAL",
                by_user=operator,                 # FK 到用户

                # —— 审计与幂等 —— #
                scan_snapshot_rev=new_rev,        # 冗余版本号（列名按你的模型）
                fp=fp,
                # posting_journal 过账时再赋值
            ))
        print("len new_logs 0 ", len(new_logs), new_logs[0] if new_logs else None)

        if not new_logs:
            # 没有任何有效数量（qty_ok <= 0 等），不写日志，直接返回当前版本号
            return new_rev
        obj = new_logs[0]
        fields = [f.name for f in obj._meta.concrete_fields]
        print({name: getattr(obj, name) for name in fields})
        if new_logs:
            print("2 new_logs", new_logs)
            TaskScanLog.objects.bulk_create(new_logs, batch_size=1000)
            print("3 new_logs after TaskScanLog.objects.bulk_create")
            # TaskScanLog.objects.bulk_create(new_logs, ignore_conflicts=True, batch_size=1000)

    return new_rev

# services.py
# ---- 通用：锁定行并带必要关联 ----
def _lock_line(line_id: int) -> WmsTaskLine:
    """锁定并获取任务行"""
    return (WmsTaskLine.objects
            .select_for_update()
            .select_related("task", "product", "from_location", "to_location")
            .get(pk=line_id))

# ---- 通用：任务需在可执行态（RELEASED / IN_PROGRESS）----
def _assert_task_executable(task: WmsTask) -> None:
    """校验任务是否处于可执行状态"""
    allowed = {getattr(WmsTask.Status, "RELEASED", "RELEASED"),
               getattr(WmsTask.Status, "IN_PROGRESS", "IN_PROGRESS")}
    if task.status not in allowed:
        raise ValidationError("任务未在可执行状态，无法完成行。")

# ---- 通用：权限（行归属）----
def _assert_line_owner(by_user, line: WmsTaskLine) -> None:
    """校验当前用户是否是行的负责人"""
    if by_user and not _is_line_mine(by_user, line):  # 现成工具函数
        raise PermissionDenied("仅当前负责人可完成该行。")

# ---- 通用：把行标记为完成 + 关闭该行活动指派 ----
def _finish_line(line: WmsTaskLine, *, qty_total: Decimal, by_user=None) -> None:
    """标记任务行完成并更新"""
    now = timezone.now()
    # 写已完数量 & 完成打点
    updates = {"qty_done": qty_total, "finished_at": now}
    if by_user:
        updates["finished_by"] = by_user
    type(line).objects.filter(pk=line.pk).update(**updates)         # 批量写更稳
    # 若后续要读 line 的新值
    for f, v in updates.items():
        setattr(line, f, v)
    # 关闭该行的活动指派
    TaskAssignment.objects.filter(line=line, finished_at__isnull=True).update(finished_at=now)

# ---- 通用：推进任务头（首条完工→IN_PROGRESS；全部完工→COMPLETED, review=PENDING）----
def _advance_task(task: WmsTask) -> None:
    """推进任务头，若所有行完成，则标记任务为 COMPLETED"""
    if task.status == getattr(WmsTask.Status, "RELEASED", "RELEASED"):
        task._allow_status_write = True
        task.status = getattr(WmsTask.Status, "IN_PROGRESS", "IN_PROGRESS")
        task.save(update_fields=["status"])
    # 若全部行 finished_at 有值 → 完成任务并进入待审核
    all_done = not WmsTaskLine.objects.filter(task=task, finished_at__isnull=True).exists()
    if all_done:
        task._allow_status_write = True
        task.status = getattr(WmsTask.Status, "COMPLETED", "COMPLETED")
        # 兼容大小写属性
        rs = getattr(WmsTask, "ReviewStatus", None)
        pending = getattr(rs, "PENDING", "PENDING") if rs else "PENDING"
        task.review_status = pending
        task.finished_at = timezone.now()
        task.save(update_fields=["status", "review_status", "finished_at"])
        print("887 task.review_status,task.posting_status",task.review_status,task.posting_status)

# ---- 通用：获取扩展并计算数量（通用 fetch 函数）----
def make_fetch(
    *,
    extra_model,
    qty_attr: str,                 # 扩展里哪个字段代表完成量（如 qty_ok / qty_moved / qty_picked / qty_counted）
    require_from: bool = False,    # 是否必须有来源库位
    require_to: bool = False,      # 是否必须有目标库位（PUTAWAY/RELOC/REPLEN 常用）
    must_positive: bool = True,    # 完成量是否必须 > 0（COUNT 可不强制）
    ensure_same_warehouse: bool = True,  # 校验 from/to 属于任务仓库
    fallback_line_qty_done: bool = True, # 扩展字段为空时，是否回退用 line.qty_done
):
    """
    生成“取扩展+算数量+基础校验”的 fetch 函数。
    返回：qty_total(Decimal), extra(扩展对象)
    """
    def _fetch(line):
        try:
            # OneToOne 默认 related_name=模型小写，例如 receivelineextra / putawaylineextra / picklineextra...
            rel_name = extra_model.__name__.replace("LineExtra", "").lower() + "lineextra"
            extra = getattr(line, rel_name)
        except Exception:
            try:
                extra = extra_model.objects.get(line_id=line.id)
            except extra_model.DoesNotExist:
                raise ValidationError(f"缺少 {extra_model.__name__}，无法完成。")

        raw = getattr(extra, qty_attr, None)
        qty = Decimal(raw or (line.qty_done if fallback_line_qty_done else 0) or 0)
        if must_positive and qty <= 0:
            raise ValidationError("完成数量必须 > 0。")

        # 库位与仓库校验
        task_wh = line.task.warehouse_id
        if require_from and not line.from_location_id:
            raise ValidationError("缺少来源库位。")
        if require_from and line.from_location.warehouse_id != task_wh:
            raise ValidationError("来源库位不在任务仓库。")

        to_id = getattr(line, "to_location_id", None) or getattr(extra, "to_location_id", None)
        if require_to and not to_id:
            raise ValidationError("缺少目标库位。")
        if ensure_same_warehouse and require_to and to_id:
            to_loc = getattr(line, "to_location", None) if getattr(line, "to_location_id", None) else getattr(extra, "to_location", None)
            if to_loc and to_loc.warehouse_id != task_wh:
                raise ValidationError("目标库位不在任务仓库。")

        return qty, extra
    return _fetch

# ---- 各任务类型策略 ----
# 这里我们为每种任务类型定制自己的数量计算和校验逻辑
FINALIZE_STRATEGIES = {
    "RECEIVE": {
        "fetch": make_fetch(extra_model=ReceiveLineExtra, qty_attr="qty_ok", must_positive=False),
        "after": lambda line, extra: None,  # 收货无额外处理
        "post":  lambda line, extra, *, by_user=None, reason="AUTO": None,   # 收货不做即时过账
    },
    "PUTAWAY": {
        "fetch": make_fetch(extra_model=PutawayLineExtra, qty_attr="qty_moved", require_from=True, require_to=True),
        "after": lambda line, extra: None,  # 上架无额外操作
        "post":  lambda line, extra, *, by_user=None, reason="AUTO": None,  # 上架不做即时过账
    },
    "PICK": {
        "fetch": make_fetch(extra_model=PickLineExtra, qty_attr="qty_picked", require_from=True, require_to=False),
        "after": lambda line, extra: None,  # 拣货无额外操作
        "post":  lambda line, extra, *, by_user=None, reason="AUTO": None,  # 拣货不做即时过账
    },
    "DISPATCH": {
        "fetch": make_fetch(extra_model=DispatchLineExtra, qty_attr="qty_dispatch", require_from=True, require_to=False),
        "after": lambda line, extra: None,  # 发运无额外操作
        "post": lambda line, extra, *, by_user=None, reason="AUTO": None,  # 发运不做即时过账
    },
    "REPLENISH": {
        "fetch": make_fetch(extra_model=ReplenishLineExtra, qty_attr="qty_moved", require_from=True, require_to=True),
        "after": lambda line, extra: None,  # 补货无额外操作
        "post":  lambda line, extra, *, by_user=None, reason="AUTO": None,  # 补货不做即时过账
    },
    "RELLOC": {
        "fetch": make_fetch(extra_model=RelocLineExtra, qty_attr="qty_moved", require_from=True, require_to=True),
        "after": lambda line, extra: None,  # 移位无额外操作
        "post":  lambda line, extra, *, by_user=None, reason="AUTO": None,  # 移位不做即时过账
    },
    "COUNT": {
        "fetch": make_fetch(extra_model=CountLineExtra, qty_attr="qty_counted", must_positive=False),
        "after": lambda line, extra: None,  # 盘点无额外操作
        "post":  lambda line, extra, *, by_user=None, reason="AUTO": None,  # 盘点不做即时过账
    },
}

# ---- 统一入口：任务行完成 ----
@transaction.atomic
def finalize_task_line(line_id: int, *, by_user=None, trigger: str = "MANUAL"):
    """
    通用“行完成”：
      - 锁行/校验任务状态/权限
      - 基于任务类型选择策略：取扩展+算数量+专项校验
      - 标记行完成（数量/时间/人）+ 关闭行级指派
      - （可选）调用场景过账
      - 推进任务头（首条→IN_PROGRESS；全部→COMPLETED & review=PENDING）
    """
    line = _lock_line(line_id)                                      # 锁 + 取关联
    task = line.task
    _assert_task_executable(task)                                   # 可执行态校验
    _assert_line_owner(by_user, line)                               # 负责人校验

    tt = getattr(task, "task_type", None) or ""
    strat = FINALIZE_STRATEGIES.get(tt)
    if not strat:
        raise ValidationError(f"不支持的任务类型：{tt}")

    # 任务类型自己的数量口径 & 专项业务校验
    qty_total, extra = strat["fetch"](line)

    # 写完成 & 关闭指派（公共实现）
    _finish_line(line, qty_total=qty_total, by_user=by_user)

    # 类型特有的“行完成后的补记”动作
    if strat.get("after"):
        strat["after"](line, extra)

    # # 可选：若需要即时过账
    # if strat.get("post"):
    #     strat["post"](line, extra, by_user=by_user, reason=trigger)

    # 推进任务头（公共实现）
    _advance_task(task)

    return {"line": line.pk, "qty_total": str(qty_total), "task_status": task.status}




@transaction.atomic
def generate_count_lines(
    task: WmsTask,
    *,
    zone_type=None,                 # Zone 实体，可空
    location=None,             # 指定单个库位，可空
    location_prefix: str | None = None,  # 库位前缀，可空
    product=None,              # 单个商品，可空
    batch_no: str | None = None,         # 批次号（字符串），可空
    ignore_zero: bool = True,            # 忽略在库=0
    limit: int = 100000,                   # 最多生成行数
    method: str = "BLIND",               # 盘点方式（向导未给就走默认）
) -> int:
    """
    按盘点向导字段生成盘点任务行；仅使用现有字段：
      owner/warehouse 来自 task，本函数不再重复传参。
      zone/location/location_prefix/product/batch_no/ignore_zero/limit 对应向导页面。
    """

    # 1) 基础范围：该任务的货主+仓库
    qs = InventoryDetail.objects.filter(
        owner=task.owner,
        warehouse=task.warehouse,
        is_active=True,
    )

    # 2) 逐项套用向导筛选（全部是现有字段）
    if zone_type:
        qs = qs.filter(zone_type=zone_type)

    if location:
        qs = qs.filter(location=location)
    elif location_prefix:
        prefix = (location_prefix or "").strip().upper()
        if prefix:
            qs = qs.filter(location__code__istartswith=prefix)

    if product:
        qs = qs.filter(product=product)

    if batch_no:
        bn = (batch_no or "").strip().upper()
        if bn:
            # 库存明细里是 batch_no（字符串）
            qs = qs.filter(batch_no__iexact=bn)

    if ignore_zero:
        # “忽略在库为 0 的明细”：按账面库存 onhand 过滤
        qs = qs.filter(onhand_qty__gt=0)

    # 3) 选取需要的字段，排序 & 限制数量
    qs = (
        qs.select_related("product", "location")
          .order_by("location__code", "product__code", "expiry_date", "batch_no")[:limit]
    )

    # 4) 落任务行 + 行扩展（账面数快照放在 qty_book；实盘数初始为 0）
    created = 0
    for inv in qs:
        line = WmsTaskLine.objects.create(
            task=task,
            product=inv.product,
            from_location=inv.location,
            qty_plan=inv.onhand_qty or Decimal("0"),
            status=WmsTaskLine.Status.DRAFT,
        )
        CountLineExtra.objects.create(
            line=line,
            lot_no=inv.batch_no or "",           # 你模型里批次是 batch_no（字符串）
            exp_date=getattr(inv, "expiry_date", None),
            # lpn_no：库存没有该字段，不能用于筛选；这里仅占位为空串
            qty_book=inv.onhand_qty or Decimal("0"),
            qty_counted=Decimal("0"),
            method=method,
        )
        created += 1

    return created

@transaction.atomic
def finalize_count_task_after_logs(task: WmsTask, *, by_user=None) -> str:
    """
    在【所有行的 CountLineExtra 都已 COUNTED 且行的 ScanLog 已写好】之后调用。

    分支：
      - 仍有未盘行：返回 "NOOP_PENDING_LINES"
      - 有差异：
          * 若可进入下一轮（未达上限）：创建下一轮复盘任务；当前任务仅置 COMPLETED；返回 "NEXT_ROUND_CREATED"
          * 若已达上限：当前任务直接 COMPLETED + APPROVED + PENDING；返回 "FINALIZED_FOR_POSTING"
      - 无差异：当前任务直接 COMPLETED + APPROVED + PENDING；返回 "COMPLETED_NO_DIFF"
    """
    print("1 finalize_count_task_after_logs")
    if not task or task.task_type != WmsTask.TaskType.COUNT:
        return "NOOP_NOT_COUNT"

    print("2 finalize_count_task_after_logs")
    # 若已处于审核/过账流，不再处理
    if task.review_status == WmsTask.ReviewStatus.APPROVED or \
       task.posting_status in (WmsTask.PostingStatus.PENDING, WmsTask.PostingStatus.POSTED):
        print("2.1 已处于审核/过账流，不再处理 finalize_count_task_after_logs")
        return "NOOP_LOCKED"

    print("3 finalize_count_task_after_logs")
    line_ids = list(task.lines.values_list("id", flat=True))
    if not line_ids:
        # 没有行，当作无差异直接完成

        WmsTask.objects.filter(pk=task.pk).update(
            status=WmsTask.Status.COMPLETED,
            review_status=WmsTask.ReviewStatus.APPROVED,
            posting_status=WmsTask.PostingStatus.PENDING,
        )
        return "COMPLETED_NO_LINES"

    print("3.1 # task没有行，当作无差异直接完成 finalize_count_task_after_logs")
    # 还有未盘行 → 不收尾
    if CountLineExtra.objects.filter(line_id__in=line_ids).exclude(count_status="COUNTED").exists():
        print("4 还有未盘行 → 不收尾  finalize_count_task_after_logs")
        return "NOOP_PENDING_LINES"

    print("5 没有未盘行 → 收尾 finalize_count_task_after_logs")
    # 差异集合（任一行 qty_diff != 0 即视为有差异）
    diff_qs = (CountLineExtra.objects
               .filter(line_id__in=line_ids)
               .exclude(qty_diff=0)
               .exclude(qty_diff__isnull=True))

    # 取当前轮次（同一任务内应一致；取任一行即可）
    any_extra = (CountLineExtra.objects
                 .filter(line_id__in=line_ids)
                 .only("countorder")
                 .first())
    cur_order = any_extra.countorder if any_extra else CountLineExtra.CountOrder.FIRST

    ORDER = ("FIRST", "SECOND", "THIRD")
    limit = max(1, min(getattr(settings, "COUNT_MAX_TIMES", 3), len(ORDER)))
    i = ORDER.index(cur_order)

    if diff_qs.exists():
        print("6 有差异  diff_qs.exists finalize_count_task_after_logs")
        # —— 有差异 —— #
        if i + 1 < limit:
            # 还有下一轮：创建复盘任务（幂等防重复）
            print("7 还有下一轮：创建复盘任务 幂等防重复 finalize_count_task_after_logs")
            remark = f"任务号{task.task_no}的复盘"
            exists_rc = WmsTask.objects.filter(
                task_type=WmsTask.TaskType.COUNT,
                status=WmsTask.Status.DRAFT,
                remark=remark,
                owner=task.owner,
                warehouse=task.warehouse,
            ).exists()
            if not exists_rc:
                biz_date = datetime.today().date()
                count_task_no = DocSequence.next_code(
                    doc_type="PD",
                    warehouse=task.warehouse,
                    owner=task.owner,
                    biz_date=biz_date,
                )
                print("8 rc = WmsTask.objects.create finalize_count_task_after_logs")
                rc = WmsTask.objects.create(
                    task_no=count_task_no,
                    task_type=WmsTask.TaskType.COUNT,
                    status=WmsTask.Status.DRAFT,
                    owner=task.owner,
                    warehouse=task.warehouse,
                    remark=remark,
                    created_by=by_user,
                )
                new_order = ORDER[i + 1]
                # 用本轮的账面快照作为下一轮的计划/账面
                for d in diff_qs.select_related("line", "line__product", "line__from_location"):
                    print("9 WmsTaskLine.objects.create CountLineExtra.objects.create")
                    line = WmsTaskLine.objects.create(
                        task=rc,
                        product=d.line.product,
                        from_location=d.line.from_location,
                        qty_plan=d.qty_book,                  # 用账面数做下一轮计划
                        status=WmsTaskLine.Status.DRAFT,
                    )
                    CountLineExtra.objects.create(
                        line=line,
                        lot_no=d.lot_no,
                        exp_date=d.exp_date,
                        qty_book=d.qty_book,                  # 延续账面快照
                        qty_counted=Decimal("0"),
                        method="BLIND",
                        countorder=new_order,
                    )

            # 当前轮只置已完成，不审核不过账
            print("10 当前轮只置已完成，不审核不过账")
            WmsTask.objects.filter(pk=task.pk).update(
                status=WmsTask.Status.COMPLETED,
                review_status=WmsTask.ReviewStatus.NEED_RECOUNT,
                posting_status=WmsTask.PostingStatus.NEED_RECOUNT,
            )
            return "NEXT_ROUND_CREATED"

        else:
            # 达到上限，不能再复盘：直接进入审核/过账流
            print("11 达到上限，不能再复盘：直接进入审核/过账流")
            WmsTask.objects.filter(pk=task.pk).update(
                status=WmsTask.Status.COMPLETED,
                review_status=WmsTask.ReviewStatus.APPROVED,
                posting_status=WmsTask.PostingStatus.PENDING,
            )
            return "FINALIZED_FOR_POSTING_WITH_DIFF"

    # —— 无差异 —— #
    WmsTask.objects.filter(pk=task.pk).update(
        status=WmsTask.Status.COMPLETED,
        review_status=WmsTask.ReviewStatus.APPROVED,
        posting_status=WmsTask.PostingStatus.PENDING,
    )
    return "COMPLETED_NO_DIFF"



def get_line_extra_generic(tl: WmsTaskLine):
    """按任务类型优先映射到对应 *LineExtra；失败则回退到 OneToOne 反向关系自动探测。"""
    if not tl or not getattr(tl, "id", None):
        return None

    task = getattr(tl, "task", None)
    ttype = getattr(task, "task_type", None)

    # 1) 明确映射（只写你项目真实存在的模型名；缺哪个加哪个）
    MAP = {
        getattr(WmsTask.TaskType, "RECEIPT",  None): "ReceiveLineExtra",
        getattr(WmsTask.TaskType, "PUTAWAY",  None): "PutawayLineExtra",
        getattr(WmsTask.TaskType, "PICK",     None): "PickLineExtra",
        getattr(WmsTask.TaskType, "REVIEW",   None): "ReviewLineExtra",
        getattr(WmsTask.TaskType, "PACK",     None): "PackLineExtra",
        getattr(WmsTask.TaskType, "LOAD",     None): "LoadLineExtra",
        getattr(WmsTask.TaskType, "DISPATCH", None): "DispatchLineExtra",
        getattr(WmsTask.TaskType, "REPLENISH",None): "ReplenishLineExtra",
        getattr(WmsTask.TaskType, "COUNT",    None): "CountLineExtra",
        getattr(WmsTask.TaskType, "ADJUST",   None): "AdjustLineExtra",
    }
    model_name = MAP.get(ttype)
    if model_name:
        try:
            Model = apps.get_model("allapp.tasking", model_name)
            obj = (Model.objects
                   .select_related("line", "line__task")
                   .filter(line_id=tl.id)
                   .first())
            if obj:
                return obj
        except LookupError:
            pass

    # 2) 回退：一对一反向关系里寻找 *LineExtra
    for rel in tl._meta.related_objects:
        if isinstance(rel, OneToOneRel):
            Model = rel.related_model
            if Model.__name__.endswith("LineExtra"):
                accessor = rel.get_accessor_name()
                try:
                    return getattr(tl, accessor)
                except Model.DoesNotExist:
                    continue
    return None


def _first_not_none(*vals):
    for v in vals:
        if v is not None:
            return v
    return None


def build_scanlog_items(tl: WmsTaskLine, extra) -> list | None:
    """
    通用地从 tl/extra 拼装一条 ScanLog item。
    兼容不同扩展上的字段命名；qty 取“第一个非 None”，再判断 > 0 才记。
    """
    if not tl or not extra:
        return None

    product  = getattr(tl, "product", None)
    # 位置优先 to_location → 扩展的 location → from_location
    location = _first_not_none(
        getattr(extra, "to_location", None),
        getattr(extra, "location", None),
        getattr(tl,   "to_location", None),
        getattr(tl,   "from_location", None),
    )
    lot_no   = _first_not_none(getattr(extra, "lot_no", None), getattr(extra, "batch_no", None))
    expiry   = _first_not_none(getattr(extra, "exp_date", None), getattr(extra, "expiry_date", None))
    serial   = getattr(extra, "serial_no", None)

    # 兼容不同扩展的“实绩量”字段名（注意用 is not None，避免 0 被当作 False 跳过）
    # qty = _first_not_none(
    #     getattr(extra, "qty_ok", None),
    #     getattr(extra, "qty_moved", None),
    #     getattr(extra, "qty_picked", None),
    #     getattr(extra, "qty_loaded", None),
    #     getattr(extra, "qty_dispatch", None),
    #     getattr(extra, "qty_counted", None),
    # )

    # ★ 关键修改：COUNT 用差异，其它任务用增量
    if getattr(tl.task, "task_type", None) == WmsTask.TaskType.COUNT:
        qty = getattr(extra, "qty_diff", None)  # ← 差异=实盘-账面（可正可负）
        # COUNT 允许负数，只有“非 None 且非 0”才生成
        if qty is None or Decimal(qty) == 0:
            return None
    else:
        qty = _first_not_none(
            getattr(extra, "qty_ok", None),
            getattr(extra, "qty_moved", None),
            getattr(extra, "qty_picked", None),
            getattr(extra, "qty_loaded", None),
            getattr(extra, "qty_dispatch", None),
        )
        # 流转类只接收“正的增量”
        if qty is None or Decimal(qty) <= 0:
            return None

    if not product:
        return None

    return [{
        "product":     product,
        "location":    location,
        "lot_no":      lot_no,
        "expiry_date": expiry,
        "serial_no":   serial,
        "qty_ok":      Decimal(qty),
    }]


def is_task_locked(task: WmsTask) -> bool:
    """统一判断任务头是否进入审核/过账阶段，避免服务层抛错后崩请求。"""
    if not task:
        return False
    approved = getattr(task, "review_status", "") == getattr(WmsTask.ReviewStatus, "APPROVED", "APPROVED")
    posting  = getattr(task, "posting_status", "") in (
        getattr(WmsTask.PostingStatus, "PENDING", "PENDING"),
        getattr(WmsTask.PostingStatus, "POSTED",  "POSTED"),
    )
    return approved or posting


# 任务类型 → 收尾函数（有则调用；没有就跳过）
FINALIZERS = {
    getattr(WmsTask.TaskType, "COUNT", None): getattr(services, "finalize_count_task_after_logs", None),
    # 其他类型需要时可补充：
    # WmsTask.TaskType.PICK: services.finalize_pick_task_after_logs,
    # ...
}
