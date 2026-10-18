"""Exercise the deployed schema -> shared customer upgrade with real historical rows."""

from datetime import date

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


@pytest.mark.migrations
class CustomerSharingUpgradeTests(TransactionTestCase):
    def test_real_upgrade_preserves_customer_ids_orders_and_independent_legacy_records(self):
        assert connection.vendor == "mysql"
        with connection.cursor() as cursor:
            cursor.execute("SELECT DATABASE()")
            assert cursor.fetchone()[0] == "wms_db_test"
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        previous = [
            ("baseinfo", "0005_ownerwarehousebinding"),
            ("outbound", "0007_operations_report_indexes"),
            ("inventory", "0010_inventorycostlayer_inventorycostadjustment_and_more"),
            ("reports", "0008_business_review_snapshot_permission"),
        ]
        try:
            executor.migrate(previous)
            historical = executor.loader.project_state(previous).apps
            User = historical.get_model("accounts", "User")
            Owner = historical.get_model("baseinfo", "Owner")
            Customer = historical.get_model("baseinfo", "Customer")
            Warehouse = historical.get_model("locations", "Warehouse")
            Order = historical.get_model("outbound", "OutboundOrder")
            owner = Owner.objects.create(code="UPGRADE", name="历史货主")
            user = User.objects.create(username="upgrade-seller")
            warehouse = Warehouse.objects.create(code="UPGRADE", name="历史仓库")
            customer = Customer.objects.create(
                owner_id=owner.pk,
                salesperson_id=user.pk,
                code="OLD-1",
                name="同名历史客户",
                external_code="EXT-OLD",
                phone="13800000000",
                bank_account="shared-bank",
                area="东区",
                delivery_route="一号线",
                delivery_seq=2,
                level=3,
                delivery_distance_km="12.50",
                promised_days=4,
            )
            duplicate_name = Customer.objects.create(
                owner_id=owner.pk,
                salesperson_id=user.pk,
                code="OLD-2",
                name=customer.name,
                phone=customer.phone,
                is_active=False,
            )
            order = Order.objects.create(
                owner_id=owner.pk,
                warehouse_id=warehouse.pk,
                customer_id=customer.pk,
                order_no="HISTORICAL-ORDER",
                biz_date=date(2025, 1, 1),
            )
            executor = MigrationExecutor(connection)
            executor.migrate(latest)
            from allapp.baseinfo.models import Customer as CurrentCustomer
            from allapp.baseinfo.models import OwnerCustomerBinding
            from allapp.outbound.models import OutboundOrder

            binding = OwnerCustomerBinding.all_objects.get(
                owner_id=owner.pk, customer_id=customer.pk
            )
            self.assertEqual(binding.code, "OLD-1")
            self.assertEqual(binding.external_code, "EXT-OLD")
            self.assertEqual(binding.salesperson_id, user.pk)
            self.assertEqual(
                (
                    binding.area,
                    binding.delivery_route,
                    binding.delivery_seq,
                    binding.level,
                    binding.promised_days,
                ),
                ("东区", "一号线", 2, 3, 4),
            )
            self.assertEqual(str(binding.delivery_distance_km), "12.50")
            self.assertEqual(
                CurrentCustomer.objects.get(pk=customer.pk).bank_account, "shared-bank"
            )
            self.assertEqual(OutboundOrder.objects.get(pk=order.pk).customer_id, customer.pk)
            self.assertEqual(CurrentCustomer.all_objects.filter(name=customer.name).count(), 2)
            self.assertTrue(
                OwnerCustomerBinding.all_objects.filter(customer_id=duplicate_name.pk).exists()
            )
            self.assertEqual(MigrationExecutor(connection).migration_plan(latest), [])
        finally:
            MigrationExecutor(connection).migrate(latest)
