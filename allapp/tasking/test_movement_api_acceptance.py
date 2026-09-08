"""Real MySQL/API acceptance for relocation and replenishment state machines."""

import datetime
from decimal import Decimal
from types import MethodType, SimpleNamespace
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db.models import Sum
from rest_framework.test import APIClient

from allapp.accounts.models import UserRoleScope
from allapp.inventory.models import InventoryDetail, InventoryTransaction
from allapp.locations.models import Warehouse
from allapp.tasking import test_relocation, test_replenishment
from allapp.tasking.models import RelocationRequest, ReplenishmentRequest, WmsTask


@pytest.fixture(params=["relocation", "replenishment"])
def flow(request, db, settings):
    for flag in (
        "RELOCATION_REQUEST_ENABLED",
        "RELOCATION_PDA_ENABLED",
        "RELOCATION_CONTAINER_ENABLED",
        "REPLENISHMENT_MANUAL_ENABLED",
        "REPLENISHMENT_PDA_ENABLED",
        "REPLENISHMENT_MINMAX_ENABLED",
    ):
        setattr(settings, flag, True)
    obj = SimpleNamespace(kind=request.param)
    if obj.kind == "relocation":
        test_relocation.RelocationFlowTests.setUp(obj)
        obj.source_detail = test_relocation.RelocationFlowTests.detail(obj, qty="10")
        obj.request_payload = {
            "owner_id": obj.owner.pk,
            "warehouse_id": obj.warehouse.pk,
            "mode": "LAYER",
            "reason": "API acceptance",
            "lines": [
                {
                    "inventory_detail_id": obj.source_detail.pk,
                    "qty": "10",
                    "to_location_id": obj.target.pk,
                }
            ],
        }
    else:
        test_replenishment.ReplenishmentFlowTests.setUp(obj)
        obj._detail = MethodType(test_replenishment.ReplenishmentFlowTests._detail, obj)
        obj.source_detail = obj._detail(qty="30", batch="SOURCE", expiry=datetime.date(2030, 1, 1))
        obj._detail(qty="5", batch="PICK", expiry=datetime.date(2030, 1, 1), location=obj.pick)
        obj.policy = test_replenishment.ReplenishmentFlowTests._policy(obj)
        obj.source, obj.target = obj.storage, obj.pick
        obj.request_payload = {
            "policy_id": obj.policy.pk,
            "requested_qty": "10",
            "reason": "API acceptance",
        }
    obj.base = f"/api/{obj.kind}/"
    obj.manager = get_user_model().objects.create_user(username=f"{obj.kind}-manager")
    obj.outsider = get_user_model().objects.create_user(username=f"{obj.kind}-outsider")
    other_wh = Warehouse.objects.create(code="OUTSIDE", name="Other warehouse")
    for user, role, warehouse in (
        (obj.user, UserRoleScope.Role.WAREHOUSE_OPERATOR, obj.warehouse),
        (obj.manager, UserRoleScope.Role.WAREHOUSE_MANAGER, obj.warehouse),
        (obj.outsider, UserRoleScope.Role.WAREHOUSE_MANAGER, other_wh),
    ):
        UserRoleScope.objects.create(user=user, role=role, warehouse=warehouse)
        user.user_permissions.set(Permission.objects.filter(content_type__app_label="tasking"))
    obj.client = APIClient()
    obj.client.force_authenticate(obj.user)
    return obj


def call(flow, path, payload=None, *, code=200, method="post", user=None):
    if user is not None:
        flow.client.force_authenticate(user)
    response = getattr(flow.client, method)(flow.base + path, payload or {}, format="json")
    assert response.status_code == code, response.data
    return response.data


def released(flow):
    row = call(flow, "requests/", flow.request_payload, code=201, user=flow.user)
    reviewed = call(flow, f"requests/{row['id']}/approve/", user=flow.manager)
    return WmsTask.objects.get(pk=reviewed["generated_task"])


def started(flow):
    task = released(flow)
    call(flow, f"pda/tasks/{task.pk}/claim/", user=flow.user)
    call(flow, f"pda/tasks/{task.pk}/start/")
    return task


def scan(flow, task, qty=4, key="scan-operation-0001"):
    return {
        "request_id": key,
        "line_id": task.lines.get().pk,
        "from_location_code": flow.source.code,
        "to_location_code": flow.target.code,
        "product_code": flow.product.code,
        "qty": qty,
    }


def balance(flow, location):
    return InventoryDetail.objects.filter(product=flow.product, location=location).aggregate(
        qty=Sum("onhand_qty")
    )["qty"] or Decimal(0)


def test_scans_4_4_2_post_once_and_replay_is_stable(flow):
    task = started(flow)
    original = (balance(flow, flow.source), balance(flow, flow.target))
    path = f"pda/tasks/{task.pk}/record/"
    for index, (qty, total) in enumerate(((4, 4), (4, 8), (2, 10))):
        payload = scan(flow, task, qty, f"scan-operation-{index:04}")
        result = call(flow, path, payload)
        assert Decimal(result["task"]["lines"][0]["qty_done"]) == total
        replay = call(flow, path, payload)
        assert replay["idempotent"] is True
        if total < 10:
            assert (balance(flow, flow.source), balance(flow, flow.target)) == original
            assert not InventoryTransaction.objects.filter(
                src_model="WmsTask", src_id=task.pk
            ).exists()
    assert balance(flow, flow.source) == original[0] - 10
    assert balance(flow, flow.target) == original[1] + 10
    assert InventoryTransaction.objects.filter(src_model="WmsTask", src_id=task.pk).count() == 2
    assert task.scan_logs.count() == 3


@pytest.mark.parametrize(
    "field,value",
    [
        ("qty", 0),
        ("qty", 11),
        ("from_location_code", "WRONG"),
        ("to_location_code", "WRONG"),
        ("product_code", "WRONG"),
        ("serial_no", "UNEXPECTED"),
    ],
)
def test_invalid_scan_does_not_change_stock_or_progress(flow, field, value):
    task = started(flow)
    before = balance(flow, flow.source)
    call(flow, f"pda/tasks/{task.pk}/record/", {**scan(flow, task), field: value}, code=400)
    assert task.lines.get().qty_done == 0
    assert task.scan_logs.count() == 0
    assert balance(flow, flow.source) == before


def test_changed_payload_same_key_conflicts(flow):
    task = started(flow)
    call(flow, f"pda/tasks/{task.pk}/record/", scan(flow, task))
    call(flow, f"pda/tasks/{task.pk}/record/", scan(flow, task, qty=3), code=409)
    assert task.lines.get().qty_done == 4
    assert task.scan_logs.count() == 1


def test_claim_start_scope_and_role_boundaries(flow):
    task = released(flow)
    path = f"pda/tasks/{task.pk}/"
    call(flow, path, method="get", user=flow.outsider, code=404)
    call(flow, path + "claim/", user=flow.manager, code=403)
    call(flow, path + "start/", user=flow.user, code=403)
    call(flow, path + "record/", scan(flow, task), code=400)
    call(flow, path + "claim/")
    call(flow, path + "claim/", code=400)
    call(flow, path + "start/")
    call(flow, path + "retry-posting/", user=flow.manager, code=400)
    call(flow, path + "record/", scan(flow, task), code=403)
    assert task.assignments.filter(finished_at__isnull=True).count() == 1
    assert task.lines.get().qty_done == 0


@pytest.mark.parametrize("action", ["approve", "reject"])
def test_request_review_list_detail_and_scope(flow, action):
    row = call(flow, "requests/", flow.request_payload, code=201)
    path = f"requests/{row['id']}/"
    assert call(flow, path, method="get")["status"] == "PENDING"
    assert len(call(flow, "requests/", method="get")) == 1
    call(flow, path + action + "/", code=403)
    call(flow, path, method="get", user=flow.outsider, code=404)
    call(flow, path + action + "/", {"note": "reviewed"}, user=flow.manager)
    expected = "APPROVED" if action == "approve" else "REJECTED"
    assert call(flow, path, method="get")["status"] == expected
    call(flow, path + "reject/", {"note": "again"}, code=400)


def test_feature_disabled_and_unscoped_account_rejected(flow, settings):
    flag = (
        "RELOCATION_REQUEST_ENABLED"
        if flow.kind == "relocation"
        else "REPLENISHMENT_MANUAL_ENABLED"
    )
    setattr(settings, flag, False)
    call(flow, "requests/", flow.request_payload, code=503)
    setattr(settings, flag, True)
    nobody = get_user_model().objects.create_user(username="unscoped")
    call(flow, "requests/", flow.request_payload, user=nobody, code=403)
    model = RelocationRequest if flow.kind == "relocation" else ReplenishmentRequest
    assert not model.objects.exists()


def test_task_list_filters(flow):
    task = released(flow)
    result = call(
        flow,
        "pda/tasks/",
        {"search": task.task_no, "status": "RELEASED"},
        method="get",
        user=flow.user,
    )
    rows = result.get("results", []) if isinstance(result, dict) else result
    assert [row["id"] for row in rows] == [task.pk]
    result = call(flow, "pda/tasks/", {"search": "NO_SUCH_TASK"}, method="get")
    rows = result.get("results", []) if isinstance(result, dict) else result
    assert rows == []


def test_posting_failure_keeps_scan_and_manager_can_retry(flow):
    task = started(flow)
    path = f"pda/tasks/{task.pk}/"
    original = balance(flow, flow.source)
    flow.client.raise_request_exception = False
    # Fail inside the real posting transaction so its normal rollback and
    # failure journal run, rather than manufacturing inconsistent statuses.
    with patch(
        "allapp.tasking.plugins.handlers.inv_services.post_task",
        side_effect=RuntimeError("injected posting outage"),
    ):
        response = flow.client.post(
            flow.base + path + "record/", scan(flow, task, qty=10), format="json"
        )
        assert response.status_code in (409, 500)
    assert balance(flow, flow.source) == original
    assert task.lines.get().qty_done == 10
    assert task.scan_logs.count() == 1
    call(flow, path + "retry-posting/", user=flow.manager)
    assert balance(flow, flow.source) == original - 10
    assert InventoryTransaction.objects.filter(src_model="WmsTask", src_id=task.pk).count() == 2


@pytest.mark.parametrize("flow", ["relocation"], indirect=True)
def test_four_piece_direct_release_changes_10_to_6_and_0_to_4(flow):
    payload = {**flow.request_payload, "lines": [{**flow.request_payload["lines"][0], "qty": "4"}]}
    call(flow, "direct-release/", payload, code=403)
    result = call(flow, "direct-release/", payload, user=flow.manager, code=201)
    task = WmsTask.objects.get(pk=result["id"])
    call(flow, f"pda/tasks/{task.pk}/claim/", user=flow.user)
    call(flow, f"pda/tasks/{task.pk}/start/")
    call(flow, f"pda/tasks/{task.pk}/record/", scan(flow, task))
    call(flow, f"pda/tasks/{task.pk}/record/", scan(flow, task))
    assert balance(flow, flow.source) == 6
    assert balance(flow, flow.target) == 4
    assert call(flow, f"pda/tasks/{task.pk}/", method="get")["posting_status"] == "POSTED"
    call(
        flow, f"pda/tasks/{task.pk}/void/", {"note": "already posted"}, user=flow.manager, code=400
    )


@pytest.mark.parametrize("flow", ["relocation"], indirect=True)
def test_exception_resume_and_void_preserve_stock_and_release_locks(flow):
    task = started(flow)
    path = f"pda/tasks/{task.pk}/"
    call(flow, path + "record/", scan(flow, task))
    call(flow, path + "report-exception/", {"note": "source damaged"})
    call(flow, path + "record/", scan(flow, task, key="another-operation"), code=400)
    call(flow, path + "resume/", code=403)
    call(flow, path + "void/", {"note": "cancel"}, code=403)
    call(flow, path + "resume/", user=flow.manager)
    call(flow, path + "resume/", code=400)
    call(flow, path + "void/", code=400)
    call(flow, path + "void/", {"note": "cancel"})
    flow.source_detail.refresh_from_db()
    assert flow.source_detail.locked_qty == 0
    assert flow.source_detail.onhand_qty == 10
    assert not InventoryTransaction.objects.filter(src_model="WmsTask", src_id=task.pk).exists()
    assert task.scan_logs.get().status == "IGNORED"


@pytest.mark.parametrize("flow", ["relocation"], indirect=True)
def test_request_cancel_and_options_are_scoped(flow):
    result = call(
        flow,
        "options/",
        {"warehouse_id": flow.warehouse.pk, "owner_id": flow.owner.pk, "search": flow.product.code},
        method="get",
    )
    assert [row["id"] for row in result["inventory"]] == [flow.source_detail.pk]
    call(
        flow,
        "options/",
        {"warehouse_id": flow.warehouse.pk},
        method="get",
        user=flow.outsider,
        code=403,
    )
    row = call(flow, "requests/", flow.request_payload, user=flow.user, code=201)
    call(flow, f"requests/{row['id']}/cancel/")
    assert RelocationRequest.objects.get(pk=row["id"]).status == "CANCELLED"
    call(flow, f"requests/{row['id']}/approve/", user=flow.manager, code=400)


@pytest.mark.parametrize("flow", ["replenishment"], indirect=True)
def test_policy_management_and_scoped_evaluation(flow):
    path = f"policies/{flow.policy.pk}/"
    call(flow, path, {"remark": "operator cannot change policy"}, method="patch", code=403)
    call(flow, path, {"remark": "reviewed"}, method="patch", user=flow.manager)
    assert call(flow, path, method="get")["remark"] == "reviewed"
    call(flow, path, method="get", user=flow.outsider, code=404)
    empty = call(flow, "evaluate/", {"policy_id": flow.policy.pk})
    assert empty["results"] == []
    result = call(
        flow,
        "evaluate/",
        {
            "policy_id": flow.policy.pk,
            "owner_id": flow.owner.pk,
            "warehouse_id": flow.warehouse.pk,
            "product_id": flow.product.pk,
        },
        user=flow.manager,
    )
    task = WmsTask.objects.get(pk=result["results"][0]["task_id"])
    assert task.lines.aggregate(total=Sum("qty_plan"))["total"] == 20
    result = call(flow, "evaluate/", {"policy_id": flow.policy.pk})
    assert not result["results"][0]["task_id"]
    assert WmsTask.objects.filter(task_type=WmsTask.TaskType.REPLEN).count() == 1


@pytest.mark.parametrize("flow", ["replenishment"], indirect=True)
def test_policy_create_has_scope_and_model_validation(flow):
    flow.policy.delete()
    payload = {
        "owner": flow.owner.pk,
        "warehouse": flow.warehouse.pk,
        "product": flow.product.pk,
        "target_location": flow.pick.pk,
        "min_qty": "10",
        "target_qty": "25",
        "replenish_uom": flow.case.pk,
        "source_zone_type": flow.storage.zone_type,
    }
    call(flow, "policies/", payload, user=flow.outsider, code=403)
    row = call(flow, "policies/", payload, user=flow.manager, code=201)
    assert Decimal(row["target_qty"]) == 25
    assert call(flow, "policies/", method="get")


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("flow", ["replenishment"], indirect=True)
def test_two_concurrent_evaluators_create_only_twenty_units(flow):
    import concurrent.futures
    import threading

    from django.db import connections

    from allapp.tasking.replenishment import evaluate_policy

    assert not WmsTask.objects.filter(task_type=WmsTask.TaskType.REPLEN).exists()
    assert balance(flow, flow.target) == 5
    assert flow.source_detail.available_qty >= 20
    barrier = threading.Barrier(2, timeout=10)

    def evaluate():
        try:
            barrier.wait()
            return evaluate_policy(flow.policy.pk, by_user=flow.manager)["created"]
        finally:
            connections.close_all()

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(evaluate) for _ in range(2)]
        results = [future.result(timeout=30) for future in futures]
    assert sorted(results) == [False, True]
    tasks = WmsTask.objects.filter(task_type=WmsTask.TaskType.REPLEN)
    assert tasks.count() == 1
    assert tasks.get().lines.aggregate(total=Sum("qty_plan"))["total"] == 20
