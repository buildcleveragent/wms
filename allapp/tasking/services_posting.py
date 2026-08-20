"""Compatibility wrapper for the canonical tasking/inventory posting entrypoint."""

from django.core.exceptions import PermissionDenied, ValidationError

from allapp.inventory.models import PostingJournal
from allapp.tasking.models import WmsTask
from allapp.tasking.services import _run_posting_handler


def _as_warehouse_manager(user):
    return bool(user and (user.is_superuser or user.has_perm("tasking.taskconfirm_as_wh_manager")))


def post_task(task_id: int, *, by_user=None, note: str = "过账"):
    """Validate orchestration concerns, then delegate to the sole handler.

    Inventory atomicity and the PENDING/POSTED journal lifecycle belong to
    ``inventory.services.post_task``.  The handler persists FAILED only after
    that inventory transaction has rolled back.
    """

    if not _as_warehouse_manager(by_user):
        raise PermissionDenied("无过账权限。")

    try:
        task = WmsTask.objects.get(pk=task_id)
    except WmsTask.DoesNotExist as exc:
        raise ValidationError("任务不存在。") from exc
    if task.status != WmsTask.Status.COMPLETED:
        raise ValidationError("任务未完工，不能过账。")
    if task.review_status != WmsTask.ReviewStatus.APPROVED:
        raise ValidationError("未审核通过，不能过账。")

    existing = PostingJournal.objects.filter(
        src_model="WmsTask", src_id=task.pk, tx_type="POST", status="POSTED"
    ).first()
    if task.posting_status == WmsTask.PostingStatus.POSTED and existing:
        return {
            "ok": True,
            "tx_created": 0,
            "journal": existing.pk,
            "status": existing.status,
        }

    created = _run_posting_handler(task.pk, by_user=by_user, note=note or "过账")
    task.refresh_from_db()
    journal = PostingJournal.objects.get(src_model="WmsTask", src_id=task.pk, tx_type="POST")
    return {
        "ok": journal.status == "POSTED",
        "tx_created": int(created or 0),
        "journal": journal.pk,
        "status": journal.status,
    }
