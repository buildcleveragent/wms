"""Bounded Boss query checks on synthetic MySQL inventory, outside the default suite."""

import json
import os
import statistics
import time
from pathlib import Path

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from allapp.inventory.models import InventoryDetail
from allapp.reports import tests as report_fixtures


class BossQueryAcceptanceTests(TestCase):
    setUp = report_fixtures.BossDashboardApiTests.setUp

    def test_large_inventory_pagination_and_long_range_query_budget(self):
        InventoryDetail.objects.bulk_create(
            [
                InventoryDetail(
                    owner=self.owner,
                    warehouse=self.warehouse,
                    location=self.location,
                    product=self.product_a,
                    zone_type=self.inventory_a.zone_type,
                    batch_no=f"QA-BOSS-{index:04}",
                    onhand_qty=1,
                    available_qty=1,
                    allocated_qty=0,
                    locked_qty=0,
                    damaged_qty=0,
                )
                for index in range(1000)
            ]
        )
        common = {
            "warehouse": self.warehouse.pk,
            "owner": self.owner.pk,
            "decimal_format": "string",
        }
        samples = []
        query_counts = []
        for size in (20, 100):
            started = time.monotonic()
            with CaptureQueriesContext(connection) as queries:
                response = self.client.get(
                    "/api/reports/boss/inventory/details/", {**common, "page_size": size}
                )
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(len(response.data["results"]), size)
            self.assertGreaterEqual(response.data["count"], 1000)
            query_counts.append(len(queries))
            samples.append(
                {
                    "endpoint": "inventory/details",
                    "page_size": size,
                    "queries": len(queries),
                    "seconds": time.monotonic() - started,
                }
            )
        self.assertLessEqual(query_counts[1], query_counts[0] + 3)
        for endpoint in ("today", "periods/summary", "operations"):
            timings = []
            counts = []
            for _ in range(3):
                started = time.monotonic()
                with CaptureQueriesContext(connection) as queries:
                    response = self.client.get(
                        f"/api/reports/boss/{endpoint}/",
                        {**common, "date_from": "2010-01-01", "date_to": str(self.today)},
                    )
                timings.append(time.monotonic() - started)
                counts.append(len(queries))
                self.assertEqual(response.status_code, 200, response.data)
            samples.append(
                {
                    "endpoint": endpoint,
                    "seconds": timings,
                    "queries": counts,
                    "median_seconds": statistics.median(timings),
                    "max_seconds": max(timings),
                }
            )
            # The client gives an individual request 15 seconds; this is not a production SLO.
            self.assertLess(max(timings), 15, endpoint)
        output = Path(os.environ["WMS_BROWSER_ARTIFACTS"]).resolve()
        output.mkdir(parents=True, exist_ok=True)
        (output / "queries.json").write_text(
            json.dumps({"synthetic_inventory_rows": 1000, "samples": samples}, indent=2)
        )
