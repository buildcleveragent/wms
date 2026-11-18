"""Opt-in same-fixture three-run count benchmark, excluding data preparation."""

import json
import os
import statistics
import time
from pathlib import Path

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from allapp.inventory.models import InventoryDetail
from allapp.tasking import counting
from allapp.tasking.test_count_acceptance import case as count_case_fixture

case = count_case_fixture


@pytest.mark.django_db(transaction=True)
def test_thousand_create_release_median(case):
    assert connection.settings_dict["NAME"] == "wms_db_test"
    artifacts = Path(os.environ["WMS_COUNT_PERFORMANCE_ARTIFACTS"]).resolve()
    assert artifacts.is_relative_to(Path("/tmp"))
    artifacts.mkdir(parents=True, exist_ok=True)
    # Same synthetic fixture as the original real-browser thousand-line baseline.
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
    runs = []
    for _ in range(3):
        with CaptureQueriesContext(connection) as queries:
            start = time.perf_counter()
            task, created, truncated = counting.create_count_task(
                created_by=case.manager,
                owner_id=case.owner.pk,
                warehouse_id=case.warehouse.pk,
            )
            create_seconds = time.perf_counter() - start
        create_queries = len(queries)
        assert created == 1000 and truncated is False
        with CaptureQueriesContext(connection) as queries:
            start = time.perf_counter()
            counting.release_count_task(task.pk, by_user=case.manager)
            release_seconds = time.perf_counter() - start
        assert task.lines.count() == 1000
        runs.append(
            {
                "create_seconds": create_seconds,
                "release_seconds": release_seconds,
                "total_seconds": create_seconds + release_seconds,
                "create_sql_count": create_queries,
                "release_sql_count": len(queries),
            }
        )
        counting.cancel_count_task(task.pk, by_user=case.manager)
    median = statistics.median(run["total_seconds"] for run in runs)
    result = {
        "rows": 1000,
        "database": "wms_db_test",
        "runs": runs,
        "median_seconds": median,
        "limit_seconds": 20,
        "excludes": ["migrations", "fixtures", "password hashing", "cancellation"],
        "status": "PASS" if median <= 20 else "FAIL",
    }
    (artifacts / "performance.json").write_text(json.dumps(result, indent=2))
    assert median <= 20, result
