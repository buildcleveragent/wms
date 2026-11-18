"""Probe errors must fail closed without changing scheduling or heartbeat state."""

from datetime import timedelta
from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import OperationalError
from django.utils import timezone

from allapp.reports.management.commands import check_boss_worker


@pytest.mark.parametrize("age", [0, 30, 120])
def test_recent_worker_heartbeat_is_healthy_even_when_automation_is_disabled(age):
    now = timezone.now()
    output = StringIO()
    with (
        patch.object(check_boss_worker.BossCheckState.objects, "filter") as query,
        patch.object(check_boss_worker.timezone, "now", return_value=now),
    ):
        query.return_value.values.return_value.first.return_value = {
            "checked_at": now - timedelta(seconds=age),
            "failed": False,
        }
        call_command("check_boss_worker", stdout=output)
        query.assert_called_once_with(key=check_boss_worker.WORKER_KEY)
    assert "healthy" in output.getvalue()


@pytest.mark.parametrize("age,failed", [(121, False), (-1, False), (0, True)])
def test_stale_future_or_failed_heartbeat_is_rejected(age, failed):
    now = timezone.now()
    with (
        patch.object(check_boss_worker.BossCheckState.objects, "filter") as query,
        patch.object(check_boss_worker.timezone, "now", return_value=now),
    ):
        query.return_value.values.return_value.first.return_value = {
            "checked_at": now - timedelta(seconds=age),
            "failed": failed,
        }
        with pytest.raises(CommandError, match="missing, stale"):
            call_command("check_boss_worker")


@pytest.mark.parametrize("state", [None, {"checked_at": None, "failed": False}])
def test_missing_heartbeat_is_rejected(state):
    with patch.object(check_boss_worker.BossCheckState.objects, "filter") as query:
        query.return_value.values.return_value.first.return_value = state
        with pytest.raises(CommandError, match="missing, stale"):
            call_command("check_boss_worker")


@pytest.mark.parametrize("offset", [-1, 0, 1])
def test_release_requires_heartbeat_after_this_container_started(offset):
    now = timezone.now()
    started = now - timedelta(seconds=30)
    argument = started if timezone.is_aware(started) else timezone.make_aware(started)
    with patch.object(check_boss_worker.BossCheckState.objects, "filter") as query:
        query.return_value.values.return_value.first.return_value = {
            "checked_at": started + timedelta(seconds=offset),
            "failed": False,
        }
        if offset <= 0:
            with pytest.raises(CommandError, match="predates"):
                call_command("check_boss_worker", started_after=argument.isoformat())
        else:
            call_command("check_boss_worker", started_after=argument.isoformat(), stdout=StringIO())


@pytest.mark.parametrize("value", ["invalid", "2026-01-01", "2026-01-01T00:00:00"])
def test_invalid_start_timestamp_is_rejected_before_query(value):
    with patch.object(check_boss_worker.BossCheckState.objects, "filter") as query:
        with pytest.raises(CommandError, match="timezone-aware"):
            call_command("check_boss_worker", started_after=value)
        query.assert_not_called()


def test_database_failure_does_not_disclose_connection_details():
    with patch.object(
        check_boss_worker.BossCheckState.objects,
        "filter",
        side_effect=OperationalError("sensitive connection details"),
    ):
        with pytest.raises(CommandError, match="database is unavailable") as error:
            call_command("check_boss_worker")
        assert "sensitive" not in str(error.value)


@pytest.mark.django_db
@pytest.mark.parametrize("use_tz", [False, True])
def test_probe_reads_saved_heartbeat_without_writing(settings, use_tz):
    settings.USE_TZ = use_tz
    now = timezone.now()
    saved = check_boss_worker.BossCheckState.objects.create(
        key=check_boss_worker.WORKER_KEY, checked_at=now, failed=False
    )
    started = now - timedelta(seconds=1)
    if timezone.is_naive(started):
        started = timezone.make_aware(started)
    call_command("check_boss_worker", started_after=started.isoformat(), stdout=StringIO())
    saved.refresh_from_db()
    assert saved.checked_at == now
    assert saved.failed is False
