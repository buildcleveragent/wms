"""Regression coverage for exact layers, blind APIs, precision and bulk rollback."""

from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection
from django.test.utils import CaptureQueriesContext

from allapp.inventory.models import InventoryDetail, InventorySummary, InventoryTransaction
from allapp.locations.models import Container
from allapp.tasking import counting
from allapp.tasking.count_inventory import COUNT_MAX_QTY, SNAPSHOT_KEY, inventory_snapshot
from allapp.tasking.models import (
    CountLineExtra,
    CountScopeLock,
    TaskAssignment,
    TaskScanLog,
    WmsTask,
    WmsTaskLine,
)
from allapp.tasking.serializers import WmsTaskLineSerializer
from allapp.tasking.test_count_acceptance import BASE
from allapp.tasking.test_count_acceptance import case as count_case_fixture
from allapp.tasking.test_count_acceptance import client, create, operator, record, release

case = count_case_fixture
pytestmark = pytest.mark.django_db


def approve(case, task, quantity, settings):
    settings.COUNT_MAX_TIMES = 1
    record(case, task, quantity)
    assert (
        counting.submit_count_task(task.pk, by_user=case.manager)["outcome"] == "PENDING_APPROVAL"
    )
    counting.approve_count_task(task.pk, by_user=case.manager)


@pytest.mark.parametrize("quantity", ["4.1234", "6.1234"])
def test_same_dimensions_in_two_containers_post_to_exact_source(case, settings, quantity):
    containers = [
        Container.objects.create(
            owner=case.owner,
            warehouse=case.warehouse,
            location=case.location,
            container_no=f"COUNT-CONTAINER-{n}",
        )
        for n in range(2)
    ]
    case.detail.container = containers[0]
    case.detail.batch_no = "SAME"
    case.detail.production_date = date(2029, 1, 1)
    case.detail.expiry_date = date(2030, 1, 1)
    case.detail.save()
    sibling = InventoryDetail.objects.create(
        owner=case.owner,
        warehouse=case.warehouse,
        product=case.product,
        location=case.location,
        container=containers[1],
        batch_no="SAME",
        production_date=case.detail.production_date,
        expiry_date=case.detail.expiry_date,
        onhand_qty=9,
    )
    task = release(case)
    settings.COUNT_MAX_TIMES = 2
    lines = {line.src_id: line for line in task.lines.all()}
    record(case, task, quantity, line=lines[case.detail.pk])
    record(case, task, "9", line=lines[sibling.pk])
    result = counting.submit_count_task(task.pk, by_user=case.manager)
    assert result["outcome"] == "RECOUNT_RELEASED"
    recount = WmsTask.objects.get(pk=result["next_task_id"])
    assert recount.lines.count() == 1
    assert recount.lines.get().plan_meta == lines[case.detail.pk].plan_meta
    record(case, recount, quantity)
    counting.submit_count_task(recount.pk, by_user=case.manager)
    counting.approve_count_task(recount.pk, by_user=case.manager)
    counting.post_count_task(recount.pk, by_user=case.manager)
    counting.post_count_task(recount.pk, by_user=case.manager)
    case.detail.refresh_from_db()
    sibling.refresh_from_db()
    assert case.detail.onhand_qty == Decimal(quantity)
    assert sibling.onhand_qty == 9
    assert InventoryDetail.objects.count() == 2
    assert inventory_snapshot(case.detail) == lines[case.detail.pk].plan_meta[SNAPSHOT_KEY]
    tx = InventoryTransaction.objects.get()
    assert tx.container_id == containers[0].pk
    assert tx.container_no == containers[0].container_no
    assert tx.production_date == case.detail.production_date
    assert tx.expiry_date == case.detail.expiry_date
    assert tx.serial_no == case.detail.serial_no
    assert tx.src_line_id == recount.lines.get().pk
    assert tx.qty_delta == Decimal(quantity) - 5


def test_same_batch_and_expiry_with_two_production_dates_stay_separate(case, settings):
    case.detail.batch_no = "SAME"
    case.detail.production_date = date(2029, 1, 1)
    case.detail.expiry_date = date(2030, 1, 1)
    case.detail.save()
    sibling = InventoryDetail.objects.create(
        owner=case.owner,
        warehouse=case.warehouse,
        product=case.product,
        location=case.location,
        batch_no=case.detail.batch_no,
        production_date=date(2029, 2, 1),
        expiry_date=case.detail.expiry_date,
        onhand_qty=9,
    )
    settings.COUNT_MAX_TIMES = 1
    task = release(case)
    lines = {line.src_id: line for line in task.lines.all()}
    record(case, task, "6.1234", line=lines[case.detail.pk])
    record(case, task, "8.4321", line=lines[sibling.pk])
    counting.submit_count_task(task.pk, by_user=case.manager)
    counting.approve_count_task(task.pk, by_user=case.manager)
    counting.post_count_task(task.pk, by_user=case.manager)
    case.detail.refresh_from_db()
    sibling.refresh_from_db()
    assert case.detail.onhand_qty == Decimal("6.1234")
    assert sibling.onhand_qty == Decimal("8.4321")
    assert InventoryDetail.objects.count() == 2
    assert dict(InventoryTransaction.objects.values_list("production_date", "qty_delta")) == {
        date(2029, 1, 1): Decimal("1.1234"),
        date(2029, 2, 1): Decimal("-0.5679"),
    }


@pytest.mark.parametrize("book,counted", [("1", "0"), ("0", "1")])
def test_serial_controlled_count_preserves_original_serial_layer(case, settings, book, counted):
    case.product.serial_control = True
    case.product.save()
    case.detail.onhand_qty = Decimal(book)
    case.detail.serial_no = "COUNT-SERIAL-1"
    case.detail.save()
    task = release(case, exclude_zero_onhand=False)
    approve(case, task, counted, settings)
    counting.post_count_task(task.pk, by_user=case.manager)
    case.detail.refresh_from_db()
    assert case.detail.onhand_qty == Decimal(counted)
    assert case.detail.serial_no == "COUNT-SERIAL-1"
    assert InventoryDetail.objects.count() == 1
    assert InventoryTransaction.objects.get().serial_no == "COUNT-SERIAL-1"


@pytest.mark.parametrize("quantity", ["0.0001", "5.1234", "99999999999999.9999"])
def test_full_four_place_and_upper_bound_closure(case, settings, quantity):
    task = release(case)
    approve(case, task, quantity, settings)
    line = task.lines.get()
    assert line.qty_plan == Decimal("5.0000")
    assert line.qty_done == Decimal(quantity)
    scan = task.scan_logs.get()
    assert scan.qty_base == Decimal(quantity) - 5
    counting.post_count_task(task.pk, by_user=case.manager)
    counting.post_count_task(task.pk, by_user=case.manager)
    case.detail.refresh_from_db()
    assert case.detail.onhand_qty == Decimal(quantity)
    assert InventorySummary.objects.get(product=case.product).onhand_qty == Decimal(quantity)
    assert InventoryTransaction.objects.get().qty_delta == Decimal(quantity) - 5


def test_maximum_book_can_be_counted_down_to_zero(case, settings):
    case.detail.onhand_qty = COUNT_MAX_QTY
    case.detail.save()
    task = release(case)
    approve(case, task, "0", settings)
    counting.post_count_task(task.pk, by_user=case.manager)
    case.detail.refresh_from_db()
    assert case.detail.onhand_qty == 0
    assert task.lines.get().qty_plan == COUNT_MAX_QTY
    assert InventoryTransaction.objects.get().qty_delta == -COUNT_MAX_QTY


@pytest.mark.parametrize(
    "change",
    [
        "batch_no",
        "production_date",
        "expiry_date",
        "serial_no",
        "onhand_qty",
        "is_active",
        "source_id",
    ],
)
def test_posting_revalidates_source_after_approval_and_keeps_freeze(case, settings, change):
    task = release(case)
    approve(case, task, "4", settings)
    if change == "source_id":
        task.lines.update(src_id=99999999)
    else:
        values = {
            "batch_no": "CHANGED",
            "production_date": date(2028, 1, 1),
            "expiry_date": date(2032, 1, 1),
            "serial_no": "CHANGED",
            "onhand_qty": Decimal("6"),
            "is_active": False,
        }
        update = {change: values[change]}
        if change == "onhand_qty":
            update["available_qty"] = values[change]
        InventoryDetail.objects.filter(pk=case.detail.pk).update(**update)
    with pytest.raises(ValidationError):
        counting.post_count_task(task.pk, by_user=case.manager)
    assert not InventoryTransaction.objects.exists()
    assert InventoryDetail.objects.count() == 1
    assert CountScopeLock.objects.filter(root_task=task, released_at__isnull=True).exists()
    task.refresh_from_db()
    assert task.posting_status == "FAILED"


@pytest.mark.parametrize("quantity", ["4", "4.1234"])
def test_legacy_snapshot_requires_verifiable_original_source(case, settings, quantity):
    case.detail.production_date = date(2029, 1, 1)
    case.detail.save()
    task = release(case)
    task.lines.update(plan_meta={})
    approve(case, task, quantity, settings)
    # Simulate the three-place task line persisted before this migration.
    task.lines.update(qty_done=Decimal(quantity).quantize(Decimal("0.001")))
    counting.post_count_task(task.pk, by_user=case.manager)
    case.detail.refresh_from_db()
    assert case.detail.onhand_qty == Decimal(quantity)
    assert InventoryDetail.objects.count() == 1
    assert InventoryTransaction.objects.get().production_date == date(2029, 1, 1)


@pytest.mark.parametrize("blind,manager", [(True, False), (True, True), (False, False)])
def test_blind_policy_covers_retries_generic_lines_nested_lines_and_scan_reads(
    case, blind, manager
):
    task = release(case, blind=blind)
    user = case.manager if manager else operator(case)
    counting.claim_count_task(task.pk, by_user=user)
    api = client(user)
    line = task.lines.get()
    payload = {"line_id": line.pk, "qty_counted": "0", "client_seq": "blind-retry"}
    hidden = blind and not manager
    for retry in (False, True):
        response = api.post(f"{BASE}{task.pk}/record/", payload, format="json")
        assert response.status_code == 200, response.data
        assert response.data["idempotent"] is retry
        assert ("qty_diff" not in response.data) is hidden
    dedicated = api.get(f"{BASE}{task.pk}/lines/")
    generic = api.get(f"/api/tasking/task-lines/{line.pk}/")
    nested = api.get(f"/api/tasking/tasks/{task.pk}/")
    for response in (dedicated, generic, nested):
        assert response.status_code == 200, response.data
    for data in (generic.data, nested.data["lines"][0]):
        assert ("qty_plan" not in data) is hidden
        assert ("qty_book" not in data["extra"]) is hidden
        assert ("qty_diff" not in data["extra"]) is hidden
        assert data["qty_done"] == "0.0000"
    assert ("qty_book" not in dedicated.data[0]) is hidden
    for url in (
        f"/api/tasking/scan-logs/?task={task.pk}",
        f"/api/tasking/tasks/{task.pk}/scan-logs/",
    ):
        response = api.get(url)
        assert response.status_code == 200, response.data
        rows = (
            response.data.get("results", []) if isinstance(response.data, dict) else response.data
        )
        assert len(rows) == 1
        for key in ("qty_base", "qty_base_delta", "qty_aux"):
            assert (key not in rows[0]) is hidden


def test_claim_and_record_flags_require_capability_status_and_assignment(case):
    task = release(case)
    denied = operator(case, permission=False)
    response = client(denied).get(f"{BASE}{task.pk}/")
    assert response.status_code == 200
    assert response.data["can_claim"] is False and response.data["can_record"] is False

    with pytest.raises(PermissionDenied):
        counting.claim_count_task(task.pk, by_user=denied)
    assert not TaskAssignment.objects.filter(task=task).exists()
    worker = operator(case, username="allowed-count-worker")
    response = client(worker).get(f"{BASE}{task.pk}/")
    assert response.data["can_claim"] is True and response.data["can_record"] is False
    counting.claim_count_task(task.pk, by_user=worker)
    assert client(worker).get(f"{BASE}{task.pk}/").data["can_record"] is True
    record(case, task, "5", user=worker)
    counting.submit_count_task(task.pk, by_user=worker)
    assert client(worker).get(f"{BASE}{task.pk}/").status_code == 404
    response = client(case.manager).get(f"{BASE}{task.pk}/")
    assert response.data["can_claim"] is False and response.data["can_record"] is False


@pytest.mark.parametrize("foreign", [False, True])
def test_invalid_detail_is_a_field_level_400(case, foreign):
    task = release(case)
    other = create(case)
    line_id = other.lines.get().pk if foreign else 999999999
    response = client(case.manager).post(
        f"{BASE}{task.pk}/record/",
        {"line_id": line_id, "qty_counted": "1", "client_seq": "bad-detail"},
        format="json",
    )
    assert response.status_code == 400
    assert "line_id" in response.data
    assert not task.scan_logs.exists()


@pytest.mark.parametrize(
    "quantity", [None, "", "NaN", "Infinity", "-1", "1.00001", "100000000000000"]
)
def test_service_also_rejects_invalid_quantity_without_rounding(case, quantity):
    task = release(case)
    with pytest.raises(ValidationError):
        record(case, task, quantity)
    assert not task.scan_logs.exists()
    assert task.lines.get().qty_done == 0


def test_non_count_generic_quantities_retain_three_place_contract(case):
    from allapp.inbound.serializers import InboundTaskLineSerializer
    from allapp.outbound.views import PickTaskLineSerializer

    task = WmsTask.objects.create(
        task_type="RECEIVE", task_no="LEGACY-QTY", owner=case.owner, warehouse=case.warehouse
    )
    line = task.lines.create(product=case.product, qty_plan="999999999999999.999", qty_done="0.001")
    line.refresh_from_db()
    for serializer_class in (
        WmsTaskLineSerializer,
        InboundTaskLineSerializer,
        PickTaskLineSerializer,
    ):
        data = serializer_class(line).data
        assert data["qty_plan"] == "999999999999999.999"
        assert data["qty_done"] == "0.001"
    serializer = WmsTaskLineSerializer(line, data={"qty_done": "0.0001"}, partial=True)
    assert not serializer.is_valid()
    assert "qty_done" in serializer.errors


def seed_layers(case, count):
    InventoryDetail.objects.bulk_create(
        [
            InventoryDetail(
                owner=case.owner,
                product=case.product,
                warehouse=case.warehouse,
                subwarehouse=case.subwarehouse,
                location=case.location,
                batch_no=f"BULK-{index:05d}",
                onhand_qty=Decimal("1.1234"),
                available_qty=Decimal("1.1234"),
                base_unit=case.uom.code,
            )
            for index in range(1, count)
        ],
        batch_size=500,
    )


def test_bulk_creation_order_primary_keys_quantities_and_publish_snapshot(case):
    seed_layers(case, 520)
    with CaptureQueriesContext(connection) as queries:
        task = create(case)
    assert len(queries) < 150, "Batch generation must not perform per-line DB validation"
    lines = list(task.lines.select_related("countlineextra").order_by("id"))
    expected = list(counting._inventory_queryset(counting._params_from_extra(task.counttaskextra)))
    assert [line.src_id for line in lines] == [d.pk for d in expected]
    for line, detail in zip(lines, expected):
        assert line.plan_meta[SNAPSHOT_KEY] == inventory_snapshot(detail)
        assert line.qty_plan == detail.onhand_qty == line.countlineextra.qty_book
        assert line.countlineextra.lot_no == detail.batch_no
        assert line.countlineextra.qty_diff == -detail.onhand_qty
        assert line.countlineextra.count_status == "NOT_COUNTED"
    InventoryDetail.objects.filter(pk=case.detail.pk).update(
        onhand_qty=Decimal("8.1234"), available_qty=Decimal("8.1234")
    )
    counting.release_count_task(task.pk, by_user=case.manager)
    assert task.lines.get(src_id=case.detail.pk).countlineextra.qty_book == Decimal("8.1234")


def test_failed_bulk_publish_preserves_original_draft_and_all_lines(case):
    seed_layers(case, 520)
    task = create(case)
    before = list(task.lines.order_by("id").values("id", "src_id", "plan_meta", "qty_plan"))
    real_bulk = CountLineExtra.objects.bulk_create

    def fail_after_write(*args, **kwargs):
        real_bulk(*args, **kwargs)
        raise RuntimeError("injected after extras bulk insert")

    with patch.object(CountLineExtra.objects, "bulk_create", side_effect=fail_after_write):
        with pytest.raises(RuntimeError, match="injected"):
            counting.release_count_task(task.pk, by_user=case.manager)
    task.refresh_from_db()
    assert task.status == "DRAFT"
    assert before == list(task.lines.order_by("id").values("id", "src_id", "plan_meta", "qty_plan"))
    assert CountLineExtra.objects.filter(line__task=task).count() == 520
    assert not CountScopeLock.objects.filter(root_task=task).exists()


def test_bulk_recount_keeps_only_differences_and_inherits_exact_snapshots(case, settings):
    settings.COUNT_MAX_TIMES = 2
    seed_layers(case, 520)
    task = release(case)
    lines = list(task.lines.select_related("countlineextra").order_by("id"))
    scans, extras = [], []
    for index, line in enumerate(lines):
        extra = line.countlineextra
        extra.qty_diff = Decimal("1") if index < 503 else Decimal("0")
        extra.qty_counted = extra.qty_book + extra.qty_diff
        extra.count_status = "COUNTED"
        line.qty_done = extra.qty_counted
        line.scan_snapshot_rev = 1
        line.status = WmsTaskLine.Status.COMPLETED
        extras.append(extra)
        scans.append(
            TaskScanLog(
                owner=case.owner,
                warehouse=case.warehouse,
                task=task,
                task_line=line,
                product=case.product,
                location=case.location,
                qty_base=extra.qty_diff,
                lot_no=extra.lot_no,
                scan_snapshot_rev=1,
                fp=f"bulk-count-{line.pk}",
            )
        )
    CountLineExtra.objects.bulk_update(
        extras, ["qty_counted", "qty_diff", "count_status"], batch_size=500
    )
    WmsTaskLine.objects.bulk_update(
        lines, ["qty_done", "scan_snapshot_rev", "status"], batch_size=500
    )
    TaskScanLog.objects.bulk_create(scans, batch_size=500)
    result = counting.submit_count_task(task.pk, by_user=case.manager)
    assert result["outcome"] == "RECOUNT_RELEASED"
    recount = WmsTask.objects.get(pk=result["next_task_id"])
    new_lines = list(recount.lines.select_related("countlineextra").order_by("id"))
    assert len(new_lines) == 503
    for old, new in zip(lines[:503], new_lines):
        assert new.src_id == old.src_id
        assert new.plan_meta == old.plan_meta
        assert new.countlineextra.lot_no == old.countlineextra.lot_no
        assert new.countlineextra.qty_book == old.countlineextra.qty_book
        assert new.countlineextra.qty_counted == 0
        assert new.countlineextra.count_status == "NOT_COUNTED"
        assert new.countlineextra.countorder == CountLineExtra.CountOrder.SECOND


def test_ten_thousand_line_limit_and_truncation_rollback(case):
    seed_layers(case, 10001)
    task = create(case, max_lines=10000)
    original_ids = list(task.lines.values_list("id", flat=True))
    assert len(original_ids) == 10000
    with pytest.raises(ValidationError, match="截断|上限|超出"):
        counting.release_count_task(task.pk, by_user=case.manager)
    task.refresh_from_db()
    assert task.status == "DRAFT"
    assert list(task.lines.values_list("id", flat=True)) == original_ids
    assert not CountScopeLock.objects.filter(root_task=task).exists()
