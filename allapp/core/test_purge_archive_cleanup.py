from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.utils import timezone

from allapp.accounts.models import AuditEvent
from allapp.baseinfo.models import Owner
from allapp.core import business_data_purge as purge
from allapp.reports.models import OwnerArchiveJob
from allapp.reports.owner_archive_jobs import discard_archive_artifacts


def create_artifacts(root, job_id):
    files = [root / f"{job_id}.xlsx", root / f"{job_id}.part"]
    scratch = root / f"{job_id}.work"
    scratch.mkdir()
    files.append(scratch / "sheet.xml")
    for path in files:
        path.write_bytes(b"fixture")
    return [*files, scratch]


@pytest.fixture
def archive_setup(settings, tmp_path, transactional_db):
    root = tmp_path / "archives"
    root.mkdir()
    settings.OWNER_ARCHIVE_ROOT = root
    operator = get_user_model().objects.create_superuser(username="purge-archives")
    owner = Owner.objects.create(name="Archive Purge Owner", code="ARC-PURGE")
    job = OwnerArchiveJob.objects.create(owner=owner, user=operator, expires_at=timezone.now())
    return root, job, create_artifacts(root, job.pk)


def run_command(job, command, *args):
    call_command(
        command,
        *args,
        "--confirm-target",
        purge.canonical_target(connection),
        "--operator",
        job.user.username,
        "--maintenance-confirmed",
        stdout=StringIO(),
    )


@pytest.mark.parametrize("command", ["purge_business_data", "purge_business_data_new"])
def test_dry_run_then_committed_cleanup(archive_setup, command):
    root, job, files = archive_setup
    neighbors = create_artifacts(root, job.pk + 1)
    call_command(command, "--dry-run", stdout=StringIO())
    assert OwnerArchiveJob.objects.filter(pk=job.pk).exists()
    assert all(path.exists() for path in files)
    assert not AuditEvent.objects.filter(module="core.operations").exists()

    run_command(job, command, "--execute", "--backup-reference", "archive-purge-test")
    assert not OwnerArchiveJob.objects.filter(pk=job.pk).exists()
    assert all(not path.exists() for path in files)
    assert all(path.exists() for path in neighbors)
    audit = AuditEvent.objects.get(action="PURGE_ARCHIVES_CLEANED")
    files[0].write_bytes(b"later")
    run_command(job, command, "--retry-archive-cleanup", audit.object_id)
    assert files[0].read_bytes() == b"later"


def test_rollback_preserves_archive_artifacts(archive_setup, monkeypatch):
    _, job, files = archive_setup
    delete_table = purge._delete_table

    def fail_after_archive_delete(cursor, table):
        count = delete_table(cursor, table)
        if table == connection.ops.quote_name(OwnerArchiveJob._meta.db_table):
            raise RuntimeError("abort transaction")
        return count

    monkeypatch.setattr(purge, "_delete_table", fail_after_archive_delete)
    with pytest.raises(CommandError, match="回滚"):
        run_command(job, "purge_business_data", "--execute", "--backup-reference", "fixture")
    assert OwnerArchiveJob.objects.filter(pk=job.pk).exists()
    assert all(path.exists() for path in files)
    assert not AuditEvent.objects.filter(action="BUSINESS_DATA_PURGE", succeeded=True).exists()


def test_retry_partial_cleanup_uses_original_job_list(archive_setup, monkeypatch):
    root, job, files = archive_setup

    def fail_after_first_file(directory, job_id):
        (directory / f"{job_id}.xlsx").unlink()
        raise OSError("volume unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(purge, "discard_archive_artifacts", fail_after_first_file)
        with pytest.raises(CommandError, match="已提交"):
            run_command(job, "purge_business_data", "--execute", "--backup-reference", "fixture")
    assert not OwnerArchiveJob.objects.filter(pk=job.pk).exists()
    assert all(path.exists() for path in files[1:])
    audit = AuditEvent.objects.get(action="BUSINESS_DATA_PURGE")
    assert audit.succeeded
    assert audit.metadata["archive_cleanup"]["job_ids"] == [job.pk]
    new_job = OwnerArchiveJob.objects.create(
        owner=job.owner, user=job.user, expires_at=timezone.now()
    )
    later_files = create_artifacts(root, new_job.pk)
    retry_args = ("--retry-archive-cleanup", str(audit.pk))
    with pytest.raises(CommandError, match="清理模式"):
        run_command(job, "purge_business_data_new", *retry_args)
    detached = root.with_name("detached")
    root.rename(detached)
    with pytest.raises(CommandError, match="挂载"):
        run_command(job, "purge_business_data", *retry_args)
    detached.rename(root)
    restored = OwnerArchiveJob.objects.create(
        pk=job.pk, owner=job.owner, user=job.user, expires_at=timezone.now()
    )
    with pytest.raises(CommandError, match="有效的归档任务"):
        run_command(job, "purge_business_data", *retry_args)
    assert all(path.exists() for path in files[1:])
    restored.delete()
    run_command(job, "purge_business_data", *retry_args)
    assert all(not path.exists() for path in files)
    assert all(path.exists() for path in later_files)
    assert OwnerArchiveJob.objects.filter(pk=new_job.pk).exists()
    assert AuditEvent.objects.filter(
        action="PURGE_ARCHIVES_CLEANED", object_id=str(audit.pk)
    ).exists()


def test_active_worker_blocks_purge(archive_setup):
    _, job, files = archive_setup
    holder = connection.copy(alias="archive_worker_holder")
    try:
        with holder.cursor() as cursor:
            cursor.execute(
                "SELECT GET_LOCK(CONCAT('owner_archive:', LEFT(SHA2(DATABASE(),256),40)), 0)"
            )
            assert cursor.fetchone()[0] == 1
        with pytest.raises(CommandError, match="归档工作进程"):
            run_command(job, "purge_business_data", "--execute", "--backup-reference", "fixture")
        assert OwnerArchiveJob.objects.filter(pk=job.pk).exists()
        assert all(path.exists() for path in files)
    finally:
        holder.close()  # MySQL releases the named lock when the connection closes.


def test_cleanup_never_follows_scratch_symlink(tmp_path):
    root = tmp_path / "private"
    root.mkdir()
    neighbors = create_artifacts(tmp_path, 12)
    scratch = root / "15.work"
    scratch.symlink_to(neighbors[-1], target_is_directory=True)
    discard_archive_artifacts(root, 15)
    assert not scratch.is_symlink()
    assert all(path.exists() for path in neighbors)


@pytest.mark.parametrize("command", ["purge_business_data", "purge_business_data_new"])
def test_close_evidence_can_be_retried_after_commit(
    archive_setup, settings, tmp_path, monkeypatch, command
):
    from allapp.inventory.daily_close_storage import PreparedFile, evidence_path, write_evidence
    from allapp.inventory.models import (
        InventoryCloseAttachment,
        InventoryCloseReview,
        InventoryDailyClose,
    )
    from allapp.locations.models import Warehouse

    _, job, _ = archive_setup
    settings.PRIVATE_CLOSE_ROOT = tmp_path / "close"
    close = InventoryDailyClose.objects.create(
        owner=job.owner,
        warehouse=Warehouse.objects.create(code="PVC", name="凭据仓"),
        business_date=timezone.now().date(),
    )
    review = InventoryCloseReview.objects.create(close=close, actor=job.user, action="TEST")
    pdf = PreparedFile("count.pdf", "pdf", "application/pdf", b"%PDF-1.4\n%%EOF")
    key = write_evidence(close.pk, pdf)
    attachment = InventoryCloseAttachment.objects.create(
        review=review,
        storage_key=key,
        file_name=pdf.file_name,
        content_type=pdf.content_type,
        size=len(pdf.data),
    )
    path = evidence_path(key)
    neighbor = evidence_path(write_evidence(close.pk, pdf))
    call_command(command, "--dry-run", stdout=StringIO())
    assert path.is_file() and InventoryCloseAttachment.objects.filter(pk=attachment.pk).exists()
    # A transaction that cannot publish its success audit must preserve both row and file.
    real_record = purge.record_audit_event

    def fail_commit(**kwargs):
        if kwargs.get("succeeded"):
            raise RuntimeError("close fixture rollback")
        return real_record(**kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(purge, "record_audit_event", fail_commit)
        with pytest.raises(CommandError, match="回滚"):
            run_command(job, command, "--execute", "--backup-reference", "close-proof")
    assert path.is_file() and InventoryCloseAttachment.objects.filter(pk=attachment.pk).exists()
    # Simulate losing the filesystem after the business transaction commits.
    with monkeypatch.context() as patch:
        patch.setattr(
            purge, "retry_purge_archive_cleanup", lambda **kw: (_ for _ in ()).throw(OSError())
        )
        with pytest.raises(CommandError, match="已提交"):
            run_command(job, command, "--execute", "--backup-reference", "close-proof")
    assert path.is_file() and not InventoryCloseAttachment.objects.filter(pk=attachment.pk).exists()
    audit = AuditEvent.objects.get(
        module="core.operations", succeeded=True, metadata__backup_reference="close-proof"
    )
    assert audit.metadata["close_evidence_cleanup"]["keys"] == [key]
    run_command(job, command, "--retry-archive-cleanup", str(audit.pk))
    assert not path.exists() and neighbor.is_file()
