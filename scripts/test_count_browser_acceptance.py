"""Explicit real-browser acceptance; run with pytest --reuse-db and MySQL 8.

WMS_BROWSER_ARTIFACTS must name an external artifacts directory. All HTTP writes
are routed to pytest-django's isolated LiveServer, never the application's
configured LAN backend. This opt-in file is outside default testpaths.
"""

import json
import os
import subprocess
import time
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest
from django.conf import settings as django_settings

from allapp.inventory import services
from allapp.inventory.models import InventoryDetail, InventorySummary, InventoryTransaction
from allapp.tasking.models import CountScopeLock, WmsTask
from allapp.tasking.test_count_acceptance import case as count_case_fixture
from allapp.tasking.test_count_acceptance import detail, operator, release
from wmsmaster.test_database import validate_test_process, verify_test_database

ROOT = Path(__file__).resolve().parents[1]
case = count_case_fixture


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "scenario",
    ["closure-390", "closure-1280", "no-difference", "reject-cancel", "expiry", "thousand"],
)
def test_real_count_browser(case, live_server, settings, scenario):
    validate_test_process(django_settings.DATABASES["default"])
    identity = verify_test_database(django_settings.DATABASES["default"])
    settings.ALLOWED_HOSTS = ["127.0.0.1", "localhost", "testserver"]
    if scenario == "reject-cancel":
        settings.COUNT_MAX_TIMES = 1
    worker = operator(case, "count-browser-worker")
    artifacts = Path(os.environ["WMS_BROWSER_ARTIFACTS"]).resolve() / scenario
    assert not artifacts.is_relative_to(ROOT), "Evidence must be outside the checkout"
    artifacts.mkdir(parents=True, exist_ok=True)
    payload = {
        **identity,
        "backend": live_server.url,
        "scenario": scenario,
        "artifacts": str(artifacts),
        "manager": case.manager.username,
        "operator": worker.username,
        "warehouse": case.warehouse.name,
        "owner": case.owner.name,
    }
    if scenario == "expiry":
        case.detail.batch_no = "SAME"
        case.detail.expiry_date = date(2030, 1, 1)
        case.detail.save()
        detail(case, batch="SAME", expiry=date(2031, 1, 1), qty="9")
        task = release(case)
        payload.update(task_id=task.pk, task_no=task.task_no)
    if scenario == "thousand":
        InventoryDetail.objects.bulk_create(
            [
                InventoryDetail(
                    owner=case.owner,
                    warehouse=case.warehouse,
                    subwarehouse=case.subwarehouse,
                    location=case.location,
                    product=case.product,
                    batch_no=f"LOAD-{index:04d}",
                    onhand_qty=1,
                    available_qty=1,
                    base_unit=case.uom.code,
                )
                for index in range(1, 1000)
            ],
            batch_size=250,
        )
        started = time.monotonic()
        task = release(case)
        payload.update(task_id=task.pk, task_no=task.task_no)
        (artifacts / "setup-timing.json").write_text(
            json.dumps({"create_and_release_1000_lines_seconds": time.monotonic() - started})
        )
    original = services._apply_receive_like
    remaining_failures = int(scenario.startswith("closure"))

    def fail_first_post_after_stock_write(*args, **kwargs):
        nonlocal remaining_failures
        result = original(*args, **kwargs)
        if remaining_failures:
            remaining_failures -= 1
            raise RuntimeError("Injected count acceptance posting failure")
        return result

    with patch.object(
        services, "_apply_receive_like", side_effect=fail_first_post_after_stock_write
    ):
        process = subprocess.run(
            ["node", str(ROOT / "scripts/count-browser-acceptance.mjs")],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            timeout=240,
        )
    (artifacts / "browser.log").write_text(process.stdout + process.stderr)
    result_path = artifacts / "result.json"
    assert result_path.exists(), "Browser harness failed before writing its sanitized result"
    result = json.loads(result_path.read_text())
    if result.get("flow_complete"):
        case.detail.refresh_from_db()
        if scenario.startswith("closure"):
            assert case.detail.onhand_qty == 4
            assert (
                InventorySummary.objects.get(owner=case.owner, product=case.product).onhand_qty == 4
            )
            assert InventoryTransaction.objects.count() == 1
            assert InventoryTransaction.objects.get().qty_delta == -1
            assert WmsTask.objects.filter(posting_status="POSTED").count() == 1
            assert not CountScopeLock.objects.filter(released_at__isnull=True).exists()
        elif scenario in ("no-difference", "reject-cancel"):
            assert case.detail.onhand_qty == 5
            assert not InventoryTransaction.objects.exists()
            assert not CountScopeLock.objects.filter(released_at__isnull=True).exists()
        elif scenario == "thousand":
            assert task.lines.count() == 1000
            assert task.scan_logs.filter(status="OK").count() == 1
            assert task.lines.order_by("id").last().countlineextra.qty_counted == Decimal("7.0001")
            assert not InventoryTransaction.objects.exists()
        elif scenario == "expiry":
            assert list(
                InventoryDetail.objects.order_by("expiry_date").values_list("onhand_qty", flat=True)
            ) == [5, 9]
            assert not InventoryTransaction.objects.exists()
            assert task.lines.count() == 2
        result["database_assertions"] = "PASS"
        result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    assert process.returncode == 0, json.dumps(result, ensure_ascii=False)
