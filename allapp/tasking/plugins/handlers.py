# allapp/tasking/plugins/handlers.py
from __future__ import annotations
from typing import Any, Dict, Iterable, List, Optional
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.utils import timezone

from allapp.tasking.models import WmsTask, TaskScanLog,WmsTaskLine
from allapp.inventory.models import PostingJournal, InventoryTransaction
from allapp.inventory import services as inv_services
from allapp.core.models import DocSequence
from allapp.core.utils.log_context import build_log_payload

import logging
log = logging.getLogger(__name__)
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
    ctx, ctx_text = build_log_payload()
    log.info("tasking.posting_handler.load.begin %s", ctx_text, extra=ctx)
    path = getattr(settings, "TASKING_POSTING_HANDLER", None)
    if not path:
        raise ImproperlyConfigured("TASKING_POSTING_HANDLER 未配置")
    mod_path, cls_name = path.rsplit(".", 1)
    mod = import_module(mod_path)
    cls = getattr(mod, cls_name)
    log.info(
        "tasking.posting_handler.load.completed %s handler=%s",
        ctx_text,
        path,
        extra=ctx,
    )
    return cls()


class BasePostingHandler:
    """
    处理器接口：统一从这里过账
    handle(task=..., scans=..., now=None, batch_no=None, note='', by_user=None) -> int
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
# 默认处理器（统一加锁顺序 + 编排层）
# -----------------------
class DefaultPostingHandler(BasePostingHandler):
    """
    Scan-Only 统一处理器（以 TaskScanLog 为唯一数据源）
    - 加锁顺序：WmsTask -> WmsTaskLine -> TaskScanLog(order_by id)
      （保留对 TaskLine 的锁，确保并发下行→扫的拓扑顺序稳定；不再做行级过账）
    - 落账入口：inventory.services.post_task(...)（仅扫描驱动，不调用任何行级 post_*）
    - 扫描打点：批量写 TaskScanLog.posting_journal / posted_at / posting_batch（不改 status）
    - 任务回填：posting_status / posted_at / posted_by / posting_note
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

        # 0) 统一取枚举/字面值
        OK        = getattr(getattr(TaskScanLog, "ScanStatus", None), "OK", "OK")
        REJECTED  = getattr(getattr(TaskScanLog, "ReviewStatus", None), "REJECTED", "REJECTED")

        # 1) 任务级 PJ（幂等与审计）—— 先建成 PENDING（外层非原子）
        pj, created = PostingJournal.objects.get_or_create(
            src_model="WmsTask",
            src_id=task.id,
            tx_type="POST",
            defaults={"status": "PENDING", "message": (note or "过账")[:255], "attempt_count": 0},
        )

        ctx, ctx_text = build_log_payload(task=task, user=by_user, journal=pj, posting_batch=batch_no)
        log.info("tasking.post.begin %s", ctx_text, extra=ctx)
        log.info(
            "tasking.post.journal_ready %s status=%s created=%s",
            ctx_text,
            pj.status,
            created,
            extra=ctx,
        )
        # （如果你做了“PJ=POSTED 但还有扫描则 REPOST”的逻辑）
        # log.info("[POST] repost decision: task=%s need_repost=%s new_pj=%s",
        #          task.id, need_repost, getattr(new_pj, "id", None))

        #（可选）同步 owner/warehouse 字段
        for fld, val in (("owner_id", task.owner_id), ("warehouse_id", task.warehouse_id)):
            if _has_field(PostingJournal, fld) and getattr(pj, fld, None) != val:
                setattr(pj, fld, val)

        if not created:
            if pj.status == "POSTED":
                log.info("tasking.post.already_posted %s", ctx_text, extra=ctx)
                # 幂等：任务已过账，直接返回
                return 0
            pj.attempt_count = (pj.attempt_count or 0) + 1
            pj.message = (note or pj.message or "")[:255]
            pj.save(update_fields=["attempt_count", "message", "updated_at"])

        # 2) 统一加锁顺序 + 固定排序：放入内层原子执行“重活”
        try:
            affected = self._handle_atomic(
                task=task,
                scans=scans,
                now=now,
                batch_no=batch_no,
                note=note,
                by_user=by_user,
                pj=pj,
                OK=OK,
                REJECTED=REJECTED,
            )
        except Exception as e:
            # 外层非原子写 PJ 失败状态（避免在回滚态里 save）
            pj.status = "FAILED"
            log.exception("tasking.post.failed %s", ctx_text, extra=ctx)
            pj.message = (str(e) or "FAILED")[:255]
            pj.save(update_fields=["status", "message", "updated_at"])
            raise

        # 3) 成功：外层非原子写 PJ 成功状态
        pj.status = "POSTED"
        pj.message = (note or pj.message or "POSTED")[:255]
        now_ts = now or timezone.now()
        if _has_field(PostingJournal, "posted_at"):
            pj.posted_at = now_ts
            pj.save(update_fields=["status", "message", "posted_at", "updated_at"])
        else:
            pj.save(update_fields=["status", "message", "updated_at"])

        # 4) 计费
        try:
            from allapp.billing import services as billing_services
            billing_services.accrue_for_posting(task, pj, by_user=by_user)
        except Exception as e:
            log.warning("tasking.post.billing_accrue_failed %s err=%s", ctx_text, e, extra=ctx)

        return affected

    def _create_putaway_task(self, receive_task: WmsTask, by_user, now_ts):
        """
        自动创建上架任务草稿
        """
        # 创建上架任务草稿
        # 生成任务号（用你项目已有的 DocSequence）
        from allapp.tasking.models import WmsTask
        # 保险：非收货任务不派生上架任务
        if receive_task.task_type != WmsTask.TaskType.RECEIVE:
            return 0
        task_no = DocSequence.next_code(
            doc_type="SJ",
            warehouse=receive_task.warehouse,
            owner=receive_task.owner,
            biz_date=timezone.now(),  # You can use the current date for business date
        )

        putaway_task = WmsTask.objects.create(
            task_no=task_no,
            task_type=WmsTask.TaskType.PUTAWAY,  # 设置为上架任务
            owner_id=receive_task.owner_id,
            warehouse_id=receive_task.warehouse_id,
            status=WmsTask.Status.DRAFT,  # 设置为草稿状态
            created_by=by_user,
            created_at=now_ts,
            updated_at=now_ts,
            # review_status=WmsTask.ReviewStatus.NOT_READY,
            # posting_status=WmsTask.PostingStatus.NOT_READY,
            posting_note="由收货任务自动生成的上架任务草稿",
        )
        putaway_ctx, putaway_text = build_log_payload(task=putaway_task, user=by_user)
        receive_ctx, receive_text = build_log_payload(task=receive_task, user=by_user)
        log.info("tasking.putaway_task.created %s source_task_id=%s", putaway_text, receive_task.id, extra=putaway_ctx)

        # 获取过账数据：从 InventoryTransaction 获取已过账商品及数量
        transactions = InventoryTransaction.objects.filter(
            src_model="WmsTask", src_id=receive_task.id, tx_type="RECEIVE"
        )

        # 遍历所有相关的库存事务，创建对应的上架任务行
        line_count = 0
        for tx in transactions:
            product_id = tx.product_id  # 获取商品ID
            qty = tx.qty_delta  # 获取过账数量（即收货数量）
            location_id = tx.location_id  # 获取库位ID（从事务中获取）
            # 创建上架任务行
            WmsTaskLine.objects.create(
                task=putaway_task,
                product_id=product_id,
                qty_plan=qty,  # 上架数量等同于收货数量
                from_location_id=location_id,  # 从收货库位出发
                to_location=None,  # 上架目标库位待补充，可能根据策略计算
                status=WmsTaskLine.Status.DRAFT,  # 初始状态为待处理
            )
            line_count += 1
        log.info(
            "tasking.putaway_task.lines_created %s source_task_no=%s line_count=%s",
            putaway_text,
            receive_task.task_no,
            line_count,
            extra=putaway_ctx,
        )
        log.info("tasking.post.receive_to_putaway_linked %s putaway_task_id=%s", receive_text, putaway_task.id, extra=receive_ctx)

    @transaction.atomic
    def _handle_atomic(
        self,
        *,
        task: WmsTask,
        scans: Optional[Iterable[TaskScanLog]],
        now=None,
        batch_no: Optional[str],
        note: str,
        by_user,
        pj: PostingJournal,
        OK: str,
        REJECTED: str,
    ) -> int:
        """
        统一锁序 + 固定排序 + 调库存服务 + 扫描打点 + 落账任务头。
        要点：
        - 候选扫描：仅 status=OK 且 posted_at IS NULL；
        - 若无候选 → 抛错回滚；
        - 库存服务无交易 → 抛错回滚；
        - 打点只更新 posted_at / posting_batch / posting_journal（不改 status）。
        """
        now_ts = now or timezone.now()
        batch = batch_no or (timezone.now().strftime("%Y%m%d-%H%M%S-") + str(uuid4())[:8])
        ctx, ctx_text = build_log_payload(task=task, user=by_user, journal=pj, posting_batch=batch)

        from allapp.tasking.models import WmsTask
        # ① 先锁任务头（统一顺序第 1 位）
        task = (WmsTask.objects
                .select_for_update()
                .get(pk=task.id))

        log.info("tasking.post.lock_task %s", ctx_text, extra=ctx)
        # ② 再锁任务行（统一顺序第 2 位；有行就按 id 升序锁一下，保持顺序一致）
        # try:
        #     # 反向关系命名为 lines（常见写法）
        #     _ = (task.lines
        #          .select_for_update()
        #          .order_by("id"))
        # except Exception:
        #     # 若没有反向管理器，就直接按 task_id 锁
        #     from allapp.tasking.models import WmsTaskLine
        #     _ = (WmsTaskLine.objects
        #          .select_for_update()
        #          .filter(task_id=task.id)
        #          .order_by("id"))
        try:
            qs_lines = (task.lines
                        .select_for_update()
                        .order_by("id"))
        except Exception:
            from allapp.tasking.models import WmsTaskLine
            qs_lines = (WmsTaskLine.objects
                        .select_for_update()
                        .filter(task_id=task.id)
                        .order_by("id"))
        _ = list(qs_lines)  # ← 关键：强制查询，确保锁生效
        log.info("tasking.post.lock_lines %s", ctx_text, extra=ctx)

        # ③ 再锁候选扫描（统一顺序第 3 位；清空默认排序后只按 id 升序）
        if scans is None:
            scans_qs = (TaskScanLog.objects
                        .select_for_update()
                        .filter(task_id=task.id, status=OK, posted_at__isnull=True)
                        .order_by()          # 清掉 Meta 默认排序（如有）
                        .order_by("id"))     # 只用主键升序，避免锁顺序不一致
            scans_locked: List[TaskScanLog] = list(scans_qs)
        else:
            # 传入可能是列表或 QuerySet，这里统一成 id 列表后重取并加锁（保证排序/锁序一致）
            try:
                scan_ids = list(getattr(scans, "values_list")("id", flat=True))  # QuerySet
            except Exception:
                scan_ids = [getattr(s, "id", s) for s in scans]  # 兼容列表/可迭代
            if not scan_ids:
                scans_locked = []
            else:
                scans_locked = list(
                    TaskScanLog.objects
                    .select_for_update()
                    .filter(id__in=scan_ids)
                    .order_by()          # 清默认排序
                    .order_by("id")      # 主键升序
                )
        log.info("tasking.post.lock_scans %s candidate_count=%s", ctx_text, len(scans_locked), extra=ctx)
        # 过滤：仅保留 OK 且未被拒绝且未打点的扫描
        scans_ok: List[TaskScanLog] = [
            s for s in scans_locked
            if getattr(s, "status", None) == OK
            and getattr(s, "review_status", None) != REJECTED
            and getattr(s, "posted_at", None) is None
        ]
        if not scans_ok:
            raise ValueError("无可过账明细（需要 OK 且未过账的 TaskScanLog）。")

        log.info(
            "tasking.post.scans_ready %s scan_count=%s first_scan_id=%s",
            ctx_text,
            len(scans_ok),
            scans_ok[0].id if scans_ok else None,
            extra=ctx,
        )

        log.info("tasking.post.inventory_call %s", ctx_text, extra=ctx)

        # ④ 调用库存服务做真实入账
        # result = inv_services.post_task(task=task, user=by_user)
        result = inv_services.post_task(task=task, user=by_user, scans=scans_ok)

        log.info("tasking.post.inventory_result %s result=%r", ctx_text, result, extra=ctx)

        # ⑤ 严格校验返回：无交易即失败回滚（根据你们 services 的返回结构尽量取“受影响条数”）
        affected = 0
        if isinstance(result, dict):
            affected = int(
                result.get("affected_tx_count")
                or result.get("tx_count")
                or result.get("created_transactions")
                or (result.get("ok") and 1)
                or 0
            )
        else:
            # 老实现可能返回 True/False
            affected = 1 if result else 0
        if affected <= 0:
            raise ValueError("库存过账未生成任何交易（InventoryTransaction）。")

        # ⑥ 扫描批量打点（不改 status；配合你的 ck_tscan_status_ok 约束）
        for s in scans_ok:
            s.posting_journal_id = getattr(pj, "id", None)
            s.posted_at = now_ts
            s.posting_batch = batch
        TaskScanLog.objects.bulk_update(scans_ok, ["posting_journal", "posted_at", "posting_batch"])

        # ⑦ 任务头落账字段回填
        posted_status = getattr(getattr(WmsTask, "PostingStatus", None), "POSTED", "POSTED")
        updates: Dict[str, Any] = {}
        if getattr(task, "posting_status", None) != posted_status:
            updates["posting_status"] = posted_status
        by_user_id = getattr(by_user, "pk", None) if by_user is not None else None
        if by_user_id is not None and getattr(task, "posted_by_id", None) != by_user_id:
            updates["posted_by_id"] = by_user_id
        if not getattr(task, "posted_at", None):
            updates["posted_at"] = now_ts
        if note:
            updates["posting_note"] = note[:200]
        if updates:
            for k, v in updates.items():
                setattr(task, k, v)
            task.save(update_fields=list(updates.keys()))

        log.info(
            "tasking.post.task_header_updated %s posting_status=%s",
            ctx_text,
            getattr(task, "posting_status", None),
            extra=ctx,
        )

        # self._create_putaway_task(task, by_user, now_ts)
        # … 成功过账后 …
        # from allapp.tasking.models import WmsTask
        # 仅“收货任务”且本次确实写出了分录，才派生上架任务
        if getattr(task, "task_type", None) == WmsTask.TaskType.RECEIVE and affected > 0:
            log.info("tasking.post.putaway_task_triggered %s affected=%s", ctx_text, affected, extra=ctx)
            self._create_putaway_task(task, by_user, now_ts)

        return affected
