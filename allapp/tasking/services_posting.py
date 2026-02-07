# allapp/tasking/services_posting.py
from django.db import transaction, IntegrityError
from django.utils import timezone
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from allapp.tasking.models import WmsTask, WmsTaskLine, PostingJournal
from allapp.tasking.posting_exec import execute_posting_handler

TX_POST = "POST"        # 过账
TX_REVERSE = "REVERSE"  # 冲销（可选）
TX_CANCEL = "CANCEL"    # 作废（可选）

STATUS_PENDING = "PENDING"
STATUS_POSTED  = "POSTED"
STATUS_FAILED  = "FAILED"

def _as_wh_mgr(user):
    return user and (user.is_superuser or user.has_perm("tasking.taskconfirm_as_wh_manager"))

def _get_or_create_journal_locked(*, src_model: str, src_id: int, tx_type: str) -> PostingJournal:
    """
    幂等 + 并发控制：
    - 尝试 get_or_create 一条日记账（PENDING）
    - 随后对该行做 select_for_update() 加锁，确保同一时刻仅一个事务执行
    - 若已是 POSTED，直接返回给上层判断“已处理”
    """
    with transaction.atomic():
        try:
            j, created = PostingJournal.objects.get_or_create(
                src_model=src_model, src_id=src_id, tx_type=tx_type,
                defaults={"status": STATUS_PENDING, "message": "", "attempt_count": 0},
            )
        except IntegrityError:
            # 并发 get_or_create 碰撞，退而求其次再取
            j = PostingJournal.objects.get(src_model=src_model, src_id=src_id, tx_type=tx_type)
        # 行锁：保证只有拿到锁的事务能修改这条日记账
        j = PostingJournal.objects.select_for_update().get(pk=j.pk)
        return j


# allapp/tasking/services_posting.py (续)
@transaction.atomic
def post_task(task_id: int, *, by_user=None, note: str = "过账"):
    """
    任务头过账（幂等）：
    - 权限：仓管经理
    - 状态门控：任务必须 COMPLETED 且 REVIEW=APPROVED
    - 幂等：PostingJournal(src=WmsTask, id, POST) 保证一次
    - 失败会把 Journal 标记 FAILED 并累计 attempt_count
    - 成功回写：WmsTask.posting_status/by/at/note
    """
    if not _as_wh_mgr(by_user):
        raise PermissionDenied("无过账权限。")

    # 1) 锁住任务头，读当前状态
    task = WmsTask.objects.select_for_update().get(pk=task_id)

    # 2) 审核/状态门控
    if task.status != WmsTask.Status.COMPLETED:
        raise ValidationError("任务未完工，不能过账。")
    if task.review_status != WmsTask.ReviewStatus.APPROVED:
        raise ValidationError("未审核通过，不能过账。")

    # 3) 幂等日记账（锁）
    j = _get_or_create_journal_locked(src_model="WmsTask", src_id=task_id, tx_type=TX_POST)

    # 已过账则直接返回（幂等）
    if j.status == STATUS_POSTED:
        # 确保任务头同步
        if task.posting_status != WmsTask.PostingStatus.POSTED:
            task.posting_status = WmsTask.PostingStatus.POSTED
            task.posted_by = by_user
            task.posted_at = timezone.now()
            task.posting_note = note or (j.message or "")
            task.save(update_fields=["posting_status", "posted_by", "posted_at", "posting_note"])
        return {"ok": True, "tx_created": 0, "journal": j.pk, "status": j.status}

    # 4) 执行器执行 + 写回
    try:
        j.attempt_count += 1
        j.status = STATUS_PENDING
        j.message = (note or "")[:255]
        j.save(update_fields=["attempt_count", "status", "message", "updated_at"])

        created = execute_posting_handler(task=task, note=note or "过账")

        j.status = STATUS_POSTED
        j.message = f"OK: created={created}"[:255]
        j.save(update_fields=["status", "message", "updated_at"])

        task.posting_status = WmsTask.PostingStatus.POSTED
        task.posted_by = by_user
        task.posted_at = timezone.now()
        task.posting_note = note or ""
        task.save(update_fields=["posting_status", "posted_by", "posted_at", "posting_note"])

        return {"ok": True, "tx_created": created, "journal": j.pk, "status": j.status}

    except Exception as e:
        j.status = STATUS_FAILED
        j.message = f"ERR: {e}"[:255]
        j.attempt_count += 0  # 已在前面+1，失败不再重复加
        j.save(update_fields=["status", "message", "updated_at"])

        task.posting_status = WmsTask.PostingStatus.FAILED
        task.posted_by = by_user
        task.posted_at = timezone.now()
        task.posting_note = f"{note or ''} {e}"[:255]
        task.save(update_fields=["posting_status", "posted_by", "posted_at", "posting_note"])

        raise
