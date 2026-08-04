import importlib
from types import SimpleNamespace

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection
from django.test import TransactionTestCase

from allapp.baseinfo.models import Customer, Owner
from allapp.locations.models import Warehouse
from allapp.outbound.models import OutboundOrder


migration = importlib.import_module(
    "allapp.outbound.migrations.0005_outbound_order_idempotency"
)


class OutboundOrderIdempotencyMigrationTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.owner = Owner.objects.create(code="MIGOWN01", name="Migration Owner")
        self.warehouse = Warehouse.objects.create(code="MIGWH01", name="Migration WH")
        self.salesperson = get_user_model().objects.create_user(
            username="migration-salesperson",
            password="x",
            owner=self.owner,
        )
        self.customer = Customer.objects.create(
            owner=self.owner,
            salesperson=self.salesperson,
            code="MIGCUST01",
            name="Migration Customer",
        )

    def create_order(self, *, owner=None, customer=None, src_bill_no):
        return OutboundOrder.objects.create(
            owner=owner or self.owner,
            warehouse=self.warehouse,
            customer=customer or self.customer,
            created_by=(customer or self.customer).salesperson,
            src_bill_no=src_bill_no,
        )

    def run_normalization(self):
        migration.normalize_source_numbers(
            apps,
            SimpleNamespace(connection=connection),
        )

    def test_blank_and_whitespace_source_numbers_become_null(self):
        blank = self.create_order(src_bill_no="TEMP-BLANK")
        OutboundOrder.objects.filter(pk=blank.pk).update(src_bill_no="")

        self.run_normalization()
        blank.refresh_from_db()
        self.assertIsNone(blank.src_bill_no)

        whitespace = self.create_order(src_bill_no="TEMP-WHITESPACE")
        OutboundOrder.objects.filter(pk=whitespace.pk).update(src_bill_no="   ")
        self.run_normalization()
        whitespace.refresh_from_db()

        self.assertIsNone(whitespace.src_bill_no)

    def test_duplicate_preflight_fails_without_rewriting_orders(self):
        first = self.create_order(src_bill_no="SOURCE-A")
        second = self.create_order(src_bill_no="SOURCE-B")
        constraint = next(
            item
            for item in OutboundOrder._meta.constraints
            if item.name == "ux_out_owner_src_bill"
        )

        with connection.schema_editor() as editor:
            editor.remove_constraint(OutboundOrder, constraint)
        try:
            OutboundOrder.objects.filter(pk=second.pk).update(src_bill_no=" source-a ")

            with self.assertRaisesRegex(RuntimeError, "人工处理重复订单") as caught:
                self.run_normalization()

            message = str(caught.exception)
            self.assertIn(str(self.owner.pk), message)
            self.assertIn(str(first.pk), message)
            self.assertIn(str(second.pk), message)
            self.assertEqual(
                list(
                    OutboundOrder.objects.filter(pk__in=[first.pk, second.pk])
                    .order_by("pk")
                    .values_list("src_bill_no", flat=True)
                ),
                ["SOURCE-A", " source-a "],
            )
        finally:
            OutboundOrder.objects.filter(pk=second.pk).update(src_bill_no="SOURCE-B")
            with connection.schema_editor() as editor:
                editor.add_constraint(OutboundOrder, constraint)

    def test_same_source_number_is_allowed_for_different_owners(self):
        other_owner = Owner.objects.create(code="MIGOWN02", name="Other Owner")
        other_salesperson = get_user_model().objects.create_user(
            username="migration-other-salesperson",
            password="x",
            owner=other_owner,
        )
        other_customer = Customer.objects.create(
            owner=other_owner,
            salesperson=other_salesperson,
            code="MIGCUST02",
            name="Other Customer",
        )

        self.create_order(src_bill_no="SHARED-SOURCE")
        self.create_order(
            owner=other_owner,
            customer=other_customer,
            src_bill_no="SHARED-SOURCE",
        )

        self.run_normalization()
        self.assertEqual(OutboundOrder.objects.count(), 2)
        call_command("check_outbound_source_duplicates", verbosity=0)
