"""Cross-domain, append-only audit hooks for facts that already carry actors."""

import logging

from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.db.models.signals import post_save
from django.dispatch import receiver

from allapp.inventory.models import InventoryTransaction
from allapp.tasking.models import TaskStatusLog

from .audit import record_audit_event


logger = logging.getLogger(__name__)


def _safe_auth_audit(**kwargs):
    """Authentication must not become unavailable when the audit store is down."""

    try:
        record_audit_event(**kwargs)
    except Exception:  # pragma: no cover - defensive logging path
        logger.exception("authentication audit write failed: %s", kwargs.get("action"))


@receiver(user_logged_in, dispatch_uid="audit_session_login")
def audit_session_login(sender, request, user, **kwargs):
    _safe_auth_audit(
        action="LOGIN",
        module="authentication.session",
        request=request,
        user=user,
    )


@receiver(user_logged_out, dispatch_uid="audit_session_logout")
def audit_session_logout(sender, request, user, **kwargs):
    _safe_auth_audit(
        action="LOGOUT",
        module="authentication.session",
        request=request,
        user=user,
    )


@receiver(user_login_failed, dispatch_uid="audit_session_login_failed")
def audit_session_login_failed(sender, credentials, request, **kwargs):
    # Never persist passwords, tokens, or the complete credentials mapping.
    attempted_identity = credentials.get("username") or credentials.get("email") or ""
    _safe_auth_audit(
        action="LOGIN_FAILED",
        module="authentication.session",
        request=request,
        succeeded=False,
        metadata={"attempted_identity": str(attempted_identity)[:150]},
    )


@receiver(post_save, sender=TaskStatusLog, dispatch_uid="audit_task_status_log")
def audit_task_status_log(sender, instance, created, **kwargs):
    if not created:
        return
    task = instance.task
    record_audit_event(
        action="TASK_STATUS_CHANGE",
        module="tasking",
        user=instance.changed_by,
        obj=task,
        owner_id=task.owner_id,
        warehouse_id=task.warehouse_id,
        before={"status": instance.old_status},
        after={"status": instance.new_status},
        metadata={"status_log_id": instance.pk, "note": instance.note},
    )


@receiver(post_save, sender=InventoryTransaction, dispatch_uid="audit_inventory_transaction")
def audit_inventory_transaction(sender, instance, created, **kwargs):
    if not created:
        return
    record_audit_event(
        action="INVENTORY_POST",
        module="inventory",
        user=instance.created_by,
        obj=instance,
        owner_id=instance.owner_id,
        warehouse_id=instance.warehouse_id,
        after={
            "tx_type": instance.tx_type,
            "qty_delta": str(instance.qty_delta),
            "product_id": instance.product_id,
            "location_id": instance.location_id,
            "batch_no": instance.batch_no,
            "posted_at": instance.posted_at.isoformat() if instance.posted_at else None,
        },
        metadata={
            "source_model": instance.src_model,
            "source_id": instance.src_id,
            "source_line_id": instance.src_line_id,
            "source_no": instance.src_no,
            "posting_batch": instance.posting_batch,
        },
    )
