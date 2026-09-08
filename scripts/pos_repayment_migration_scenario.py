#!/usr/bin/env python3
"""Verify POS 0009→0010 against nonempty synthetic repayment history."""

import json
import os


def scenario():
    from django.db import IntegrityError, connection, transaction
    from django.db.migrations.executor import MigrationExecutor

    before_target = [("pos", "0009_poscustomer_and_more")]
    after_target = [("pos", "0010_poscustomerrepayment_idempotency_fingerprint_and_more")]
    executor = MigrationExecutor(connection)
    executor.migrate(before_target)
    old = executor.loader.project_state(before_target).apps
    warehouse = old.get_model("locations", "Warehouse").objects.create(
        code="POS-MIG", name="Migration warehouse"
    )
    customer = old.get_model("pos", "PosCustomer").objects.create(
        warehouse_id=warehouse.pk, code="HISTORY", name="Synthetic history"
    )
    repayment = old.get_model("pos", "PosCustomerRepayment")
    for index, (reference, amount, status) in enumerate(
        (
            ("DUPLICATE", "10.00", "COMPLETED"),
            ("DUPLICATE", "7.00", "COMPLETED"),
            ("", "5.00", "COMPLETED"),
            ("", "1.00", "VOIDED"),
        )
    ):
        repayment.objects.create(
            repayment_no=f"HISTORY-{index}",
            warehouse_id=warehouse.pk,
            pos_customer_id=customer.pk,
            method="CASH",
            reference_no=reference,
            amount=amount,
            status=status,
        )
    fields = [field.attname for field in repayment._meta.concrete_fields]
    before = list(repayment.objects.order_by("pk").values(*fields))
    executor = MigrationExecutor(connection)
    executor.migrate(after_target)
    current = executor.loader.project_state(after_target).apps.get_model(
        "pos", "PosCustomerRepayment"
    )
    assert list(current.objects.order_by("pk").values(*fields)) == before
    assert (
        list(current.objects.values_list("idempotency_key", "idempotency_fingerprint"))
        == [(None, "")] * 4
    )
    with connection.cursor() as cursor:
        constraints = connection.introspection.get_constraints(cursor, current._meta.db_table)
    assert constraints["ux_pos_repay_request"]["unique"]
    with transaction.atomic():
        payload = dict(
            warehouse_id=warehouse.pk,
            pos_customer_id=customer.pk,
            method="CASH",
            amount="1.00",
            idempotency_key="probe-key",
        )
        current.objects.create(repayment_no="PROBE-1", **payload)
        try:
            with transaction.atomic():
                current.objects.create(repayment_no="PROBE-2", **payload)
        except IntegrityError:
            pass
        else:
            raise AssertionError("new request identity is not unique")
        transaction.set_rollback(True)
    executor = MigrationExecutor(connection)
    executor.migrate(executor.loader.graph.leaf_nodes())
    from django.contrib.auth import get_user_model
    from django.core.exceptions import ValidationError

    from allapp.pos.services import create_customer_repayment

    user = get_user_model().objects.create_superuser(
        username="migration-reviewer", warehouse_id=warehouse.pk
    )
    try:
        create_customer_repayment(
            user=user,
            customer_id=customer.pk,
            amount="1.00",
            method="CASH",
            reference_no="DUPLICATE",
        )
    except ValidationError as exc:
        assert "历史还款" in str(exc)
    else:
        raise AssertionError("legacy reference-only replay was not blocked")
    assert list(current.objects.order_by("pk").values(*fields)) == before
    return {
        "history_rows": 4,
        "history_unchanged": True,
        "null_defaults_verified": True,
        "unique_constraint_verified": True,
        "legacy_replay_blocked": True,
        "restored_to_head": True,
    }


def main():
    if os.environ.get("APP_ENV") != "test" or os.environ.get("WMS_ISOLATED_MIGRATION_TEST") != "1":
        raise RuntimeError("requires an isolated test instance")
    if any(os.environ.get(key) != "wms_db_test" for key in ("DB_NAME", "DB_TEST_NAME")):
        raise RuntimeError("requires the canonical test database")
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "wmsmaster.settings")
    import django

    django.setup()
    from django.conf import settings
    from django.db import connections

    from scripts.identifier_migration_scenario import _assert_empty_schema
    from wmsmaster.test_coordination import DatabaseLease

    with DatabaseLease(settings.DATABASES["default"]):
        try:
            _assert_empty_schema()
            result = scenario()
            print(json.dumps(result))
        finally:
            connections.close_all()


if __name__ == "__main__":
    main()
