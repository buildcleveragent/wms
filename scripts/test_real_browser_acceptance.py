"""Opt-in real HTTP/browser acceptance against synthetic MySQL test data."""

import datetime
import json
import os
import subprocess
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings

from allapp.accounts.models import UserRoleScope
from allapp.outbound.models import OutboundOrder
from allapp.salesapp import tests as sales_fixtures

ROOT = Path(__file__).resolve().parents[1]


@override_settings(ALLOWED_HOSTS=["localhost", "127.0.0.1", "testserver"])
class RealBrowserAcceptanceTests(StaticLiveServerTestCase):
    def test_owner_mall_and_admin_login_against_real_api(self):
        sales_fixtures.SaleMiniApiTests.setUp(self)
        owner_user = get_user_model().objects.create_user(
            username="browser-owner", password="browser-test-password", owner=self.owner
        )
        UserRoleScope.objects.create(
            user=owner_user, owner=self.owner, role=UserRoleScope.Role.OWNER_MANAGER
        )
        get_user_model().objects.create_superuser(
            username="browser-admin", password="browser-test-password"
        )
        artifacts = Path(os.environ["WMS_BROWSER_ARTIFACTS"]).resolve()
        artifacts.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            ["node", str(ROOT / "scripts/real-browser-acceptance.mjs")],
            input=json.dumps({"backend": self.live_server_url, "artifacts": str(artifacts)}),
            text=True,
            capture_output=True,
            timeout=180,
        )
        (artifacts / "browser.log").write_text(result.stdout + result.stderr)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        order = OutboundOrder.objects.get(owner=self.owner)
        line = order.lines.get()
        self.assertEqual(line.product_id, self.product.id)
        self.assertEqual(line.base_qty, Decimal("1"))
        self.assertEqual(line.base_price, Decimal("9.50"))


@override_settings(
    ALLOWED_HOSTS=["localhost", "127.0.0.1", "testserver"],
    RELOCATION_REQUEST_ENABLED=True,
    RELOCATION_PDA_ENABLED=True,
    REPLENISHMENT_MANUAL_ENABLED=True,
    REPLENISHMENT_PDA_ENABLED=True,
)
class MovementBrowserAcceptanceTests(StaticLiveServerTestCase):
    def test_pda_and_android_scan_sequence_with_real_lost_response(self):
        from allapp.inventory.models import InventoryDetail, InventoryTransaction
        from allapp.tasking import relocation, replenishment, test_relocation, test_replenishment
        from allapp.tasking.models import ReplenishmentRequest, WmsTask

        cases, tasks = [], []
        for kind in ("move", "replenish"):
            fixture = SimpleNamespace()
            if kind == "move":
                test_relocation.RelocationFlowTests.setUp(fixture)
            else:
                test_replenishment.ReplenishmentFlowTests.setUp(fixture)
                test_replenishment.ReplenishmentFlowTests._detail(
                    fixture, qty="30", batch="SOURCE", expiry=datetime.date(2030, 1, 1)
                )
                test_replenishment.ReplenishmentFlowTests._detail(
                    fixture,
                    qty="5",
                    batch="PICK",
                    expiry=datetime.date(2030, 1, 1),
                    location=fixture.pick,
                )
                test_replenishment.ReplenishmentFlowTests._policy(fixture)
                fixture.source, fixture.target = fixture.storage, fixture.pick
            fixture.user.warehouse = fixture.warehouse
            fixture.user.save(update_fields=["warehouse"])
            UserRoleScope.objects.create(
                user=fixture.user,
                role=UserRoleScope.Role.WAREHOUSE_OPERATOR,
                warehouse=fixture.warehouse,
            )
            fixture.user.user_permissions.set(
                Permission.objects.filter(content_type__app_label="tasking")
            )
            for app in ("wmspda", "wmsandroid"):
                if kind == "move":
                    detail = InventoryDetail.objects.create(
                        owner=fixture.owner,
                        product=fixture.product,
                        location=fixture.source,
                        warehouse=fixture.warehouse,
                        zone_type=fixture.source.zone_type,
                        batch_no=app,
                        onhand_qty=10,
                        allocated_qty=0,
                        locked_qty=0,
                        damaged_qty=0,
                    )
                    request = relocation.create_layer_request(
                        owner=fixture.owner,
                        warehouse=fixture.warehouse,
                        lines=[
                            {
                                "inventory_detail_id": detail.pk,
                                "qty": "10",
                                "to_location_id": fixture.target.pk,
                            }
                        ],
                        reason="browser acceptance",
                        by_user=fixture.user,
                    )
                    task = relocation.approve_request(request.pk, by_user=fixture.user)
                else:
                    request = ReplenishmentRequest.objects.create(
                        owner=fixture.owner,
                        warehouse=fixture.warehouse,
                        product=fixture.product,
                        target_location=fixture.pick,
                        requested_qty=10,
                        reason="browser acceptance",
                        created_by=fixture.user,
                    )
                    task = replenishment.approve_request(request.pk, by_user=fixture.user)
                tasks.append(task.pk)
                cases.append(
                    {
                        "app": app,
                        "kind": kind,
                        "task_id": task.pk,
                        "task_no": task.task_no,
                        "source": fixture.source.code,
                        "target": fixture.target.code,
                        "product": fixture.product.code,
                        "username": fixture.user.username,
                    }
                )
        artifacts = Path(os.environ["WMS_BROWSER_ARTIFACTS"]).resolve() / "movement"
        artifacts.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            ["node", str(ROOT / "scripts/movement-browser-acceptance.mjs")],
            input=json.dumps(
                {"backend": self.live_server_url, "artifacts": str(artifacts), "cases": cases}
            ),
            text=True,
            capture_output=True,
            timeout=300,
        )
        (artifacts / "browser.log").write_text(result.stdout + result.stderr)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for task_id in tasks:
            task = WmsTask.objects.get(pk=task_id)
            self.assertEqual(task.posting_status, WmsTask.PostingStatus.POSTED)
            self.assertEqual(task.lines.get().qty_done, 10)
            self.assertEqual(task.scan_logs.count(), 3)
            self.assertEqual(
                InventoryTransaction.objects.filter(src_model="WmsTask", src_id=task_id).count(), 2
            )
