# allapp/tasking/plugins/handlers.py
from __future__ import annotations
from typing import Any, Dict, Iterable, Optional
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import transaction
from django.utils import timezone

from allapp.tasking.models import WmsTask, TaskScanLog,WmsTaskLine
from allapp.inventory.models import PostingJournal, InventoryTransaction
from allapp.inventory import services as inv_services
from allapp.core.models import DocSequence
from allapp.core.utils.log_context import build_log_payload

import logging
log = logging.getLogger(__name__)

_ALREADY_POSTED = object()

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
    - 加锁顺序：WmsTask -> PostingJournal -> WmsTaskLine -> TaskScanLog(order_by id)
      （保留对 TaskLine 的锁，确保并发下行→扫的拓扑顺序稳定；不再做行级过账）
    - 落账入口：inventory.services.post_task(...)（仅扫描驱动，不调用任何行级 post_*）
    - 扫描打点：由库存服务在同一事务内精确写入并校验数量
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
            )
        except Exception as e:
            # 外层非原子写失败审计；条件更新避免并发重试覆盖已成功状态。
            log.exception("tasking.post.failed %s", ctx_text, extra=ctx)
            failure_message = (str(e) or "FAILED")[:255]
            failed_at = timezone.now()
            PostingJournal.objects.filter(pk=pj.pk).exclude(status="POSTED").update(
                status="FAILED",
                message=failure_message,
                updated_at=failed_at,
            )
            failure_note = f"{note or '过账'}失败：{str(e) or '未知错误'}"[:200]
            task_updates = {
                "posting_status": WmsTask.PostingStatus.FAILED,
                "posting_note": failure_note,
                "updated_at": failed_at,
            }
            by_user_id = getattr(by_user, "pk", None)
            if by_user_id is not None:
                task_updates["posted_by_id"] = by_user_id
            WmsTask.objects.filter(pk=task.pk).exclude(
                posting_status=WmsTask.PostingStatus.POSTED
            ).update(**task_updates)
            raise

        if affected is _ALREADY_POSTED:
            log.info("tasking.post.already_posted %s", ctx_text, extra=ctx)
            return 0

        # 成功状态由内层事务与库存变更一起提交，这里只重读供后续计费使用。
        pj.refresh_from_db()

        # 4) 计费
        # try:
        #     from allapp.billing import services as billing_services
        #     billing_services.accrue_for_posting(task, pj, by_user=by_user)
        # except Exception as e:
        #     log.warning("tasking.post.billing_accrue_failed %s err=%s", ctx_text, e, extra=ctx)

        # 4) 计费
        try:
            from allapp.billing import services as billing_services
            # from allapp.billing.services.accrual import AUTO_REVIEW_ORDER_PROCESSING_METHODS
            #
            # billing_services.accrue_for_posting(task, pj, by_user=by_user)
            #
            # # 当前业务下：PICK → REVIEW → 订单完成。
            # # REVIEW 过账后自动触发订单处理费。
            # if task.task_type == WmsTask.TaskType.REVIEW:
            #     billing_services.accrue_order_processing_for_task(
            #         task,
            #         pj,
            #         by_user=by_user,
            #         allowed_methods=AUTO_REVIEW_ORDER_PROCESSING_METHODS,
            #     )
            from allapp.billing.services.accrual import AUTO_REVIEW_ORDER_PROCESSING_METHODS

            billing_services.accrue_for_posting(task, pj, by_user=by_user)

            should_accrue_order_processing = (
                    task.task_type == WmsTask.TaskType.REVIEW
                    or (
                            task.task_type == WmsTask.TaskType.PICK
                            and task.review_status == WmsTask.ReviewStatus.APPROVED
                    )
            )

            if should_accrue_order_processing:
                billing_services.accrue_order_processing_for_task(
                    task,
                    pj,
                    by_user=by_user,
                    allowed_methods=AUTO_REVIEW_ORDER_PROCESSING_METHODS,
                )
        except Exception as e:
            log.error("tasking.post.billing_accrue_failed %s err=%s", ctx_text, e, extra=ctx)
            # 在 PJ 上标记 billing 失败，便于 billing_retry_failed 命令后续补算
            try:
                pj.message = f"{pj.message}|BILLING_FAILED:{str(e)[:100]}"[:255]
                pj.save(update_fields=["message", "updated_at"])
            except Exception:
                pass


        return affected

    def _create_putaway_task(self, receive_task: WmsTask, by_user, now_ts):
        """
        按已过账收货流水幂等创建一张待发布上架任务。
        """
        # 保险：非收货任务不派生上架任务
        if receive_task.task_type != WmsTask.TaskType.RECEIVE:
            return None

        source = {
            "source_app": "tasking",
            "source_model": "WmsTask",
            "source_pk": str(receive_task.pk),
        }
        existing = (
            WmsTask.objects.filter(
                task_type=WmsTask.TaskType.PUTAWAY,
                **source,
            )
            .exclude(status=WmsTask.Status.CANCELLED)
            .first()
        )
        if existing:
            return existing

        transactions = list(
            InventoryTransaction.objects.filter(
                src_model="WmsTask",
                src_id=receive_task.id,
                tx_type="RECEIVE",
                qty_delta__gt=0,
            )
            .select_related("product", "location")
            .order_by("id")
        )
        if not transactions:
            log.warning(
                "tasking.putaway_task.skipped_no_transactions receive_task_id=%s",
                receive_task.id,
            )
            return None

        task_no = DocSequence.next_code(
            doc_type="SJ",
            warehouse=receive_task.warehouse,
            owner=receive_task.owner,
            biz_date=now_ts.date(),
        )

        putaway_task = WmsTask.objects.create(
            task_no=task_no,
            task_type=WmsTask.TaskType.PUTAWAY,
            owner_id=receive_task.owner_id,
            warehouse_id=receive_task.warehouse_id,
            status=WmsTask.Status.READY,
            created_by=by_user,
            created_at=now_ts,
            updated_at=now_ts,
            ref_no=(receive_task.task_no or "")[:60],
            review_status=WmsTask.ReviewStatus.NOT_READY,
            posting_status=WmsTask.PostingStatus.NOT_READY,
            posting_note="由收货任务自动生成，待仓库管理员分配目标库位并发布",
            **source,
        )
        putaway_ctx, putaway_text = build_log_payload(task=putaway_task, user=by_user)
        receive_ctx, receive_text = build_log_payload(task=receive_task, user=by_user)
        log.info(
            "tasking.putaway_task.created %s source_task_id=%s",
            putaway_text,
            receive_task.id,
            extra=putaway_ctx,
        )

        # 遍历所有相关的库存事务，创建对应的上架任务行
        line_count = 0
        for tx in transactions:
            WmsTaskLine.objects.create(
                task=putaway_task,
                product_id=tx.product_id,
                qty_plan=tx.qty_delta,
                from_location_id=tx.location_id,
                to_location=None,
                status=WmsTaskLine.Status.READY,
                src_model="inventory.InventoryTransaction",
                src_id=tx.pk,
                plan_meta={
                    "lot_no": tx.batch_no or "",
                    "mfg_date": tx.production_date.isoformat()
                    if tx.production_date
                    else "",
                    "exp_date": tx.expiry_date.isoformat() if tx.expiry_date else "",
                    "serial_no": tx.serial_no or "",
                },
            )
            line_count += 1
        log.info(
            "tasking.putaway_task.lines_created %s source_task_no=%s line_count=%s",
            putaway_text,
            receive_task.task_no,
            line_count,
            extra=putaway_ctx,
        )
        log.info(
            "tasking.post.receive_to_putaway_linked %s putaway_task_id=%s",
            receive_text,
            putaway_task.id,
            extra=receive_ctx,
        )
        from allapp.accounts.audit import record_audit_event

        record_audit_event(
            action="inbound.putaway.create",
            module="inbound",
            user=by_user,
            obj=putaway_task,
            before={},
            after={
                "status": putaway_task.status,
                "source_receive_task_id": receive_task.pk,
            },
            metadata={"line_count": line_count},
        )
        return putaway_task

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
    ) -> int:
        """
        统一锁序 + 固定排序 + 调库存服务 + 扫描打点 + 落账任务头。
        要点：
        - 未显式传入扫描时，在锁内只选择当前任务可过账的 OK 事实；
        - 显式传入时按 ID 重取并严格校验，不静默过滤非法记录；
        - 库存服务无交易 → 抛错回滚；
        - 扫描打点由库存服务完成并校验更新数量。
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
        # ② 再锁任务级日记账，并在锁内重新判定幂等状态。
        pj = PostingJournal.objects.select_for_update().get(pk=pj.pk)
        posted_status = getattr(
            getattr(WmsTask, "PostingStatus", None), "POSTED", "POSTED"
        )
        task_is_posted = getattr(task, "posting_status", None) == posted_status
        journal_is_posted = pj.status == "POSTED"
        if task_is_posted and journal_is_posted:
            return _ALREADY_POSTED
        if task.posting_status != pj.status:
            raise ValidationError(
                "任务与过账日记账状态不一致，已拒绝自动修复或重复过账。"
            )
        retryable_statuses = {
            WmsTask.PostingStatus.PENDING,
            WmsTask.PostingStatus.FAILED,
        }
        if task.posting_status not in retryable_statuses:
            raise ValidationError(
                f"过账状态 {task.posting_status} 不允许执行库存过账。"
            )
        log.info("tasking.post.lock_journal %s", ctx_text, extra=ctx)

        # ③ 再锁任务行（有行就按 id 升序锁一下，保持顺序一致）
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
        locked_lines = list(qs_lines)  # ← 关键：强制查询，确保锁生效
        valid_line_ids = [line.id for line in locked_lines]
        log.info("tasking.post.lock_lines %s", ctx_text, extra=ctx)

        # ④ 再锁候选扫描（清空默认排序后只按 id 升序）
        if scans is None:
            rejected = getattr(
                TaskScanLog.ReviewStatus, "REJECTED", "REJECTED"
            )
            scans_locked = list(
                TaskScanLog.objects.select_for_update()
                .filter(
                    task_id=task.id,
                    task_line_id__in=valid_line_ids,
                    status=TaskScanLog.ScanStatus.OK,
                    posted_at__isnull=True,
                    posting_journal_id__isnull=True,
                    posting_batch__isnull=True,
                )
                .exclude(review_status=rejected)
                .order_by()
                .order_by("id")
            )
            scan_ids = [scan.id for scan in scans_locked]
        else:
            supplied_scans = list(scans)
            scan_ids = []
            for scan in supplied_scans:
                scan_id = getattr(scan, "pk", None)
                if scan_id is None:
                    raise ValidationError("过账扫描必须是已持久化的 TaskScanLog。")
                scan_ids.append(scan_id)
            if len(scan_ids) != len(set(scan_ids)):
                raise ValidationError("过账扫描包含重复记录。")
            scans_locked = list(
                TaskScanLog.objects.select_for_update()
                .filter(id__in=scan_ids)
                .order_by()
                .order_by("id")
            )
            if len(scans_locked) != len(scan_ids):
                raise ValidationError("部分过账扫描不存在。")
        if not scan_ids:
            raise ValidationError("无可过账扫描。")
        log.info("tasking.post.lock_scans %s candidate_count=%s", ctx_text, len(scans_locked), extra=ctx)

        log.info(
            "tasking.post.scans_ready %s scan_count=%s first_scan_id=%s",
            ctx_text,
            len(scans_locked),
            scans_locked[0].id,
            extra=ctx,
        )

        log.info("tasking.post.inventory_call %s", ctx_text, extra=ctx)

        # ④ 调用库存服务做真实入账
        # result = inv_services.post_task(task=task, user=by_user)
        result = inv_services.post_task(
            task=task,
            user=by_user,
            scans=scans_locked,
            note=note,
            now=now_ts,
            batch_no=batch,
        )

        log.info("tasking.post.inventory_result %s result=%r", ctx_text, result, extra=ctx)

        # ⑤ 严格校验返回：无交易即失败回滚（根据你们 services 的返回结构尽量取“受影响条数”）
        affected = 0
        result_ok = True
        if isinstance(result, dict):
            result_ok = result.get("ok", True) is not False
            for key in ("affected_tx_count", "tx_count", "created_transactions"):
                if key in result and result[key] is not None:
                    affected = int(result[key])
                    break
            else:
                affected = 1 if result.get("ok") else 0
        else:
            # 老实现可能返回 True/False
            affected = 1 if result else 0
        zero_count_allowed = (
            getattr(task, "task_type", None) == WmsTask.TaskType.COUNT and affected == 0
        )
        if not result_ok or (affected <= 0 and not zero_count_allowed):
            raise ValueError("库存过账未生成任何交易（InventoryTransaction）。")

        # ⑥ 任务头补充经手人、时间和备注；成功状态已由库存服务提交。
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

        audit_action = {
            WmsTask.TaskType.RECEIVE: "inbound.receive.post",
            WmsTask.TaskType.PUTAWAY: "inbound.putaway.post",
        }.get(getattr(task, "task_type", None))
        if audit_action:
            from allapp.accounts.audit import record_audit_event

            record_audit_event(
                action=audit_action,
                module="inbound",
                user=by_user,
                obj=task,
                before={"posting_status": WmsTask.PostingStatus.PENDING},
                after={"posting_status": task.posting_status},
                metadata={"affected_transactions": affected},
            )

        if getattr(task, "task_type", None) == WmsTask.TaskType.PUTAWAY:
            from allapp.inbound.services import close_inbound_order_after_putaway

            close_inbound_order_after_putaway(task, by_user=by_user)

        # 库存服务必须在同一事务内提交任务和日记账成功状态。
        task.refresh_from_db(fields=["posting_status"])
        pj.refresh_from_db(fields=["status"])
        if task.posting_status != posted_status or pj.status != "POSTED":
            raise ValidationError(
                "库存过账返回成功，但任务或过账日记账未处于 POSTED 状态。"
            )

        return affected
