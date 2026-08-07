import datetime
import gzip
import json
import os
import tempfile
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import SimpleTestCase, TransactionTestCase

from allapp.accounts.models import AuditEvent
from allapp.baseinfo.models import Owner, OwnerWarehouseBinding
from allapp.billing.enums import CalcMethod, ChargeType
from allapp.billing.models import BillingServiceContract
from allapp.core.business_data_purge import canonical_target
from allapp.core.preserved_data_transfer import (
    EXCLUDED_HISTORY_MODEL_LABELS,
    _dump_command,
    _restore_footer,
    _restore_header,
    mysql_defaults_file,
    resolve_transfer_scope,
    validate_dump_sql,
)
from allapp.locations.models import Warehouse
from allapp.products.models import ProductCategory
from allapp.reports.models import OperatingTarget


class PreservedDataTransferUnitTests(SimpleTestCase):
    def test_scope_reuses_preserved_manifest_and_excludes_history(self):
        scope = resolve_transfer_scope()

        self.assertEqual(scope.excluded_model_labels, EXCLUDED_HISTORY_MODEL_LABELS)
        self.assertNotIn("django_migrations", scope.selected_tables)
        self.assertIn("accounts_user", scope.selected_tables)
        self.assertIn("baseinfo_ownerwarehousebinding", scope.selected_tables)
        self.assertIn("billing_billingservicecontract", scope.selected_tables)
        self.assertIn("reports_operatingtarget", scope.selected_tables)
        self.assertIn("products_productcategory", scope.selected_tables)
        self.assertNotIn("accounts_auditevent", scope.selected_tables)
        self.assertNotIn("django_admin_log", scope.selected_tables)
        self.assertNotIn("products_product", scope.selected_tables)

    def test_sql_validator_accepts_only_selected_insert_statements(self):
        with tempfile.TemporaryDirectory() as raw_temp:
            sql_path = Path(raw_temp) / "data.sql.gz"
            with gzip.open(sql_path, "wt", encoding="utf-8") as handle:
                handle.write("-- compact mysqldump\n")
                handle.write("INSERT INTO `accounts_user` (`id`) VALUES (1);\n")

            self.assertEqual(
                validate_dump_sql(
                    sql_path,
                    selected_tables=frozenset({"accounts_user"}),
                    expected_row_counts={"accounts_user": 1},
                ),
                (True, ""),
            )

            with gzip.open(sql_path, "wt", encoding="utf-8") as handle:
                handle.write("DROP TABLE `accounts_user`;\n")
            safe, error = validate_dump_sql(
                sql_path,
                selected_tables=frozenset({"accounts_user"}),
                expected_row_counts={"accounts_user": 1},
            )
            self.assertFalse(safe)
            self.assertIn("不是允许的 INSERT", error)

    def test_mysql_credentials_use_mode_0600_file_and_not_command_arguments(self):
        fake_connection = SimpleNamespace(
            settings_dict={
                "NAME": "wms_db",
                "USER": "wms-user",
                "PASSWORD": "secret-password",
                "HOST": "127.0.0.1",
                "PORT": "3306",
                "OPTIONS": {"charset": "utf8mb4"},
            }
        )
        with tempfile.TemporaryDirectory() as raw_temp:
            with mysql_defaults_file(fake_connection, Path(raw_temp)) as defaults:
                self.assertEqual(os.stat(defaults).st_mode & 0o777, 0o600)
                command = _dump_command(defaults, fake_connection, ["accounts_user"])
                self.assertNotIn("secret-password", " ".join(command))
                self.assertIn("secret-password", defaults.read_text(encoding="utf-8"))
            self.assertFalse(defaults.exists())

    def test_restore_sql_has_delete_transaction_and_count_assertions_only(self):
        header = _restore_header(connection, frozenset({"accounts_user"})).decode()
        footer = _restore_footer(connection, {"accounts_user": 3}).decode()

        self.assertIn("START TRANSACTION", header)
        self.assertIn("DELETE FROM `accounts_user`", header)
        self.assertIn("FOREIGN_KEY_CHECKS = 0", header)
        self.assertIn("COUNT(*) = 3", footer)
        self.assertIn("WMS_ROW_COUNT_MISMATCH", footer)
        self.assertIn("COMMIT", footer)
        self.assertNotIn("CREATE", header + footer)
        self.assertNotIn("DROP", header + footer)
        self.assertNotIn("TRUNCATE", header + footer)

    def test_execute_modes_require_all_confirmation_arguments(self):
        with self.assertRaisesMessage(CommandError, "正式执行缺少参数"):
            call_command("backup_preserved_data", "--execute", stdout=StringIO())
        with self.assertRaisesMessage(CommandError, "正式执行缺少参数"):
            call_command("restore_preserved_data", "--execute", stdout=StringIO())

        output = StringIO()
        call_command("restore_preserved_data", stdout=output)
        self.assertIn("未提供 --input", output.getvalue())


@skipUnless(connection.vendor == "mysql", "preserved data transfer requires MySQL")
class PreservedDataTransferMySQLTests(TransactionTestCase):
    reset_sequences = False

    def _empty_application_tables(self):
        with connection.cursor() as cursor:
            tables = [
                item.name
                for item in connection.introspection.get_table_list(cursor)
                if item.type in {"t", "p"} and item.name != "django_migrations"
            ]
            cursor.execute("SET SESSION FOREIGN_KEY_CHECKS = 0")
            try:
                for table in tables:
                    cursor.execute(f"DELETE FROM {connection.ops.quote_name(table)}")
            finally:
                cursor.execute("SET SESSION FOREIGN_KEY_CHECKS = 1")

    def test_backup_and_restore_round_trip_into_fresh_database(self):
        operator = get_user_model().objects.create_superuser(
            username="preserved-operator",
            email="operator@example.com",
            password="original-password",
        )
        owner = Owner.objects.create(name="Preserved Owner", code="PRESERVED")
        warehouse = Warehouse.objects.create(code="PRESWH", name="Preserved")
        binding = OwnerWarehouseBinding.objects.create(
            owner=owner,
            warehouse=warehouse,
        )
        contract = BillingServiceContract.objects.create(
            owner=owner,
            warehouse=warehouse,
            charge_type=ChargeType.STORAGE,
            calc_method=CalcMethod.PER_CBM_DAY,
            effective_from=datetime.date(2026, 8, 1),
        )
        target = OperatingTarget.objects.create(
            month=datetime.date(2026, 8, 1),
            warehouse=warehouse,
            owner=owner,
            metric=OperatingTarget.Metric.OTIF,
            target_value="95.0000",
            created_by=operator,
        )
        category = ProductCategory.objects.create(
            code="PRESERVED-CATEGORY",
            name="Preserved Category",
            image="product_categories/example.png",
        )
        historical_audit = AuditEvent.objects.create(
            username=operator.username,
            action="HISTORICAL_EVENT",
            module="core.tests",
            event_hash="a" * 64,
        )

        with tempfile.TemporaryDirectory() as raw_temp:
            bundle = Path(raw_temp) / "preserved-backup"
            call_command(
                "backup_preserved_data",
                "--execute",
                "--confirm-target",
                canonical_target(connection),
                "--operator",
                operator.username,
                "--output",
                str(bundle),
                "--maintenance-confirmed",
                stdout=StringIO(),
            )
            manifest = json.loads((bundle / "manifest.json").read_text("utf-8"))
            self.assertEqual(os.stat(bundle).st_mode & 0o777, 0o700)
            self.assertEqual(
                os.stat(bundle / "preserved-data.sql.gz").st_mode & 0o777,
                0o600,
            )
            self.assertIn("accounts.auditevent", manifest["excluded_model_labels"])

            self._empty_application_tables()
            self.assertFalse(get_user_model().objects.exists())

            call_command(
                "restore_preserved_data",
                "--execute",
                "--confirm-target",
                canonical_target(connection),
                "--operator",
                operator.username,
                "--input",
                str(bundle),
                "--maintenance-confirmed",
                "--fresh-database-confirmed",
                stdout=StringIO(),
            )

        restored_user = get_user_model().objects.get(pk=operator.pk)
        self.assertTrue(restored_user.check_password("original-password"))
        self.assertTrue(Owner.objects.filter(pk=owner.pk, code="PRESERVED").exists())
        self.assertTrue(OwnerWarehouseBinding.objects.filter(pk=binding.pk).exists())
        self.assertTrue(BillingServiceContract.objects.filter(pk=contract.pk).exists())
        self.assertTrue(OperatingTarget.objects.filter(pk=target.pk).exists())
        restored_category = ProductCategory.objects.get(pk=category.pk)
        self.assertEqual(restored_category.image.name, "product_categories/example.png")
        self.assertFalse(AuditEvent.objects.filter(pk=historical_audit.pk).exists())
        self.assertTrue(
            AuditEvent.objects.filter(
                action="PRESERVED_DATA_RESTORE",
                succeeded=True,
            ).exists()
        )

    def test_restore_row_count_failure_rolls_back_seed_replacement(self):
        operator = get_user_model().objects.create_superuser(
            username="rollback-operator",
            email="rollback@example.com",
            password="original-password",
        )

        with tempfile.TemporaryDirectory() as raw_temp:
            bundle = Path(raw_temp) / "preserved-backup"
            call_command(
                "backup_preserved_data",
                "--execute",
                "--confirm-target",
                canonical_target(connection),
                "--operator",
                operator.username,
                "--output",
                str(bundle),
                "--maintenance-confirmed",
                stdout=StringIO(),
            )
            self._empty_application_tables()
            sentinel = ContentType.objects.create(
                app_label="restore_rollback",
                model="sentinel",
            )

            manifest_path = bundle / "manifest.json"
            manifest = json.loads(manifest_path.read_text("utf-8"))
            for item in manifest["selected_models"]:
                if item["table"] == "accounts_user":
                    item["row_count"] += 1
                    break
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )

            with self.assertRaisesMessage(CommandError, "事务已回滚"):
                call_command(
                    "restore_preserved_data",
                    "--execute",
                    "--confirm-target",
                    canonical_target(connection),
                    "--operator",
                    operator.username,
                    "--input",
                    str(bundle),
                    "--maintenance-confirmed",
                    "--fresh-database-confirmed",
                    stdout=StringIO(),
                )

        self.assertTrue(ContentType.objects.filter(pk=sentinel.pk).exists())
        self.assertFalse(get_user_model().objects.exists())
