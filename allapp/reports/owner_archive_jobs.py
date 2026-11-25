"""Private owner workbook jobs. The database lock also recovers abandoned running jobs."""

import logging
import shutil
import tempfile
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.db import connection, transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied

from allapp.accounts.access import AccessScope
from allapp.accounts.audit import record_audit_event
from allapp.baseinfo.models import Owner
from allapp.inventory.owner_permissions import inventory_capabilities

from .models import OwnerArchiveJob

logger = logging.getLogger(__name__)
TTL = timedelta(hours=24)


def can_export_archive(user, scope=None):
    scope = scope or AccessScope.for_user(user)
    return (
        scope.roles in ({"owner_manager"}, {"owner_finance"})
        and inventory_capabilities(user, scope)["can_export_owner_inventory"]
        and user.has_perms(
            (
                "reports.view_owner_operations",
                "reports.export_operations",
                "accounts.view_owner_financials",
                "inbound.view_inboundorder",
                "outbound.view_outboundorder",
                "outbound.view_assistedownershiptransfer",
            )
        )
    )


def authorized_owner(user):
    scope = AccessScope.for_user(user)
    if not can_export_archive(user, scope):
        raise PermissionDenied("需要本货主管理员或财务的完整档案导出权限。")
    return scope.single_owner_id


def archive_file(job, suffix=".xlsx"):
    root = Path(settings.OWNER_ARCHIVE_ROOT).resolve()
    if any(
        root.is_relative_to(Path(public).resolve())
        for public in (settings.MEDIA_ROOT, settings.STATIC_ROOT)
        if public
    ):
        raise ImproperlyConfigured("货主导出目录应位于独立私有目录。")
    return root / f"{job.pk}{suffix}"


def discard_published_file(sender, instance, using, **kwargs):
    path = archive_file(instance)
    transaction.on_commit(lambda: path.unlink(missing_ok=True), using=using)


@contextmanager
def private_scratch(job):
    # The dedicated, single-job worker is the only caller. openpyxl spools worksheet
    # XML through tempfile while writing. Reclaim scratch left by an interrupted run.
    scratch = archive_file(job, ".work")
    if scratch.exists():
        shutil.rmtree(scratch)
    scratch.mkdir(mode=0o700)
    original = tempfile.tempdir
    try:
        tempfile.tempdir = str(scratch)
        yield
    finally:
        tempfile.tempdir = original
        shutil.rmtree(scratch)


@transaction.atomic
def enqueue(request, dates):
    # Serialize concurrent taps from the same account, including unbounded date ranges.
    user = get_user_model().objects.select_for_update().get(pk=request.user.pk)
    owner_id = authorized_owner(user)
    filters = dict(
        owner_id=owner_id, user=user, date_from=dates.get("date_from"), date_to=dates.get("date_to")
    )
    existing = OwnerArchiveJob.objects.filter(
        **filters, status__in=["queued", "running"], expires_at__gt=timezone.now()
    ).first()
    if existing:
        return existing
    job = OwnerArchiveJob.objects.create(**filters, expires_at=timezone.now() + TTL)
    record_audit_event(
        action="EXPORT", module="owner.archive.request", request=request, obj=job, metadata=dates
    )
    return job


def validate_job(job):
    if authorized_owner(get_user_model().objects.get(pk=job.user_id)) != job.owner_id:
        raise PermissionDenied("货主范围已变更。")


def generate(job):
    from .owner_workbook import write_workbook

    temp = archive_file(job, ".part")
    final = archive_file(job)
    try:
        validate_job(job)
        final.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        OwnerArchiveJob.objects.filter(pk=job.pk).update(status="running")
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        with private_scratch(job), transaction.atomic():
            # All data readers use this connection and its first consistent read.
            owner = Owner.objects.get(pk=job.owner_id)
            temp.unlink(missing_ok=True)
            temp.touch(mode=0o600, exist_ok=False)
            write_workbook(temp, job, owner)
        with transaction.atomic():
            job = OwnerArchiveJob.objects.select_for_update().get(pk=job.pk)
            validate_job(job)
            temp.replace(final)
            OwnerArchiveJob.objects.filter(pk=job.pk).update(
                status="ready", expires_at=timezone.now() + TTL
            )
            record_audit_event(
                action="EXPORT", module="owner.archive.generated", user=job.user, obj=job
            )
    except Exception:
        logger.exception("Owner archive generation failed (job %s)", job.pk)
        final.unlink(missing_ok=True)
        OwnerArchiveJob.objects.filter(pk=job.pk).update(
            status="failed", expires_at=timezone.now() + TTL
        )
    finally:
        temp.unlink(missing_ok=True)


def run_one():
    """One worker per database; MySQL releases the named lock when the connection dies."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT GET_LOCK(CONCAT('owner_archive:', LEFT(SHA2(DATABASE(),256),40)), 0)"
        )
        if not cursor.fetchone()[0]:
            return False
        try:
            expired = OwnerArchiveJob.objects.filter(expires_at__lte=timezone.now())
            for job in expired.order_by("id")[:100]:
                archive_file(job).unlink(missing_ok=True)
                archive_file(job, ".part").unlink(missing_ok=True)
                scratch = archive_file(job, ".work")
                if scratch.exists():
                    shutil.rmtree(scratch)
                job.delete()
            job = (
                OwnerArchiveJob.objects.filter(
                    status__in=["queued", "running"], expires_at__gt=timezone.now()
                )
                .order_by("id")
                .first()
            )
            if job:
                generate(job)
            return bool(job)
        finally:
            cursor.execute(
                "SELECT RELEASE_LOCK(CONCAT('owner_archive:', LEFT(SHA2(DATABASE(),256),40)))"
            )
