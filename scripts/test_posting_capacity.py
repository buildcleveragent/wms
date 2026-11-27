"""Opt-in MySQL experiment comparing one hot warehouse with two warehouses."""

import json
import os
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from time import perf_counter

import pytest
from django.db import connection, connections

from allapp.inventory.models import InventoryDetail, InventorySummary, InventoryTransaction
from allapp.inventory.services import post_task
from allapp.locations.models import Location, Subwarehouse, Warehouse
from allapp.products.models import Product
from allapp.tasking.models import TaskScanLog, WmsTask, WmsTaskLine
from allapp.tasking.test_count_acceptance import case as stock_fixture
from scripts.putaway_load_test import percentile

case = stock_fixture
POSTS_PER_WORKER = 5


def receipt(product, location, number):
    reference = f"{product.code}-{number}"
    task = WmsTask.objects.create(
        owner_id=product.owner_id,
        warehouse_id=location.warehouse_id,
        task_no=reference,
        task_type=WmsTask.TaskType.RECEIVE,
        status=WmsTask.Status.COMPLETED,
        review_status=WmsTask.ReviewStatus.APPROVED,
        posting_status=WmsTask.PostingStatus.PENDING,
    )
    line = WmsTaskLine.objects.create(
        task=task,
        product=product,
        to_location=location,
        qty_plan=1,
        qty_done=1,
        status=WmsTaskLine.Status.COMPLETED,
    )
    scan = TaskScanLog.objects.create(
        owner_id=product.owner_id,
        warehouse_id=location.warehouse_id,
        task=task,
        task_line=line,
        product=product,
        location=location,
        status=TaskScanLog.ScanStatus.OK,
        qty_base_delta=1,
        fp=reference,
        scan_snapshot_rev=0,
    )
    return task, scan


def post_receipts(jobs, start):
    # Each worker owns its database connection, including the SQL timing hook.
    warehouse_table = connection.ops.quote_name(Warehouse._meta.db_table)
    lock_queries = []

    def time_warehouse_lock(execute, sql, params, many, context):
        if f"FROM {warehouse_table} " in sql and "FOR UPDATE" in sql:
            began = perf_counter()
            try:
                return execute(sql, params, many, context)
            finally:
                lock_queries.append((perf_counter() - began) * 1000)
        return execute(sql, params, many, context)

    samples = []
    try:
        connection.ensure_connection()
        start.wait(timeout=30)
        with connection.execute_wrapper(time_warehouse_lock):
            for task, scan in jobs:
                lock_queries.clear()
                began = perf_counter()
                result = post_task(task=task, scans=[scan], batch_no=task.task_no)
                ended = perf_counter()
                assert result["affected_tx_count"] == 1 and len(lock_queries) == 1
                samples.append(
                    {
                        "task": task.task_no,
                        "start": began,
                        "end": ended,
                        "post_ms": (ended - began) * 1000,
                        "warehouse_lock_query_ms": lock_queries[0],
                    }
                )
        return samples
    finally:
        connections.close_all()


def measure(case, locations, workers, scenario):
    jobs = []
    products = []
    receipts = []
    for worker in range(workers):
        # A distinct product per worker prevents inventory-summary contention across lanes.
        location = locations[worker % len(locations)]
        code = f"CAP-{workers}-{len(locations)}-{worker}"
        product = Product.objects.create(
            owner=case.owner, code=code, sku=code, name=code, base_uom=case.uom
        )
        products.append(product.pk)
        lane = [receipt(product, location, n) for n in range(POSTS_PER_WORKER + 1)]
        receipts.extend(lane)
        warm_task, warm_scan = lane[0]
        post_task(task=warm_task, scans=[warm_scan], batch_no=warm_task.task_no)
        jobs.append(lane[1:])

    start = Barrier(workers)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(post_receipts, lane, start) for lane in jobs]
        samples = [sample for future in futures for sample in future.result()]
    elapsed = max(s["end"] for s in samples) - min(s["start"] for s in samples)

    expected_ledger = Counter((t.pk, s.product_id, t.warehouse_id, 1) for t, s in receipts)
    ledger = InventoryTransaction.objects.filter(product_id__in=products)
    actual_ledger = Counter(ledger.values_list("src_id", "product_id", "warehouse_id", "qty_delta"))
    ledger_errors = (actual_ledger - expected_ledger).total() + (
        expected_ledger - actual_ledger
    ).total()
    expected_stock = Counter((pk, POSTS_PER_WORKER + 1, POSTS_PER_WORKER + 1) for pk in products)
    stock_errors = 0
    for model in (InventoryDetail, InventorySummary):
        quantities = model.objects.filter(product_id__in=products).values_list(
            "product_id", "onhand_qty", "available_qty"
        )
        actual_stock = Counter(quantities)
        stock_errors += (actual_stock - expected_stock).total() + (
            expected_stock - actual_stock
        ).total()
    return {
        "scenario": scenario,
        "workers": workers,
        "warehouses": len(locations),
        "postings": len(samples),
        "postings_per_second": len(samples) / elapsed,
        "post_p95_ms": percentile([s["post_ms"] for s in samples], 0.95),
        "warehouse_lock_query_p95_ms": percentile(
            [s["warehouse_lock_query_ms"] for s in samples], 0.95
        ),
        "ledger_mismatches": ledger_errors,
        "stock_mismatches": stock_errors,
        "samples": [
            {k: v for k, v in sample.items() if k not in ("start", "end")} for sample in samples
        ],
    }


@pytest.mark.django_db(transaction=True)
@pytest.mark.skipif(
    not os.getenv("WMS_POSTING_BENCHMARK_REPORT"), reason="opt-in capacity experiment"
)
def test_same_and_cross_warehouse_posting(case):
    with connection.cursor() as cursor:
        cursor.execute("SELECT DATABASE()")
        assert connection.vendor == "mysql" and cursor.fetchone()[0] == "wms_db_test"
    second = Warehouse.objects.create(code="CAP-WH2", name="Capacity second warehouse")
    subwarehouse = Subwarehouse.objects.create(warehouse=second, code="CAPSW", name="Capacity")
    other = Location.objects.create(
        warehouse=second, subwarehouse=subwarehouse, code="CAPSW-01-01-01", name="Capacity"
    )
    runs = []
    for workers in (4, 8):
        runs.append(measure(case, [case.location], workers, "same-warehouse-hotspot"))
        runs.append(measure(case, [case.location, other], workers, "cross-warehouse"))
    report = {
        "workload": "One SKU per worker, one unit per inventory receipt posting",
        "warehouse_lock_query_ms": "Warehouse SELECT FOR UPDATE including the database round trip",
        "warmup": "One unmeasured receipt per worker before each run",
        "runs": runs,
    }
    path = Path(os.environ["WMS_POSTING_BENCHMARK_REPORT"])
    path.write_text(json.dumps(report, indent=2) + "\n")
    for run in runs:
        assert run["stock_mismatches"] == run["ledger_mismatches"] == 0, run
