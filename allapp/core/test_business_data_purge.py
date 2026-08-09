import datetime
from io import StringIO
from types import SimpleNamespace
from unittest import mock, skipUnless

from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import SimpleTestCase, TransactionTestCase
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework_simplejwt.token_blacklist.models import (
    BlacklistedToken,
    OutstandingToken,
)

from allapp.accounts.audit import record_audit_event
from allapp.accounts.models import AuditEvent, LoginThrottleCacheEntry
from allapp.baseinfo.models import Customer, Owner, OwnerWarehouseBinding
from allapp.billing.enums import CalcMethod, ChargeType
from allapp.billing.models import BillingPeriod, BillingRule, BillingServiceContract
from allapp.core.business_data_purge import (
    PRESERVED_MODEL_LABELS,
    PURGED_MODEL_LABELS,
    PurgeConfigurationError,
    _lock_name,
    canonical_target,
    prepare_purge,
    resolve_manifest,
)
from allapp.core.models import DocSequence
from allapp.inventory.models import InventoryCostLayer, InventoryDetail
from allapp.locations.models import Location, Subwarehouse, Warehouse
from allapp.pos.models import PosCustomer, PosShift
from allapp.products.models import Product, ProductCategory, ProductUom
from allapp.reports.models import OperatingTarget
from allapp.salesapp.models import PriceGroup, SaleProductConfig
from allapp.strategies.models import (
    Strategy,
    StrategyAssignment,
    StrategyCategory,
    StrategyTemplate,
)


class BusinessDataPurgeManifestTests(SimpleTestCase):
    newly_preserved = {
        "baseinfo.ownerwarehousebinding",
        "billing.billingservicecontract",
        "reports.operatingtarget",
    }
    newly_purged = {
        "accounts.loginthrottlecacheentry",
        "billing.collectionactivity",
        "billing.paymentallocation",
        "billing.paymentreceipt",
        "billing.receivablecollectioncase",
        "inventory.inventorycostadjustment",
        "inventory.inventorycostlayer",
        "inventory.inventorylayermovement",
        "inventory.inventorylayerposition",
        "products.productbarcode",
        "products.productexternalidentifier",
        "products.productidentifierregistry",
        "reports.alertcase",
        "reports.alertcasehistory",
        "reports.businessreviewsnapshot",
        "reports.factoutboundordersla",
        "reports.taskstatesnapshotdaily",
        "salesapp.saleminiproductreview",
        "salesapp.saleminiproductreviewimage",
        "tasking.countscopelock",
        "tasking.relocationrequest",
        "tasking.relocationrequestline",
        "tasking.relocationreservation",
        "tasking.replenishmentpolicy",
        "tasking.replenishmentrequest",
        "token_blacklist.blacklistedtoken",
        "token_blacklist.outstandingtoken",
    }

    def test_every_managed_model_is_explicitly_classified(self):
        manifest = resolve_manifest()

        self.assertFalse(PRESERVED_MODEL_LABELS & PURGED_MODEL_LABELS)
        self.assertTrue(self.newly_preserved <= PRESERVED_MODEL_LABELS)
        self.assertTrue(self.newly_purged <= PURGED_MODEL_LABELS)
        self.assertIn("accounts_user", manifest.preserved_tables)
        self.assertIn("products_product", manifest.purged_tables)
        self.assertIn("django_migrations", manifest.preserved_tables)

    def test_new_unclassified_model_fails_closed(self):
        class RegistryWithUnknownModel:
            def get_models(self, include_auto_created=False):
                from django.apps import apps

                models = list(
                    apps.get_models(include_auto_created=include_auto_created)
                )
                models.append(
                    SimpleNamespace(
                        _meta=SimpleNamespace(
                            label_lower="unclassified.newmodel",
                            managed=True,
                            proxy=False,
                            db_table="unclassified_newmodel",
                        )
                    )
                )
                return models

        with self.assertRaisesMessage(PurgeConfigurationError, "未分类模型"):
            resolve_manifest(RegistryWithUnknownModel())


@skipUnless(connection.vendor == "mysql", "purge command is intentionally MySQL-only")
class BusinessDataPurgeCommandTests(TransactionTestCase):
    reset_sequences = False

    def setUp(self):
        self.operator = get_user_model().objects.create_superuser(
            username="purge-operator",
            email="purge@example.com",
            password="test-pass",
        )
        self.owner = Owner.objects.create(
            name="Purge Owner",
            code="PURGEOWN",
            next_sku_sequence=50,
        )
        self.customer = Customer.objects.create(
            owner=self.owner,
            salesperson=self.operator,
            code="PURGE-CUSTOMER",
            name="Purge Customer",
        )
        self.warehouse = Warehouse.objects.create(code="PWH", name="Purge Warehouse")
        self.owner_warehouse_binding = OwnerWarehouseBinding.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
        )
        self.subwarehouse = Subwarehouse.objects.create(
            warehouse=self.warehouse,
            code="PSW1",
            name="Purge Subwarehouse",
        )
        self.location = Location.objects.create(
            warehouse=self.warehouse,
            code="PSW1-01-01-01",
            name="Purge Location",
        )
        self.category = ProductCategory.objects.create(
            code="PURGE-CAT", name="Category"
        )
        self.location.product_categories.add(self.category)
        self.uom = ProductUom.objects.create(code="PURGE-EA", name="Each")
        self.product = Product.objects.create(
            owner=self.owner,
            category=self.category,
            base_uom=self.uom,
            code="PURGE-PRODUCT",
            name="Purge Product",
            batch_control=False,
            expiry_control=False,
        )
        InventoryDetail.objects.create(
            owner=self.owner,
            product=self.product,
            warehouse=self.warehouse,
            subwarehouse=self.subwarehouse,
            location=self.location,
            base_unit=self.uom.code,
            onhand_qty=3,
        )
        InventoryCostLayer.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            product=self.product,
            base_uom=self.uom,
            original_qty=3,
            source_type="PURGE_TEST",
            source_id="PURGE-LAYER",
        )
        self.rule = BillingRule.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            charge_type=ChargeType.STORAGE,
            calc_method=CalcMethod.PER_CBM_DAY,
            unit_price="1.0000",
        )
        self.service_contract = BillingServiceContract.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            charge_type=ChargeType.STORAGE,
            calc_method=CalcMethod.PER_CBM_DAY,
            effective_from=datetime.date(2026, 7, 1),
        )
        BillingPeriod.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            label="2026-07",
            start_date=datetime.date(2026, 7, 1),
            end_date=datetime.date(2026, 7, 31),
        )
        self.pos_customer = PosCustomer.objects.create(
            warehouse=self.warehouse,
            code="POS-CUSTOMER",
            name="POS Customer",
        )
        PosShift.objects.create(
            shift_no="PURGE-SHIFT",
            warehouse=self.warehouse,
            cashier=self.operator,
            opened_at=timezone.now(),
        )
        self.price_group = PriceGroup.objects.create(
            owner=self.owner,
            code="PURGE-PRICE-GROUP",
            name="Price Group",
        )
        SaleProductConfig.objects.create(owner=self.owner, product=self.product)
        strategy_category = StrategyCategory.objects.create(name="Purge strategy")
        strategy_template = StrategyTemplate.objects.create(
            name="Purge template",
            category=strategy_category,
        )
        self.strategy = Strategy.objects.create(
            name="Preserved strategy",
            template=strategy_template,
            category=strategy_category,
        )
        StrategyAssignment.objects.create(
            strategy=self.strategy,
            target="product",
            target_id=self.product.pk,
            start_date=timezone.now(),
        )
        self.sequence = DocSequence.objects.create(
            doc_type="PURGE",
            biz_date=datetime.date(2026, 7, 22),
            warehouse=self.warehouse,
            owner=self.owner,
            next_no=88,
        )
        self.operating_target = OperatingTarget.objects.create(
            month=datetime.date(2026, 7, 1),
            warehouse=self.warehouse,
            owner=self.owner,
            metric=OperatingTarget.Metric.OTIF,
            target_value="95.0000",
            created_by=self.operator,
        )
        LoginThrottleCacheEntry.objects.create(
            cache_key="purge-login-throttle",
            value="cached",
            expires=timezone.now() + datetime.timedelta(hours=1),
        )
        Session.objects.create(
            session_key="purge-session",
            session_data="e30:1:invalid",
            expire_date=timezone.now() + datetime.timedelta(days=1),
        )
        self.token = Token.objects.create(user=self.operator)
        self.outstanding_token = OutstandingToken.objects.create(
            user=self.operator,
            jti="purge-outstanding-token",
            token="purge-token",
            created_at=timezone.now(),
            expires_at=timezone.now() + datetime.timedelta(days=1),
        )
        BlacklistedToken.objects.create(token=self.outstanding_token)
        self.previous_audit = record_audit_event(
            action="PURGE_TEST_BEFORE",
            module="core.tests",
            user=self.operator,
        )

    def execute_command(self, *, stdout=None):
        call_command(
            "purge_business_data",
            "--execute",
            "--confirm-target",
            canonical_target(connection),
            "--operator",
            self.operator.username,
            "--backup-reference",
            "backup-test-20260722",
            "--maintenance-confirmed",
            stdout=stdout or StringIO(),
        )

    def test_dry_run_is_read_only_and_reports_target(self):
        output = StringIO()

        call_command("purge_business_data", stdout=output)

        self.assertTrue(Product.objects.filter(pk=self.product.pk).exists())
        self.assertTrue(Token.objects.filter(pk=self.token.pk).exists())
        self.assertIn("DRY-RUN（只读）", output.getvalue())
        self.assertIn(canonical_target(connection), output.getvalue())
        self.assertIn("products_product", output.getvalue())
        self.assertIn("accounts_user", output.getvalue())

    def test_execute_purges_business_rows_and_preserves_configuration(self):
        owner_sequence_before = Owner.all_objects.get(
            pk=self.owner.pk
        ).next_sku_sequence

        self.execute_command()

        self.assertFalse(Product.all_objects.filter(pk=self.product.pk).exists())
        self.assertFalse(InventoryDetail.all_objects.exists())
        self.assertFalse(InventoryCostLayer.objects.exists())
        self.assertFalse(BillingPeriod.objects.exists())
        self.assertFalse(PosShift.objects.exists())
        self.assertFalse(SaleProductConfig.all_objects.exists())
        self.assertFalse(StrategyAssignment.objects.exists())
        self.assertFalse(Session.objects.exists())
        self.assertFalse(Token.objects.exists())
        self.assertFalse(LoginThrottleCacheEntry.objects.exists())
        self.assertFalse(OutstandingToken.objects.exists())
        self.assertFalse(BlacklistedToken.objects.exists())

        self.assertTrue(get_user_model().objects.filter(pk=self.operator.pk).exists())
        self.assertTrue(Owner.all_objects.filter(pk=self.owner.pk).exists())
        self.assertTrue(
            OwnerWarehouseBinding.all_objects.filter(
                pk=self.owner_warehouse_binding.pk
            ).exists()
        )
        self.assertTrue(Customer.all_objects.filter(pk=self.customer.pk).exists())
        self.assertTrue(
            ProductCategory.all_objects.filter(pk=self.category.pk).exists()
        )
        self.assertTrue(ProductUom.all_objects.filter(pk=self.uom.pk).exists())
        self.assertTrue(BillingRule.objects.filter(pk=self.rule.pk).exists())
        self.assertTrue(
            BillingServiceContract.objects.filter(pk=self.service_contract.pk).exists()
        )
        self.assertTrue(PosCustomer.objects.filter(pk=self.pos_customer.pk).exists())
        self.assertTrue(PriceGroup.all_objects.filter(pk=self.price_group.pk).exists())
        self.assertTrue(Strategy.objects.filter(pk=self.strategy.pk).exists())
        self.assertEqual(
            DocSequence.objects.get(pk=self.sequence.pk).next_no,
            88,
        )
        self.assertEqual(
            Owner.all_objects.get(pk=self.owner.pk).next_sku_sequence,
            owner_sequence_before,
        )
        self.assertTrue(AuditEvent.objects.filter(pk=self.previous_audit.pk).exists())
        self.assertTrue(
            OperatingTarget.objects.filter(pk=self.operating_target.pk).exists()
        )
        success = AuditEvent.objects.get(
            action="BUSINESS_DATA_PURGE",
            succeeded=True,
        )
        self.assertEqual(
            success.metadata["backup_reference"],
            "backup-test-20260722",
        )
        self.assertGreater(success.after["deleted_rows"]["products_product"], 0)

    def test_missing_confirmation_arguments_are_rejected_before_delete(self):
        with self.assertRaisesMessage(CommandError, "正式执行缺少参数"):
            call_command("purge_business_data", "--execute", stdout=StringIO())

        self.assertTrue(Product.objects.filter(pk=self.product.pk).exists())

    def test_unknown_database_table_blocks_execution(self):
        table = "core_purge_unknown_test"
        with connection.cursor() as cursor:
            cursor.execute(
                f"CREATE TABLE {connection.ops.quote_name(table)} "
                "(id BIGINT PRIMARY KEY) ENGINE=InnoDB"
            )
        try:
            report = prepare_purge()
            self.assertIn(table, report.unknown_tables)
            with self.assertRaisesMessage(CommandError, "未分类数据库表"):
                self.execute_command()
            self.assertTrue(Product.objects.filter(pk=self.product.pk).exists())
        finally:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"DROP TABLE IF EXISTS {connection.ops.quote_name(table)}"
                )

    def test_sql_failure_rolls_back_and_restores_foreign_key_setting(self):
        from allapp.core import business_data_purge

        with connection.cursor() as cursor:
            cursor.execute("SELECT @@SESSION.FOREIGN_KEY_CHECKS")
            before_fk_checks = int(cursor.fetchone()[0])

        real_delete = business_data_purge._delete_table
        calls = 0

        def fail_after_first_delete(cursor, quoted_table):
            nonlocal calls
            calls += 1
            deleted = real_delete(cursor, quoted_table)
            if calls == 1:
                raise RuntimeError("injected purge failure")
            return deleted

        with mock.patch.object(
            business_data_purge,
            "_delete_table",
            side_effect=fail_after_first_delete,
        ):
            with self.assertRaisesMessage(CommandError, "事务已回滚"):
                self.execute_command()

        self.assertTrue(Product.objects.filter(pk=self.product.pk).exists())
        self.assertTrue(Token.objects.filter(pk=self.token.pk).exists())
        with connection.cursor() as cursor:
            cursor.execute("SELECT @@SESSION.FOREIGN_KEY_CHECKS")
            after_fk_checks = int(cursor.fetchone()[0])
        self.assertEqual(after_fk_checks, before_fk_checks)
        self.assertTrue(
            AuditEvent.objects.filter(
                action="BUSINESS_DATA_PURGE",
                succeeded=False,
            ).exists()
        )

    def test_second_connection_cannot_run_while_named_lock_is_held(self):
        target = canonical_target(connection)
        lock_name = _lock_name(target)
        lock_holder = connection.copy(alias="purge_lock_holder")
        try:
            with lock_holder.cursor() as cursor:
                cursor.execute("SELECT GET_LOCK(%s, 0)", [lock_name])
                self.assertEqual(cursor.fetchone()[0], 1)

            with self.assertRaisesMessage(CommandError, "已有清理任务正在运行"):
                self.execute_command()

            self.assertTrue(Product.objects.filter(pk=self.product.pk).exists())
        finally:
            if lock_holder.connection is not None:
                with lock_holder.cursor() as cursor:
                    cursor.execute("SELECT RELEASE_LOCK(%s)", [lock_name])
            lock_holder.close()
