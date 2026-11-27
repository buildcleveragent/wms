"""Check the load driver's role payloads and response checks against the actual WMS HTTP API."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from django.db import connection

from allapp.accounts.models import UserRoleScope
from allapp.inbound import test_order_and_pda_api as inbound
from allapp.pos import tests as pos
from allapp.pos.models import PosSale
from allapp.reports import tests as reports
from allapp.salesapp import tests as mall
from allapp.tasking.models import TaskAssignment, WmsTask, WmsTaskLine
from scripts.wms_capacity import Session, business_call, load, save
from scripts.wms_capacity_reconciliation import capture, verify


@pytest.mark.django_db(transaction=True)
def test_synthetic_users_can_perform_the_capacity_operations(live_server, tmp_path):
    # Reuse the same domain-specific setup as the corresponding API tests.
    warehouse = inbound.InboundOrderAndPdaApiTests()
    warehouse.setUp()
    operator = warehouse.warehouse_user("capacity-operator")
    buyer = warehouse.owner_user("capacity-owner", UserRoleScope.Role.OWNER_SALESPERSON)
    buyer.user_permissions.add(
        warehouse.permission("inbound", "view_inboundorder"),
        warehouse.permission("inbound", "submit_as_owner_buyers"),
    )
    order = warehouse.create_order(created_by=buyer, order_no="CAPACITY-PO")
    received = warehouse.create_task(
        task_no="CAPACITY-RECEIVED",
        task_type=WmsTask.TaskType.RECEIVE,
        status=WmsTask.Status.COMPLETED,
        review_status=WmsTask.ReviewStatus.APPROVED,
        posting_status=WmsTask.PostingStatus.POSTED,
        owner=warehouse.owner,
        warehouse=warehouse.warehouse,
        source_app="inbound",
        source_model="InboundOrder",
        source_pk=str(order.pk),
    )
    task = warehouse.create_task(
        task_no="CAPACITY-PUTAWAY",
        task_type=WmsTask.TaskType.PUTAWAY,
        status=WmsTask.Status.IN_PROGRESS,
        owner=warehouse.owner,
        warehouse=warehouse.warehouse,
        source_app="tasking",
        source_model="WmsTask",
        source_pk=str(received.pk),
    )
    line = WmsTaskLine.objects.create(
        task=task,
        product=warehouse.product,
        from_location=warehouse.receive_location,
        qty_plan=250,
        status=WmsTaskLine.Status.RELEASED,
    )
    TaskAssignment.objects.create(task=task, assignee=operator)

    checkout = pos.PosApiTests()
    checkout.setUp()
    storefront = mall.SaleMiniApiTests()
    storefront.setUp()
    added = storefront.client.post(
        "/api/sale-mini/cart/add/",
        {"product_id": storefront.product.pk, "qty": "1.000", "order_uom": storefront.uom.code},
        format="json",
    )
    assert added.status_code == 200 and added.data["line_count"] == 1
    dashboard = reports.BossDashboardApiTests()
    dashboard.setUp()

    actors = [
        (
            operator,
            "x",
            "pda",
            warehouse.warehouse.pk,
            dict(task_id=task.pk, line_id=line.pk, to_location_id=warehouse.putaway_location.pk),
        ),
        (
            buyer,
            "x",
            "owner",
            warehouse.warehouse.pk,
            dict(order_id=order.pk, owner_id=order.owner_id),
        ),
        (checkout.user, "x", "pos", checkout.warehouse.pk, dict(product_id=checkout.product.pk)),
        (
            storefront.user,
            "pw",
            "mall",
            storefront.warehouse.pk,
            dict(owner_id=storefront.owner.pk),
        ),
        (dashboard.user, "x", "reports", dashboard.warehouse.pk, {}),
    ]
    writes = {}
    started_at = datetime.now(timezone.utc).isoformat()
    for index, (user, password, role, warehouse_id, fields) in enumerate(actors, 1):
        actor = dict(
            username=user.username,
            password=password,
            role=role,
            warehouse_id=warehouse_id,
            **fields,
        )
        session = Session(actor, live_server.url, index)
        session.preflight(required_units=240 if role == "pda" else 0)
        session.authenticate()  # Refresh the token through the same endpoint as the measured phase.
        read, _ = business_call(session.client, actor, False, "")
        assert read.ok, (role, read.status)
        if role in ("pda", "pos"):
            key = f"cap-http-test-50-{index}-1"
            write, _ = business_call(session.client, actor, True, key)
            assert write.ok, (role, write.status)
            writes[role, warehouse_id, key] = "ok"
    line.refresh_from_db()
    assert line.qty_done == 1
    assert PosSale.objects.filter(warehouse=checkout.warehouse).count() == 1

    # Read back the real audit/receipt keys and run the native reconciliation on this fixture.
    finished_at = datetime.now(timezone.utc).isoformat()
    row = dict(users=50, started_at=started_at, finished_at=finished_at)
    with connection.cursor() as cursor:
        cursor.execute("SELECT @@server_uuid")
        server_uuid = cursor.fetchone()[0]
    path = tmp_path / "client.json"
    save(path, dict(run_id="http-test", deployment_identity="fixture", runs=[row]))
    output = tmp_path / "checkpoint.json"
    capture(
        SimpleNamespace(
            client=path,
            users=50,
            as_of=finished_at,
            snapshot_db="wms_db_test",
            snapshot_server_uuid=server_uuid,
            snapshot_id="fixture",
            output=output,
        )
    )
    checkpoint = load(output)
    counts = verify(checkpoint, row, "http-test", "fixture", writes)
    assert counts["inventory"] == checkpoint["reconciliation"]["inventory"]["issue_count"]
    assert counts["billing"] == checkpoint["reconciliation"]["billing"]["issue_count"]
