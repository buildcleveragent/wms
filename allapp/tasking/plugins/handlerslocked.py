# allapp/tasking/plugins/handlers.py
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.utils import timezone

from allapp.tasking.models import WmsTask, TaskScanLog
from allapp.inventory.models import PostingJournal
from allapp.inventory import services as inv_services


# -----------------------
# 工具 & 可插拔入口
# -----------------------
def _has_field(model, name: str) -> bool:
    try:
        model._meta.get_field(name)
        return True
    except Exception:
        return False


def get_posting_handler():
    """
    从 settings.TASKING_POSTING_HANDLER 动态加载处理器类，并实例化。
    例如：'allapp.tasking.plugins.handlers.DefaultPostingHandler'
    """
    from importlib import import_module
    path = getattr(settings, "TASKING_POSTING_HANDLER", None)
    if not path:
        raise ImproperlyConfigured("TASKING_POSTING_HANDLER 未配置")
    mod_path, cls_name = path.rsplit(".", 1)
    mod = import_module(mod_path)
    cls = getattr(mod, cls_name)
    return cls()


class BasePostingHandler:
    """
    处理器接口：统一从这里过账
    handle(task=..., scans=..., now=None, batch_no=None, note='', by_user=None) -> int(创建的交易/批次数，服务端可自定义)
    """
    def handle(
        self,
        *,
        task: WmsTask,
        scans: Optional[Iterable[TaskScanLog]] = None,
        now=None,
        batch_no: Optional[str] = None,
        note: str = "",
        by_user=None,
    ) -> int:
        raise NotImplementedError


# -----------------------
# 默认处理器（编排层）
# -----------------------
class DefaultPostingHandler(BasePostingHandler):
    def handle(self, *, task, scans=None, now=None, batch_no=None, note="", by_user=None) -> int:
        # —— 0) 自动查可过账扫描（READY/OK & 未过账） —— #
        ok_val = getattr(getattr(TaskScanLog, "ScanStatus", None), "OK", "OK")

        if scans is None:
            scans = TaskScanLog.objects.select_for_update().filter(
                task_id=task.id, status=ok_val, posted_at__isnull=True
            )

        print("scans", scans,len(scans))

        # —— 1) 任务级 PJ：幂等与审计（先建成 PENDING） —— #
        pj, created = PostingJournal.objects.get_or_create(
            src_model="WmsTask", src_id=task.id, tx_type="POST",
            defaults={"status": "PENDING", "message": (note or "过账")[:255], "attempt_count": 0},
        )
        if not created:
            if pj.status == "POSTED":
                return 0
            pj.attempt_count = (pj.attempt_count or 0) + 1
            pj.message = (note or pj.message or "")[:255]
            pj.save(update_fields=["attempt_count", "message", "updated_at"])

        # —— 2) 过滤扫描：READY/OK 且未被 REJECTED；若空直接报错 —— #
        REJECTED = getattr(getattr(TaskScanLog, "ReviewStatus", None), "REJECTED", "REJECTED")
        OK = getattr(getattr(TaskScanLog, "ScanStatus", None), "OK", "OK")
        scans_ok = [s for s in (scans or [])
                    if getattr(s, "status", None) == OK
                    and getattr(s, "review_status", None) != REJECTED]
        if not scans_ok:
            # 注意：这里不写 DB，只抛错；让上层去把 PJ 标 FAILED
            raise ValueError("无可过账明细（需要 READY/OK 且未过账的 TaskScanLog）")

        # —— 3) 做“重活儿”放到内层原子事务 —— #
        try:
            affected = self._handle_atomic(task=task, scans=scans_ok, now=now, batch_no=batch_no, note=note, by_user=by_user, pj=pj)
        except Exception as e:
            # 🚫 不能在 atomic 内 save()；这里已脱离内层事务，安全写 FAILED
            pj.status = "FAILED"
            pj.message = (str(e) or "FAILED")[:255]
            pj.save(update_fields=["status", "message", "updated_at"])
            raise

        # —— 4) 成功：单独把 PJ 标 POSTED（可选带 posted_at） —— #
        pj.status = "POSTED"
        pj.message = (note or pj.message or "POSTED")[:255]
        if _has_field(PostingJournal, "posted_at"):
            pj.posted_at = timezone.now()
            pj.save(update_fields=["status", "message", "posted_at", "updated_at"])
        else:
            pj.save(update_fields=["status", "message", "updated_at"])

        return affected

    @transaction.atomic
    def _handle_atomic(self, *, task, scans, now=None, batch_no=None, note="", by_user=None, pj=None) -> int:
        """内层原子：委托库存服务 + 批量回填 ScanLog + 落账任务头；这里不要在 except 里 save()。"""
        now_ts = now or timezone.now()
        batch = batch_no or (timezone.now().strftime("%Y%m%d-%H%M%S-") + str(uuid4())[:8])

        # 3.1 委托库存服务
        result = inv_services.post_task(task=task, user=by_user)

        # —— 严格校验返回 —— #
        affected = 0
        if isinstance(result, dict):
            # 你们的 services 里若有这类字段，任选一个对齐
            affected = int(
                result.get("affected_tx_count")
                or result.get("tx_count")
                or result.get("created_transactions")
                or result.get("ok") and 1
                or 0
            )
        else:
            # 旧实现可能只返回 True/False
            affected = 1 if result else 0

        if affected <= 0:
            raise ValueError("库存过账未生成任何交易（InventoryTransaction），已回滚。")

        # 3.1 委托库存服务（与你的 inventory.services 对齐）
        # result = inv_services.post_task(task=task, user=by_user)
        # （可选严谨）若库存服务没有生成任何交易，可视为失败
        # tx_count = int(result.get("affected_tx_count", 0)) if isinstance(result, dict) else int(bool(result))
        # if tx_count <= 0:
        #     raise ValueError("库存服务未生成任何交易")



        # 3.2 扫描批量打点（READY/OK → POSTED）
        # 只打点，不改 status（保持 OK）
        for s in scans:
            s.posting_journal_id = getattr(pj, "id", None)
            s.posted_at = now_ts
            s.posting_batch = batch
        TaskScanLog.objects.bulk_update(scans, ["posting_journal", "posted_at", "posting_batch"])

        # 3.3 任务头落账
        posted_status = getattr(getattr(WmsTask, "PostingStatus", None), "POSTED", "POSTED")
        update_fields = []
        if task.posting_status != posted_status:
            task.posting_status = posted_status; update_fields.append("posting_status")
        by_user_id = getattr(by_user, "pk", None) or getattr(pj, "created_by_id", None)
        if by_user_id is not None and task.posted_by_id != by_user_id:
            task.posted_by_id = by_user_id; update_fields.append("posted_by")
        if not task.posted_at:
            task.posted_at = now_ts; update_fields.append("posted_at")
        if note:
            task.posting_note = note[:200]; update_fields.append("posting_note")
        if update_fields:
            task.save(update_fields=update_fields)

        # 你想返回什么都行：这里返回 1 表示这次任务级过账成功
        return 1

