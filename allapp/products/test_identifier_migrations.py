from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.recorder import MigrationRecorder
from django.test import TransactionTestCase
from django.utils import timezone

MIGRATE_FROM = ("products", "0008_product_carton_package")
MIGRATE_TO = ("products", "0011_identifier_normalized_value_indexes")


class ProductIdentifierMigrationTests(TransactionTestCase):
    def setUp(self):
        super().setUp()
        self.executor = MigrationExecutor(connection)
        self.executor.migrate([MIGRATE_FROM])
        self.old_apps = self.executor.loader.project_state([MIGRATE_FROM]).apps

    def tearDown(self):
        MigrationExecutor(connection).migrate([MIGRATE_TO])
        super().tearDown()

    def _owner_and_uoms(self, suffix):
        Owner = self.old_apps.get_model("baseinfo", "Owner")
        ProductUom = self.old_apps.get_model("products", "ProductUom")
        owner = Owner.objects.create(code=f"MIG-{suffix}", name=f"迁移货主-{suffix}")
        base_uom = ProductUom.objects.create(code=f"EA-{suffix}", name="瓶")
        carton_uom = ProductUom.objects.create(code=f"CTN-{suffix}", name="箱")
        return owner, base_uom, carton_uom

    def test_0008_to_0011_backfills_soft_deleted_identifier_history(self):
        Product = self.old_apps.get_model("products", "Product")
        ProductPackage = self.old_apps.get_model("products", "ProductPackage")
        owner, base_uom, carton_uom = self._owner_and_uoms("OK")
        deleted_at = timezone.now()
        product = Product.objects.create(
            owner=owner,
            code="MIG-CODE",
            sku="MIG-SKU",
            name="迁移商品",
            base_uom=base_uom,
            gtin="6901234567892",
            unit_barcode="MIG-UNIT",
            external_code="MIG-ERP",
            is_deleted=True,
            is_active=False,
            deleted_at=deleted_at,
        )
        package = ProductPackage.objects.create(
            product=product,
            uom=carton_uom,
            qty_in_base=24,
            barcode="MIG-PACKAGE",
            is_deleted=True,
            is_active=False,
            deleted_at=deleted_at,
        )
        Product.objects.filter(pk=product.pk).update(
            carton_barcode="MIG-CARTON",
            carton_package_id=package.pk,
        )

        executor = MigrationExecutor(connection)
        executor.migrate([MIGRATE_TO])
        apps = executor.loader.project_state([MIGRATE_TO]).apps
        Barcode = apps.get_model("products", "ProductBarcode")
        External = apps.get_model("products", "ProductExternalIdentifier")
        Registry = apps.get_model("products", "ProductIdentifierRegistry")

        self.assertEqual(
            set(
                Registry.objects.filter(product_id=product.pk).values_list(
                    "normalized_value", flat=True
                )
            ),
            {
                "MIG-CODE",
                "MIG-SKU",
                "6901234567892",
                "MIG-UNIT",
                "MIG-CARTON",
                "MIG-PACKAGE",
                "MIG-ERP",
            },
        )
        carton = Barcode.objects.get(
            product_id=product.pk,
            barcode_type="CARTON",
        )
        package_barcode = Barcode.objects.get(
            product_id=product.pk,
            barcode_type="PACKAGE",
        )
        self.assertEqual(carton.qty_in_base, 24)
        self.assertEqual(carton.primary_scope, f"CARTON:{package.pk}")
        self.assertEqual(package_barcode.qty_in_base, 24)
        self.assertTrue(package_barcode.is_deleted)
        self.assertEqual(
            External.objects.get(product_id=product.pk).primary_scope,
            "LEGACY",
        )

    def test_preflight_conflict_fails_before_schema_changes(self):
        Product = self.old_apps.get_model("products", "Product")
        owner, base_uom, _ = self._owner_and_uoms("CF")
        Product.objects.create(
            owner=owner,
            code="CROSS-VALUE",
            sku="MIG-FIRST-SKU",
            name="迁移冲突商品一",
            base_uom=base_uom,
        )
        second = Product.objects.create(
            owner=owner,
            code="MIG-SECOND-CODE",
            sku="CROSS-VALUE",
            name="迁移冲突商品二",
            base_uom=base_uom,
        )
        tables_before = set(connection.introspection.table_names())
        applied_before = set(
            MigrationRecorder(connection)
            .migration_qs.filter(app="products")
            .values_list("app", "name")
        )

        with self.assertRaisesRegex(RuntimeError, "CROSS-VALUE"):
            MigrationExecutor(connection).migrate([MIGRATE_TO])

        self.assertEqual(set(connection.introspection.table_names()), tables_before)
        self.assertNotIn("products_productbarcode", tables_before)
        self.assertEqual(
            set(
                MigrationRecorder(connection)
                .migration_qs.filter(app="products")
                .values_list("app", "name")
            ),
            applied_before,
        )

        Product.objects.filter(pk=second.pk).delete()
        retry_executor = MigrationExecutor(connection)
        retry_executor.migrate([MIGRATE_TO])
        self.assertIn(
            "products_productbarcode", set(connection.introspection.table_names())
        )
        self.assertTrue(
            MigrationRecorder(connection)
            .migration_qs.filter(app=MIGRATE_TO[0], name=MIGRATE_TO[1])
            .exists()
        )
