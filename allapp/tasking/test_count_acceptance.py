"""PDA count acceptance: observable contracts, stock integrity and race conditions.

These tests intentionally keep failures visible when an implementation violates a
business invariant. They do not change production behavior or migration state.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from threading import Barrier, Event, current_thread
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db import close_old_connections
from django.test import override_settings
from rest_framework.test import APIClient

from allapp.accounts.models import UserRoleScope
from allapp.baseinfo.models import Owner, OwnerWarehouseBinding
from allapp.core.choices import ZoneType
from allapp.inventory.models import (
    InventoryDetail,
    InventorySummary,
    InventoryTransaction,
    PostingJournal,
)
from allapp.locations.models import Location, Warehouse
from allapp.products.models import Product
from allapp.tasking import counting
from allapp.tasking import test_counting_closure as fixtures
from allapp.tasking.models import CountScopeLock, TaskAssignment, WmsTask

pytestmark = pytest.mark.django_db
BASE = "/api/pda/count-tasks/"


@pytest.fixture
def case(db):
    value = SimpleNamespace()
    fixtures.CountProductionClosureTests.setUp(value)
    return value


def client(user=None):
    result = APIClient(raise_request_exception=False)
    if user:
        result.force_authenticate(user)
    return result


def create(case, **changes):
    return counting.create_count_task(
        created_by=case.manager,
        owner_id=case.owner.pk,
        warehouse_id=case.warehouse.pk,
        **changes,
    )[0]


def release(case, **changes):
    task = create(case, **changes)
    return counting.release_count_task(task.pk, by_user=case.manager)


def record(case, task, quantity="4", seq="first", user=None, line=None):
    return counting.record_count(
        task.pk,
        line_id=(line or task.lines.get()).pk,
        qty_counted=quantity,
        client_seq=seq,
        by_user=user or case.manager,
    )


def operator(case, username="count-worker", permission=True, **scope):
    user = get_user_model().objects.create_user(username=username, password="x")
    if permission:
        user.user_permissions.add(Permission.objects.get(codename="claim_task_as_wh_operator"))
    UserRoleScope.objects.create(
        user=user,
        role=(
            UserRoleScope.Role.OWNER_MANAGER
            if "owner" in scope
            else UserRoleScope.Role.WAREHOUSE_OPERATOR
        ),
        **(scope or {"warehouse": case.warehouse}),
    )
    return user


def detail(case, *, product=None, location=None, qty="2", batch="B", expiry=None):
    return InventoryDetail.objects.create(
        owner=case.owner,
        product=product or case.product,
        warehouse=case.warehouse,
        location=location or case.location,
        batch_no=batch,
        expiry_date=expiry,
        onhand_qty=Decimal(qty),
    )


@pytest.mark.parametrize("scope", ["ALL", "ZONE", "LOC", "prefix", "SKU", "batch", "zero"])
def test_scope_filters_select_exact_inventory_layers(case, scope):
    other_location = Location.objects.create(
        warehouse=case.warehouse,
        subwarehouse=case.subwarehouse,
        code="COUNTSW-01-01-02",
        name="另一个库位",
        zone_type=ZoneType.PICK,
    )
    other_product = Product.objects.create(
        owner=case.owner, code="COUNT-OTHER", name="另一个商品", base_uom=case.uom
    )
    other = detail(case, location=other_location)
    zero = detail(case, product=other_product, qty="0", batch="ZERO")
    batch = detail(case, batch="B")
    options = {
        "ALL": ({"scope": "ALL"}, {case.detail.pk, other.pk, batch.pk}),
        "ZONE": ({"scope": "ZONE", "zone_type": ZoneType.STORAGE}, {case.detail.pk, batch.pk}),
        "LOC": ({"scope": "LOC", "location_id": case.location.pk}, {case.detail.pk, batch.pk}),
        "prefix": ({"scope": "LOC", "location_prefix": other_location.code.lower()}, {other.pk}),
        "SKU": (
            {"scope": "SKU", "product_id": other_product.pk, "exclude_zero_onhand": False},
            {zero.pk},
        ),
        "batch": ({"batch_no": " b "}, {other.pk, batch.pk}),
        "zero": ({"exclude_zero_onhand": False}, {case.detail.pk, other.pk, batch.pk, zero.pk}),
    }
    params, expected = options[scope]
    task = create(case, **params)
    assert set(task.lines.values_list("src_id", flat=True)) == expected
    assert not CountScopeLock.objects.filter(released_at__isnull=True).exists()


@pytest.mark.parametrize(
    "changes",
    [
        {"scope": "UNKNOWN"},
        {"scope": "SKU"},
        {"scope": "LOC"},
        {"scope": "ZONE"},
        {"max_lines": 0},
        {"max_lines": 10001},
        {"max_lines": "1.5"},
        {"recount_threshold": "-0.0001"},
        {"recount_threshold": "0.00001"},
        {"task_remark": "x" * 201},
        {"warehouse_id": 999999},
        {"owner_id": 999999},
        {"scope": "LOC", "location_prefix": "NO-MATCH"},
    ],
)
def test_invalid_creation_is_4xx_and_atomic(case, changes):
    before = WmsTask.objects.count()
    response = client(case.manager).post(
        BASE,
        {"owner_id": case.owner.pk, "warehouse_id": case.warehouse.pk, **changes},
        format="json",
    )
    assert response.status_code in (400, 403, 404)
    assert WmsTask.objects.count() == before
    assert not CountScopeLock.objects.filter(released_at__isnull=True).exists()


def test_release_refreshes_snapshot_and_is_idempotent(case):
    task = create(case, max_lines=10000)
    case.detail.onhand_qty = Decimal("7.1250")
    case.detail.save()
    counting.release_count_task(task.pk, by_user=case.manager)
    first_lines = list(task.lines.values_list("pk", flat=True))
    counting.release_count_task(task.pk, by_user=case.manager)
    assert list(task.lines.values_list("pk", flat=True)) == first_lines
    assert task.lines.get().countlineextra.qty_book == Decimal("7.1250")
    assert CountScopeLock.objects.filter(active_task=task, released_at__isnull=True).count() == 1


def test_no_stock_after_draft_rejects_release_without_partial_changes(case):
    task = create(case)
    old_lines = list(task.lines.values_list("pk", flat=True))
    case.detail.onhand_qty = 0
    case.detail.save()
    response = client(case.manager).post(f"{BASE}{task.pk}/release/")
    assert response.status_code == 400
    task.refresh_from_db()
    assert task.status == "DRAFT"
    assert list(task.lines.values_list("pk", flat=True)) == old_lines
    assert not CountScopeLock.objects.filter(released_at__isnull=True).exists()


def test_scope_excludes_other_owners_inventory_before_applying_line_limit(case):
    other_owner = Owner.objects.create(code="CNT-OTHER", name="其他货主")
    OwnerWarehouseBinding.objects.create(owner=other_owner, warehouse=case.warehouse)
    product = Product.objects.create(
        owner=other_owner, code="OTHER-SKU", name="其他货主商品", base_uom=case.uom
    )
    InventoryDetail.objects.create(
        owner=other_owner,
        warehouse=case.warehouse,
        location=case.location,
        product=product,
        onhand_qty=9,
    )
    task = release(case, max_lines=1)
    assert list(task.lines.values_list("src_id", flat=True)) == [case.detail.pk]
    assert task.lines.get().product.owner_id == case.owner.pk


def test_stage_lists_follow_the_real_lifecycle(case):
    api = client(case.manager)
    task = create(case)

    def assert_stage(expected):
        for stage in ("draft", "work", "review", "posting"):
            response = api.get(BASE, {"stage": stage})
            assert response.status_code == 200
            assert [row["id"] for row in response.data] == ([task.pk] if stage == expected else [])

    assert_stage("draft")
    counting.release_count_task(task.pk, by_user=case.manager)
    assert_stage("work")
    with override_settings(COUNT_MAX_TIMES=1):
        record(case, task, "4")
        counting.submit_count_task(task.pk, by_user=case.manager)
    assert_stage("review")
    counting.approve_count_task(task.pk, by_user=case.manager)
    assert_stage("posting")
    counting.post_count_task(task.pk, by_user=case.manager)
    assert_stage(None)
    assert api.get(BASE, {"stage": "unknown"}).status_code == 400


@pytest.mark.parametrize(
    "quantity", ["", "-1", "0.00001", "100000000000000", "NaN", "Infinity", "abc"]
)
def test_invalid_count_does_not_write_scans_or_quantity(case, quantity):
    task = release(case)
    response = client(case.manager).post(
        f"{BASE}{task.pk}/record/",
        {"line_id": task.lines.get().pk, "qty_counted": quantity, "client_seq": "invalid"},
        format="json",
    )
    assert response.status_code == 400
    assert not task.scan_logs.exists()
    assert task.lines.get().countlineextra.count_status == "NOT_COUNTED"


@pytest.mark.parametrize("quantity", ["0", "0.0001", "5.1234", "99999999999999.9999"])
def test_valid_count_preserves_four_decimal_precision(case, quantity):
    task = release(case)
    response = client(case.manager).post(
        f"{BASE}{task.pk}/record/",
        {"line_id": task.lines.get().pk, "qty_counted": quantity, "client_seq": "valid"},
        format="json",
    )
    assert response.status_code == 200, response.data
    assert Decimal(response.data["qty_counted"]) == Decimal(quantity)
    assert task.lines.get().countlineextra.qty_counted == Decimal(quantity)
    case.detail.refresh_from_db()
    assert case.detail.onhand_qty == 5


@pytest.mark.parametrize("foreign_line", [False, True])
def test_unknown_or_other_task_line_returns_4xx(case, foreign_line):
    other = create(case)
    task = release(case)
    line_id = other.lines.get().pk if foreign_line else 999999999
    response = client(case.manager).post(
        f"{BASE}{task.pk}/record/",
        {"line_id": line_id, "qty_counted": "1", "client_seq": "wrong-line"},
        format="json",
    )
    assert response.status_code in (400, 404), "Invalid line IDs must not produce server errors"
    assert not task.scan_logs.exists()


def test_blind_record_response_does_not_disclose_book_quantity(case):
    task = release(case)
    worker = operator(case)
    api = client(worker)
    assert api.post(f"{BASE}{task.pk}/claim/").status_code == 200
    lines = api.get(f"{BASE}{task.pk}/lines/").data
    assert "qty_book" not in lines[0] and "qty_diff" not in lines[0]
    response = api.post(
        f"{BASE}{task.pk}/record/",
        {"line_id": lines[0]["id"], "qty_counted": "0", "client_seq": "blind-zero"},
        format="json",
    )
    assert response.status_code == 200
    assert "qty_diff" not in response.data, "counted - difference reveals blind book quantity"
    assert "qty_book" not in response.data


def test_user_without_operation_permission_cannot_claim(case):
    task = release(case)
    worker = operator(case, permission=False)
    assert not worker.has_perm("tasking.claim_task_as_wh_operator")
    response = client(worker).post(f"{BASE}{task.pk}/claim/")
    assert response.status_code in (403, 404)
    assert not TaskAssignment.objects.filter(task=task).exists()


def test_operator_requires_own_assignment_and_cannot_manage(case):
    task = release(case)
    first, second = operator(case), operator(case, "count-second")
    api = client(first)
    payload = {"line_id": task.lines.get().pk, "qty_counted": "5", "client_seq": "mine"}
    assert api.post(f"{BASE}{task.pk}/record/", payload).status_code == 403
    assert api.post(f"{BASE}{task.pk}/claim/").status_code == 200
    assert api.post(f"{BASE}{task.pk}/claim/").status_code == 200
    for action in ["release", "cancel", "approve", "reject", "post"]:
        assert api.post(f"{BASE}{task.pk}/{action}/", {"note": "test"}).status_code == 403
    outsider = client(second)
    for action in ["claim", "record", "submit"]:
        assert outsider.post(f"{BASE}{task.pk}/{action}/", payload).status_code in (403, 404)
    assert outsider.get(f"{BASE}{task.pk}/lines/").status_code == 404


@pytest.mark.parametrize("boundary", ["warehouse", "owner", "anonymous"])
def test_task_and_options_are_isolated(case, boundary):
    task = release(case)
    if boundary == "anonymous":
        api = client()
    elif boundary == "warehouse":
        other = Warehouse.objects.create(code="CNT-OTH", name="其他仓")
        api = client(operator(case, warehouse=other))
    else:
        other = Owner.objects.create(code="CNT-OTH", name="其他货主")
        OwnerWarehouseBinding.objects.create(owner=other, warehouse=case.warehouse)
        api = client(operator(case, owner=other))
    for suffix in ["", "lines/"]:
        assert api.get(f"{BASE}{task.pk}/{suffix}").status_code in (401, 403, 404)
    for action in ["claim", "record", "submit", "release", "cancel", "approve", "reject", "post"]:
        assert api.post(f"{BASE}{task.pk}/{action}/", {}).status_code in (401, 403, 404)


@pytest.mark.parametrize(
    "quantity,outcome",
    [
        ("5", "AUTO_POSTED_NO_DIFF"),
        ("4.5", "PENDING_APPROVAL"),
        ("4", "PENDING_APPROVAL"),
        ("3.9999", "RECOUNT_RELEASED"),
    ],
)
def test_recount_threshold_is_absolute_and_strict(case, quantity, outcome):
    task = release(case, recount_threshold="1")
    record(case, task, quantity)
    result = counting.submit_count_task(task.pk, by_user=case.manager)
    assert result["outcome"] == outcome
    if outcome == "RECOUNT_RELEASED":
        assert CountScopeLock.objects.filter(
            active_task_id=result["next_task_id"], released_at__isnull=True
        ).exists()


@pytest.mark.parametrize("round_limit", [1, 2, 3])
def test_recount_round_limit_and_assignment_transfer(case, round_limit):
    task = release(case)
    worker = operator(case)
    counting.claim_count_task(task.pk, by_user=worker)
    root = task.pk
    with override_settings(COUNT_MAX_TIMES=round_limit):
        for round_number in range(1, round_limit + 1):
            record(case, task, "4", seq=f"round-{round_number}", user=worker)
            result = counting.submit_count_task(task.pk, by_user=worker)
            assert not TaskAssignment.objects.filter(task=task, finished_at__isnull=True).exists()
            if round_number < round_limit:
                assert result["outcome"] == "RECOUNT_RELEASED"
                task = WmsTask.objects.get(pk=result["next_task_id"])
                assert task.counttaskextra.round_no == round_number + 1
                assert task.counttaskextra.root_task_id == root
                assert task.assignments.get().assignee_id == worker.pk
            else:
                assert result["outcome"] == "PENDING_APPROVAL"
    counting.approve_count_task(task.pk, by_user=case.manager)
    counting.post_count_task(task.pk, by_user=case.manager)
    case.detail.refresh_from_db()
    assert case.detail.onhand_qty == 4
    assert not CountScopeLock.objects.filter(root_task_id=root, released_at__isnull=True).exists()


@pytest.mark.parametrize("quantity", ["0", "4.1234", "7.1234"])
def test_posted_quantity_summary_transactions_and_retry_agree(case, quantity):
    task = release(case)
    with override_settings(COUNT_MAX_TIMES=1):
        record(case, task, quantity)
        counting.submit_count_task(task.pk, by_user=case.manager)
    counting.approve_count_task(task.pk, by_user=case.manager)
    counting.post_count_task(task.pk, by_user=case.manager)
    counting.post_count_task(task.pk, by_user=case.manager)
    case.detail.refresh_from_db()
    expected = Decimal(quantity)
    assert case.detail.onhand_qty == expected
    assert case.detail.available_qty == expected
    summary = InventorySummary.objects.get(owner=case.owner, product=case.product)
    assert summary.onhand_qty == expected and summary.available_qty == expected
    tx = InventoryTransaction.objects.get(src_model="WmsTask", src_id=task.pk)
    assert tx.qty_delta == expected - 5
    assert tx.tx_type == ("ADJ_GAIN" if expected > 5 else "ADJ_LOSS")
    assert PostingJournal.objects.filter(src_id=task.pk, status="POSTED").count() == 1
    assert task.scan_logs.filter(posted_at__isnull=False).count() == 1


def test_count_preserves_separate_expiry_layers(case):
    case.detail.batch_no = "SAME"
    case.detail.expiry_date = date(2030, 1, 1)
    case.detail.save()
    other = detail(case, batch="SAME", expiry=date(2031, 1, 1), qty="9")
    task = release(case)
    with override_settings(COUNT_MAX_TIMES=1):
        for line in task.lines.all():
            record(case, task, "4" if line.src_id == case.detail.pk else "11", line=line)
        counting.submit_count_task(task.pk, by_user=case.manager)
    counting.approve_count_task(task.pk, by_user=case.manager)
    counting.post_count_task(task.pk, by_user=case.manager)
    case.detail.refresh_from_db()
    other.refresh_from_db()
    assert (case.detail.onhand_qty, other.onhand_qty) == (4, 11)
    assert InventorySummary.objects.get(owner=case.owner, product=case.product).onhand_qty == 15


@pytest.mark.parametrize("action", ["cancel", "reject"])
def test_cancel_and_reject_preserve_stock_and_unlock(case, action):
    task = release(case)
    with override_settings(COUNT_MAX_TIMES=1):
        record(case, task, "0")
        if action == "reject":
            counting.submit_count_task(task.pk, by_user=case.manager)
            assert (
                client(case.manager).post(f"{BASE}{task.pk}/reject/", {"note": " "}).status_code
                == 400
            )
            counting.reject_count_task(task.pk, by_user=case.manager, note="重新检查")
        else:
            counting.cancel_count_task(task.pk, by_user=case.manager)
    case.detail.refresh_from_db()
    assert case.detail.onhand_qty == 5
    assert not InventoryTransaction.objects.filter(src_id=task.pk).exists()
    assert not CountScopeLock.objects.filter(released_at__isnull=True).exists()
    assert client(case.manager).post(f"{BASE}{task.pk}/submit/").status_code == 400


def test_partial_count_cannot_submit_and_snapshot_drift_is_rejected(case):
    detail(case)
    task = release(case)
    lines = list(task.lines.all())
    record(case, task, "5", line=lines[0])
    assert client(case.manager).post(f"{BASE}{task.pk}/submit/").status_code == 400
    record(case, task, "2", line=lines[1])
    case.detail.onhand_qty = 6
    case.detail.save()
    assert client(case.manager).post(f"{BASE}{task.pk}/submit/").status_code == 400
    task.refresh_from_db()
    assert task.status == "IN_PROGRESS"
    assert not InventoryTransaction.objects.exists()


def test_post_failure_after_first_stock_write_rolls_back_and_can_retry(case):
    from allapp.inventory import services

    second = detail(case, qty="3")
    task = release(case)
    with override_settings(COUNT_MAX_TIMES=1):
        for line in task.lines.all():
            record(case, task, "6" if line.src_id == case.detail.pk else "2", line=line)
        counting.submit_count_task(task.pk, by_user=case.manager)
    counting.approve_count_task(task.pk, by_user=case.manager)
    original = services._apply_receive_like
    calls = []

    def fail_after_real_write(*args, **kwargs):
        result = original(*args, **kwargs)
        calls.append(result)
        raise RuntimeError("injected failure after real stock write")

    with patch.object(services, "_apply_receive_like", side_effect=fail_after_real_write):
        with pytest.raises(RuntimeError, match="after real stock write"):
            counting.post_count_task(task.pk, by_user=case.manager)
    assert calls
    case.detail.refresh_from_db()
    second.refresh_from_db()
    task.refresh_from_db()
    assert (case.detail.onhand_qty, second.onhand_qty) == (5, 3)
    assert not InventoryTransaction.objects.exists()
    assert task.posting_status == "FAILED"
    assert CountScopeLock.objects.filter(released_at__isnull=True).exists()
    counting.post_count_task(task.pk, by_user=case.manager)
    assert InventoryTransaction.objects.filter(src_id=task.pk).count() == 2
    assert not CountScopeLock.objects.filter(released_at__isnull=True).exists()


def test_replaced_snapshot_and_late_retry_cannot_restore_old_value(case):
    task = release(case)
    record(case, task, "4", seq="old")
    record(case, task, "6", seq="new")
    response = record(case, task, "4", seq="old")
    assert response["idempotent"] is True
    assert Decimal(response["qty_counted"]) == 6
    assert task.lines.get().countlineextra.qty_counted == 6
    assert task.scan_logs.filter(status="OK").count() == 1
    assert task.scan_logs.filter(status="IGNORED").count() == 1


def race(workers):
    barrier = Barrier(len(workers))

    def run(fn):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            try:
                fn()
                return "ok"
            except ValidationError:
                return "rejected"
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=len(workers)) as pool:
        return list(pool.map(run, workers))


@pytest.mark.django_db(transaction=True)
def test_two_workers_cannot_both_claim(case):
    task = release(case)
    users = [operator(case, "race-one"), operator(case, "race-two")]
    results = race([lambda u=u: counting.claim_count_task(task.pk, by_user=u) for u in users])
    assert sorted(results) == ["ok", "rejected"]
    assert TaskAssignment.objects.filter(task=task, finished_at__isnull=True).count() == 1


@pytest.mark.django_db(transaction=True)
def test_concurrent_same_record_creates_one_snapshot(case):
    task = release(case)
    results = race([lambda: record(case, task, "4", seq="retry")] * 2)
    assert results == ["ok", "ok"]
    assert task.scan_logs.filter(status="OK").count() == 1


@pytest.mark.django_db(transaction=True)
def test_concurrent_submit_creates_only_one_recount(case):
    task = release(case)
    record(case, task)
    results = race([lambda: counting.submit_count_task(task.pk, by_user=case.manager)] * 2)
    assert sorted(results) == ["ok", "rejected"]
    assert WmsTask.objects.filter(counttaskextra__parent_task=task).count() == 1


@pytest.mark.django_db(transaction=True)
def test_concurrent_post_changes_stock_only_once(case):
    task = release(case)
    with override_settings(COUNT_MAX_TIMES=1):
        record(case, task)
        counting.submit_count_task(task.pk, by_user=case.manager)
    counting.approve_count_task(task.pk, by_user=case.manager)
    results = race([lambda: counting.post_count_task(task.pk, by_user=case.manager)] * 2)
    assert results == ["ok", "ok"]
    case.detail.refresh_from_db()
    assert case.detail.onhand_qty == 4
    assert InventoryTransaction.objects.filter(src_id=task.pk).count() == 1
    assert not CountScopeLock.objects.filter(released_at__isnull=True).exists()


@pytest.mark.django_db(transaction=True)
def test_edit_racing_submit_has_a_single_consistent_final_snapshot(case):
    task = release(case)
    record(case, task, "5")
    with override_settings(COUNT_MAX_TIMES=1):
        outcomes = race(
            [
                lambda: record(case, task, "4", seq="concurrent-edit"),
                lambda: counting.submit_count_task(task.pk, by_user=case.manager),
            ]
        )
    task.refresh_from_db()
    case.detail.refresh_from_db()
    extra = task.lines.get().countlineextra
    assert outcomes[1] == "ok"
    assert task.status == "COMPLETED"
    assert case.detail.onhand_qty == 5
    assert task.scan_logs.filter(status="OK").count() == 1
    if outcomes[0] == "ok":
        assert extra.qty_counted == 4
        assert task.review_status == "PENDING"
        assert CountScopeLock.objects.filter(released_at__isnull=True).exists()
    else:
        assert extra.qty_counted == 5
        assert task.posting_status == "POSTED"
        assert not CountScopeLock.objects.filter(released_at__isnull=True).exists()


@pytest.mark.django_db(transaction=True)
def test_concurrent_approve_and_reject_cannot_both_succeed(case):
    task = release(case)
    with override_settings(COUNT_MAX_TIMES=1):
        record(case, task, "4")
        counting.submit_count_task(task.pk, by_user=case.manager)
    outcomes = race(
        [
            lambda: counting.approve_count_task(task.pk, by_user=case.manager),
            lambda: counting.reject_count_task(task.pk, by_user=case.manager, note="复核失败"),
        ]
    )
    assert sorted(outcomes) == ["ok", "rejected"]
    task.refresh_from_db()
    assert task.review_status in ("APPROVED", "REJECTED")
    assert not InventoryTransaction.objects.exists()
    assert CountScopeLock.objects.filter(released_at__isnull=True).exists() == (
        task.review_status == "APPROVED"
    )


@pytest.mark.parametrize("blind", [True, False])
def test_manager_can_see_books_and_operator_sees_them_only_in_open_count(case, blind):
    task = release(case, blind=blind)
    worker = operator(case)
    manager_row = client(case.manager).get(f"{BASE}{task.pk}/lines/").data[0]
    worker_row = client(worker).get(f"{BASE}{task.pk}/lines/").data[0]
    assert manager_row["qty_book"] == "5.0000"
    assert ("qty_book" in worker_row) == (not blind)
    assert ("qty_diff" in worker_row) == (not blind)


def test_unaudited_task_cannot_post_and_closed_task_cannot_be_edited(case):
    task = release(case)
    api = client(case.manager)
    assert api.post(f"{BASE}{task.pk}/post/").status_code == 400
    record(case, task, "5")
    counting.submit_count_task(task.pk, by_user=case.manager)
    for action in ["claim", "release", "cancel", "approve", "reject", "submit"]:
        assert api.post(f"{BASE}{task.pk}/{action}/", {"note": "test"}).status_code == 400
    response = api.post(
        f"{BASE}{task.pk}/record/",
        {
            "line_id": task.lines.get().pk,
            "qty_counted": "0",
            "client_seq": "too-late",
        },
    )
    assert response.status_code == 400
    case.detail.refresh_from_db()
    assert case.detail.onhand_qty == 5


def test_count_cannot_reduce_stock_below_existing_reservations(case):
    case.detail.allocated_qty = 2
    case.detail.save()
    task = release(case)
    with override_settings(COUNT_MAX_TIMES=1):
        record(case, task, "0")
        counting.submit_count_task(task.pk, by_user=case.manager)
    counting.approve_count_task(task.pk, by_user=case.manager)
    with pytest.raises(ValidationError):
        counting.post_count_task(task.pk, by_user=case.manager)
    task.refresh_from_db()
    case.detail.refresh_from_db()
    assert (case.detail.onhand_qty, case.detail.allocated_qty, case.detail.available_qty) == (
        5,
        2,
        3,
    )
    assert task.posting_status == "FAILED"
    assert CountScopeLock.objects.filter(released_at__isnull=True).exists()
    assert not InventoryTransaction.objects.exists()


@pytest.mark.parametrize("batch_length", [60, 64])
def test_count_accepts_batch_numbers_supported_by_inventory(case, batch_length):
    case.detail.batch_no = "B" * batch_length
    case.detail.save()
    response = client(case.manager).post(
        BASE,
        {
            "owner_id": case.owner.pk,
            "warehouse_id": case.warehouse.pk,
        },
        format="json",
    )
    assert response.status_code == 201, response.data
    task = WmsTask.objects.get(pk=response.data["id"])
    assert task.lines.get().countlineextra.lot_no == case.detail.batch_no


@pytest.mark.parametrize("quantity", ["4", "6"])
def test_posting_updates_the_original_manufacturing_date_layer(case, quantity):
    case.detail.batch_no = "DATED"
    case.detail.production_date = date(2029, 1, 1)
    case.detail.expiry_date = date(2030, 1, 1)
    case.detail.save()
    task = release(case)
    with override_settings(COUNT_MAX_TIMES=1):
        record(case, task, quantity)
        counting.submit_count_task(task.pk, by_user=case.manager)
    counting.approve_count_task(task.pk, by_user=case.manager)
    response = client(case.manager).post(f"{BASE}{task.pk}/post/")
    assert response.status_code == 200, getattr(response, "data", response.status_code)
    layers = list(
        InventoryDetail.objects.filter(product=case.product).values(
            "id",
            "production_date",
            "expiry_date",
            "onhand_qty",
        )
    )
    assert layers == [
        {
            "id": case.detail.pk,
            "production_date": date(2029, 1, 1),
            "expiry_date": date(2030, 1, 1),
            "onhand_qty": Decimal(quantity),
        }
    ]


@pytest.mark.django_db(transaction=True)
def test_late_failed_post_cannot_downgrade_a_successful_concurrent_journal(case):
    from allapp.inventory import services

    task = release(case)
    with override_settings(COUNT_MAX_TIMES=1):
        record(case, task, "4")
        counting.submit_count_task(task.pk, by_user=case.manager)
    counting.approve_count_task(task.pk, by_user=case.manager)
    started, successful = Event(), Event()
    real_post = services.post_task

    def delayed_failure(**kwargs):
        if current_thread().name.startswith("late-count-failure"):
            started.set()
            assert successful.wait(timeout=15), "The other posting operation did not finish"
            raise RuntimeError("late posting failure")
        return real_post(**kwargs)

    def first_request():
        close_old_connections()
        try:
            with pytest.raises(RuntimeError, match="late posting failure"):
                counting.post_count_task(task.pk, by_user=case.manager)
        finally:
            close_old_connections()

    with patch.object(services, "post_task", side_effect=delayed_failure):
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="late-count-failure") as pool:
            first = pool.submit(first_request)
            try:
                assert started.wait(timeout=15)
                counting.post_count_task(task.pk, by_user=case.manager)
            finally:
                successful.set()
            first.result(timeout=15)
    task.refresh_from_db()
    case.detail.refresh_from_db()
    assert task.posting_status == "POSTED"
    assert case.detail.onhand_qty == 4
    assert InventoryTransaction.objects.filter(src_id=task.pk).count() == 1
    journal = PostingJournal.objects.get(src_model="WmsTask", src_id=task.pk, tx_type="POST")
    assert (
        journal.status == "POSTED"
    ), "A late failed request must not overwrite successful posting evidence"
    assert counting.post_count_task(task.pk, by_user=case.manager)["ok"] is True
