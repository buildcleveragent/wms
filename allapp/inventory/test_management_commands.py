import datetime
from io import StringIO
from unittest import mock

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase

pytestmark = pytest.mark.unit


class InventorySnapshotManagementCommandTests(SimpleTestCase):
    def test_inventory_generate_snapshot_resolves_unique_dates_and_scope(self):
        out = StringIO()
        summary = {
            "service_dates": [
                datetime.date(2026, 5, 1),
                datetime.date(2026, 5, 2),
            ],
            "rows_created": 7,
            "scopes_processed": 2,
            "days": [
                {
                    "service_date": datetime.date(2026, 5, 1),
                    "mode": "bootstrap",
                    "rows_created": 3,
                    "scopes_processed": 1,
                },
                {
                    "service_date": datetime.date(2026, 5, 2),
                    "mode": "roll_forward",
                    "rows_created": 4,
                    "scopes_processed": 1,
                },
            ],
        }

        with mock.patch(
            "allapp.inventory.management.commands.inventory_generate_snapshot.generate_inventory_snapshots_for_dates",
            return_value=summary,
        ) as mocked_generate:
            call_command(
                "inventory_generate_snapshot",
                "--date",
                "2026-05-02",
                "--date",
                "2026-05-01",
                "--date",
                "2026-05-01",
                "--owner",
                "11",
                "--warehouse",
                "22",
                "--bootstrap",
                stdout=out,
            )

        mocked_generate.assert_called_once_with(
            [datetime.date(2026, 5, 1), datetime.date(2026, 5, 2)],
            owner_id=11,
            warehouse_id=22,
            bootstrap_first=True,
        )
        self.assertIn("rows_created=7", out.getvalue())
        self.assertIn("mode=bootstrap", out.getvalue())

    def test_inventory_generate_snapshot_resolves_date_range(self):
        with mock.patch(
            "allapp.inventory.management.commands.inventory_generate_snapshot.generate_inventory_snapshots_for_dates",
            return_value={
                "service_dates": [
                    datetime.date(2026, 5, 1),
                    datetime.date(2026, 5, 2),
                    datetime.date(2026, 5, 3),
                ],
                "rows_created": 0,
                "scopes_processed": 0,
                "days": [],
            },
        ) as mocked_generate:
            call_command(
                "inventory_generate_snapshot",
                "--date-from",
                "2026-05-01",
                "--date-to",
                "2026-05-03",
                stdout=StringIO(),
            )

        self.assertEqual(
            mocked_generate.call_args.args[0],
            [
                datetime.date(2026, 5, 1),
                datetime.date(2026, 5, 2),
                datetime.date(2026, 5, 3),
            ],
        )

    def test_inventory_generate_snapshot_rejects_invalid_date_options(self):
        with self.assertRaises(CommandError):
            call_command(
                "inventory_generate_snapshot",
                "--date",
                "2026-05-01",
                "--date-from",
                "2026-05-01",
                "--date-to",
                "2026-05-02",
            )

        with self.assertRaises(CommandError):
            call_command(
                "inventory_generate_snapshot",
                "--date-from",
                "2026-05-03",
                "--date-to",
                "2026-05-01",
            )
