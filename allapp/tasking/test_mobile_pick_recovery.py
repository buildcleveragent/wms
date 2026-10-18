"""A lost scan response must be recoverable after the task changes state."""

import pytest
from rest_framework.exceptions import ValidationError

from allapp.tasking.models import TaskScanLog, WmsTask
from allapp.tasking.services import scan_task


@pytest.fixture
def pick(db):
    from allapp.tasking.test_assisted_outbound_integrity import PickScanIntegrityTests

    fixture = PickScanIntegrityTests(
        "test_pick_scans_select_unfinished_location_lines_and_remain_append_only"
    )
    fixture.setUp()
    return fixture


@pytest.mark.parametrize("status", ["RELEASED", "COMPLETED", "CANCELLED"])
def test_original_pick_request_replays_once_even_after_terminal_state(pick, status):
    payload = {
        "task_id": pick.task.pk,
        "barcode": pick.product.sku,
        "qty": "1.000",
        "by_user": pick.user,
        "client_seq": "mobile-original-request",
    }
    original = scan_task(**payload)
    WmsTask.objects.filter(pk=pick.task.pk).update(
        status=status, review_status="PENDING" if status == "COMPLETED" else "NONE"
    )
    # A code can be retired after a successful scan; replay uses the saved fact.
    pick.product.is_active = False
    pick.product.save(update_fields=["is_active"])
    replay = scan_task(**payload)
    assert replay["idempotent"] is True
    assert replay["scan_id"] == original["scan_id"]
    assert replay["resolved"] == original["resolved"]
    assert TaskScanLog.objects.filter(task=pick.task).count() == 1
    pick.line_1.refresh_from_db()
    pick.line_2.refresh_from_db()
    assert pick.line_1.qty_done == 1
    assert pick.line_2.qty_done == 0


def test_terminal_task_still_rejects_new_requests_and_other_actors(pick):
    payload = {
        "task_id": pick.task.pk,
        "barcode": pick.product.sku,
        "qty": 1,
        "by_user": pick.user,
        "client_seq": "original",
    }
    scan_task(**payload)
    WmsTask.objects.filter(pk=pick.task.pk).update(status="COMPLETED", review_status="PENDING")
    for changes in (
        {"client_seq": "new"},
        {"qty": 2},
        {"by_user": None},
        {"location_id": pick.location_2.pk},
    ):
        with pytest.raises(ValidationError):
            scan_task(**{**payload, **changes})
    assert TaskScanLog.objects.filter(task=pick.task).count() == 1
