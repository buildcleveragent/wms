#!/usr/bin/env python3
"""Run product identifier migration assertions in one disposable MySQL schema."""

import json
import os
import time

import MySQLdb

MIGRATE_FROM = ("products", "0008_product_carton_package")
MIGRATE_TO = ("products", "0011_identifier_normalized_value_indexes")
MIGRATE_FROM_TARGETS = [
    MIGRATE_FROM,
    ("baseinfo", "0005_ownerwarehousebinding"),
]


def _admin_connection():
    return MySQLdb.connect(
        host=os.environ["DB_HOST"],
        port=int(os.environ["DB_PORT"]),
        user=os.environ["DB_USER"],
        passwd=os.environ["DB_PASSWORD"],
        charset="utf8mb4",
        connect_timeout=5,
    )


def _assert_empty_schema():
    connection = _admin_connection()
    try:
        connection.select_db("wms_db_test")
        with connection.cursor() as cursor:
            cursor.execute("SELECT DATABASE()")
            if cursor.fetchone()[0] != "wms_db_test":
                raise RuntimeError("Unexpected connected migration database.")
            cursor.execute("SHOW TABLES")
            if cursor.fetchall():
                raise RuntimeError("Migration scenario requires a NEW empty test instance.")
    finally:
        connection.close()


def _run_scenario():
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor
    from django.db.migrations.recorder import MigrationRecorder
    from django.utils import timezone

    started = time.monotonic()
    executor = MigrationExecutor(connection)
    executor.migrate(MIGRATE_FROM_TARGETS)
    migrated_from_seconds = time.monotonic() - started
    old_apps = executor.loader.project_state(MIGRATE_FROM_TARGETS).apps

    Owner = old_apps.get_model("baseinfo", "Owner")
    ProductUom = old_apps.get_model("products", "ProductUom")
    Product = old_apps.get_model("products", "Product")
    ProductPackage = old_apps.get_model("products", "ProductPackage")

    conflict_owner = Owner.objects.create(code="MIG-CF", name="迁移货主-CF")
    conflict_uom = ProductUom.objects.create(code="EA-CF", name="件")
    Product.objects.create(
        owner=conflict_owner,
        code="CROSS-VALUE",
        sku="MIG-FIRST-SKU",
        name="迁移冲突商品一",
        base_uom=conflict_uom,
    )
    conflicting_product = Product.objects.create(
        owner=conflict_owner,
        code="MIG-SECOND-CODE",
        sku="CROSS-VALUE",
        name="迁移冲突商品二",
        base_uom=conflict_uom,
    )
    tables_before = set(connection.introspection.table_names())
    applied_before = set(
        MigrationRecorder(connection).migration_qs.filter(app="products").values_list("app", "name")
    )
    try:
        MigrationExecutor(connection).migrate([MIGRATE_TO])
    except RuntimeError as exc:
        if "CROSS-VALUE" not in str(exc):
            raise
    else:
        raise AssertionError("Identifier conflict preflight unexpectedly succeeded.")

    assert set(connection.introspection.table_names()) == tables_before
    assert "products_productbarcode" not in tables_before
    assert (
        set(
            MigrationRecorder(connection)
            .migration_qs.filter(app="products")
            .values_list("app", "name")
        )
        == applied_before
    )
    conflicting_product.delete()

    owner = Owner.objects.create(code="MIG-OK", name="迁移货主-OK")
    base_uom = ProductUom.objects.create(code="EA-OK", name="瓶")
    carton_uom = ProductUom.objects.create(code="CTN-OK", name="箱")
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

    migrate_to_started = time.monotonic()
    migrated = MigrationExecutor(connection)
    migrated.migrate([MIGRATE_TO])
    migrated_to_seconds = time.monotonic() - migrate_to_started
    apps = migrated.loader.project_state([MIGRATE_TO]).apps
    Barcode = apps.get_model("products", "ProductBarcode")
    External = apps.get_model("products", "ProductExternalIdentifier")
    Registry = apps.get_model("products", "ProductIdentifierRegistry")

    values = set(
        Registry.objects.filter(product_id=product.pk).values_list("normalized_value", flat=True)
    )
    assert values == {
        "MIG-CODE",
        "MIG-SKU",
        "6901234567892",
        "MIG-UNIT",
        "MIG-CARTON",
        "MIG-PACKAGE",
        "MIG-ERP",
    }
    carton = Barcode.objects.get(product_id=product.pk, barcode_type="CARTON")
    package_barcode = Barcode.objects.get(
        product_id=product.pk,
        barcode_type="PACKAGE",
    )
    assert carton.qty_in_base == 24
    assert carton.primary_scope == f"CARTON:{package.pk}"
    assert package_barcode.qty_in_base == 24
    assert package_barcode.is_deleted
    assert External.objects.get(product_id=product.pk).primary_scope == "LEGACY"
    assert "products_productbarcode" in set(connection.introspection.table_names())
    assert (
        MigrationRecorder(connection)
        .migration_qs.filter(app=MIGRATE_TO[0], name=MIGRATE_TO[1])
        .exists()
    )
    return {
        "backfill_verified": True,
        "preflight_verified": True,
        "retry_verified": True,
        "registry_count": len(values),
        "migrate_to_0008_seconds": round(migrated_from_seconds, 3),
        "migrate_to_0011_seconds": round(migrated_to_seconds, 3),
    }


def main():
    if os.environ.get("APP_ENV") != "test":
        raise SystemExit("APP_ENV must be test.")
    if any(os.environ.get(key) != "wms_db_test" for key in ("DB_NAME", "DB_TEST_NAME")):
        raise SystemExit("Both database names must be wms_db_test.")
    if os.environ.get("WMS_ISOLATED_MIGRATION_TEST") != "1":
        raise SystemExit("Run through the isolated migration test container.")
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "wmsmaster.settings")
    import django

    django.setup()
    from django.conf import settings
    from django.db import connection, connections
    from django.db.migrations.executor import MigrationExecutor

    from wmsmaster.test_coordination import DatabaseLease

    with DatabaseLease(settings.DATABASES["default"]):
        try:
            _assert_empty_schema()
            result = _run_scenario()
            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())
            result["restored_to_head"] = True
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        finally:
            connections.close_all()


if __name__ == "__main__":
    main()
