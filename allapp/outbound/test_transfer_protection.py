from decimal import Decimal
from importlib import import_module

from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, override_settings

from allapp.outbound.models import OutboundOrderLine
from allapp.outbound.ownership import map_product
from allapp.outbound.print_units import apply_print_units
from allapp.outbound.test_ownership_transfer import TransferFixtures
from allapp.outbound.transfer_models import OwnerProductMapping
from allapp.products.models import Product, ProductPackage, ProductUom, TransferProductBinding
from allapp.products.serializers import ProductSerializer


@override_settings(OUTBOUND_LEGACY_AUTHZ_MODE="enforce", INVENTORY_FIFO_ENABLED=False)
class TransferProtectionTests(TransferFixtures, TestCase):
    def package(self):
        unit = ProductUom.objects.create(code="PROTECT-BAG", name="袋")
        return ProductPackage.objects.create(product=self.product, uom=unit, qty_in_base=50)

    def test_target_protection_and_source_measurement_lock_allow_prices(self):
        package = self.package()
        order, task = self.create_order()
        target = order.lines.get().product
        for product, field, value in [
            (target, "name", "renamed"),
            (target, "base_uom_id", package.uom_id),
            (self.product, "serial_control", True),
        ]:
            old = getattr(product, field)
            setattr(product, field, value)
            with self.assertRaises(ValidationError), transaction.atomic():
                product.save()
            setattr(product, field, old)
        target.price = Decimal("99")
        target.save(update_fields=["price"])
        target.refresh_from_db()
        self.assertEqual(target.price, 99)
        self.assertTrue(ProductSerializer(target).data["transfer_protection"]["managed"])
        self.complete(task)

    def test_queryset_and_package_mutations_cannot_bypass_protection(self):
        package = self.package()
        order, _ = self.create_order()
        target = order.lines.get().product
        for qs, changes in [
            (Product.objects.filter(pk=target.pk), {"batch_control": False}),
            (Product.all_objects.filter(pk=self.product.pk), {"base_uom_id": package.uom_id}),
            (ProductPackage.all_objects.filter(pk=package.pk), {"qty_in_base": 1}),
        ]:
            with self.assertRaises(ValidationError):
                qs.update(**changes)
        package.qty_in_base = 1
        with self.assertRaises(ValidationError):
            ProductPackage.objects.bulk_update([package], ["qty_in_base"])
        for p in (package, target.packages.get()):
            with self.assertRaises(ValidationError), transaction.atomic():
                p.qty_in_base = 1
                p.save()
            with self.assertRaises(ValidationError), transaction.atomic():
                ProductPackage.all_objects.filter(pk=p.pk).delete()
        with self.assertRaises(ValidationError):
            TransferProductBinding.objects.all().delete()

    def test_source_description_sync_preserves_order_name(self):
        order, _ = self.create_order()
        line = order.lines.get()
        old_name = line.frozen_product_name
        self.product.name = "Corrected source name"
        self.product.model = "MODEL-CORRECTED"
        self.product.save(update_fields=["name", "model"])
        line.product.refresh_from_db()
        self.assertEqual(line.product.name, self.product.name)
        self.assertEqual(line.product.model, self.product.model)
        line.refresh_from_db()
        self.assertEqual(line.frozen_product_name, old_name)

    def test_new_package_sync_and_no_independent_target_addition(self):
        order, _ = self.create_order()
        target = order.lines.get().product
        package = self.package()
        self.assertEqual(target.packages.get(uom=package.uom).qty_in_base, 50)
        unit = ProductUom.objects.create(code="OTHER-BAG", name="other")
        with self.assertRaises(ValidationError), transaction.atomic():
            ProductPackage.objects.create(product=target, uom=unit, qty_in_base=10)

    def test_disabled_mapping_and_cancel_do_not_unlock_or_create_duplicate(self):
        order, _ = self.create_order()
        target = order.lines.get().product
        mapping = OwnerProductMapping.objects.get()
        mapping.is_active = False
        mapping.save()
        with self.assertRaises(ValidationError), transaction.atomic():
            map_product(self.product, self.target_owner)
        with self.assertRaises(ValidationError):
            mapping.delete()
        target.name = "cannot rename"
        with self.assertRaises(ValidationError), transaction.atomic():
            target.save()
        mapping.is_active = True
        mapping.save()
        second, _ = self.create_order()
        self.assertEqual(second.lines.get().product_id, target.pk)
        self.assertEqual(TransferProductBinding.objects.count(), 1)

    def test_historical_conflicts_are_protected_and_report_differences(self):
        target = Product.objects.create(
            owner=self.target_owner,
            code="LEGACY",
            name="other",
            base_uom=self.uom,
            serial_control=True,
        )
        OwnerProductMapping.objects.create(
            source_product=self.product, target_owner=self.target_owner, target_product=target
        )
        response = self._create(self.payload())
        self.assertEqual(response.status_code, 400)
        self.assertIn("来源商品", str(response.data))
        self.assertIn("序列号", str(response.data))
        self.assertTrue(TransferProductBinding.objects.filter(target_product=target).exists())
        target.name = "again"
        with self.assertRaises(ValidationError), transaction.atomic():
            target.save()

    def test_migration_covers_deleted_mapping_and_historical_only_target(self):
        order, _ = self.create_order()
        target = order.lines.get().product
        state = MigrationExecutor(connection).loader.project_state()
        Binding = state.apps.get_model("products", "TransferProductBinding")
        Mapping = state.apps.get_model("outbound", "OwnerProductMapping")
        Binding.objects.all().delete()
        Mapping.objects.all().delete()
        other = Product.objects.create(
            owner=self.target_owner, code="INACTIVE", name="old", base_uom=self.uom
        )
        Mapping.objects.create(
            source_product_id=self.product.pk,
            target_owner_id=self.target_owner.pk,
            target_product_id=other.pk,
            is_deleted=True,
            is_active=False,
        )
        migration = import_module("allapp.products.migrations.0016_transferproductbinding")
        with connection.schema_editor() as editor:
            migration.protect_existing(state.apps, editor)
        self.assertEqual(
            set(TransferProductBinding.objects.values_list("target_product_id", flat=True)),
            {target.pk, other.pk},
        )

    def test_print_base_and_bag_keep_money_and_frozen_options(self):
        package = self.package()
        order, task = self.create_order(
            self.payload(items=[{"product_id": self.product.pk, "qty": "75", "price": "3"}])
        )
        line = order.lines.get()
        target_package = line.product.packages.get()
        self.assertEqual(
            apply_print_units([line], {"unit_mode": "package"}),
            [{"unit": "袋", "qty": Decimal("1.5"), "text": "1.5"}],
        )
        self.assertEqual(line.print_price, 150)
        self.assertEqual(line.base_qty, 75)
        self.assertEqual(line.base_price, 3)
        self.assertEqual(line.print_qty * line.print_price, 225)
        line.note = "saved later"
        line.save(update_fields=["note"])
        line.refresh_from_db()
        self.assertEqual(Decimal(line.print_unit_snapshot["packages"][0]["ratio"]), 50)
        with self.assertRaises(ValidationError):
            apply_print_units([line], {f"unit_{line.pk}": str(package.pk)})
        apply_print_units([line], {f"unit_{line.pk}": str(target_package.pk)})
        self.complete(task)
        from allapp.outbound.export_print import pick_task_print
        from allapp.pos.models import PosReceiptWarehouseInfo

        PosReceiptWarehouseInfo.objects.create(
            warehouse=self.warehouse, name="打印仓", address="打印测试地址"
        )

        request = self.factory.get(
            f"/api/outbound/pda/pick-tasks/{task.pk}/print/", {"unit_mode": "package"}
        )
        request.user = self.operator
        response = pick_task_print(request, task.pk)
        self.assertEqual(response.status_code, 200, response.content.decode())
        self.assertContains(response, "150.00")
        self.assertContains(response, "1.5")
        self.assertContains(response, "225.00")

    def test_legacy_line_does_not_guess_current_package_ratio(self):
        self.package()
        order, _ = self.create_order()
        line = order.lines.get()
        OutboundOrderLine.all_objects.filter(pk=line.pk).update(print_unit_snapshot={})
        line.refresh_from_db()
        apply_print_units([line], {"unit_mode": "package"})
        self.assertEqual(len(line.print_options), 1)
        self.assertEqual(line.print_qty, line.base_qty)

    def test_sync_failure_rolls_back_source_change(self):
        order, _ = self.create_order()
        target = order.lines.get().product
        original = self.product.name
        # A legacy document without a name snapshot must prevent a cascading rename.
        OutboundOrderLine.all_objects.filter(order=order).update(product_snapshot={})
        self.product.name = "new name"
        with self.assertRaises(ValidationError), transaction.atomic():
            self.product.save()
        self.product.refresh_from_db()
        target.refresh_from_db()
        self.assertEqual(self.product.name, original)
        self.assertEqual(target.name, original)

    def test_audit_is_readonly_and_repair_rechecks_signature_and_history(self):
        from allapp.accounts.models import AuditEvent
        from allapp.products.transfer_audit import repair_binding, report_binding
        from allapp.products.transfer_protection import _system_write

        package = self.package()
        order, task = self.create_order(
            self.payload(
                items=[
                    {
                        "product_id": self.product.pk,
                        "qty": "75",
                        "price": "3",
                        "package_id": package.pk,
                        "package_qty": "1.5",
                    }
                ]
            )
        )
        self.complete(task)
        line = order.lines.get()
        target = line.product
        binding = TransferProductBinding.objects.get(target_product=target)
        # Simulate a pre-upgrade damaged master without changing its historical documents.
        with _system_write([target.pk]):
            target.base_uom = package.uom
            target.price = Decimal("150")
            target.save()
            target_package = target.packages.get()
            target_package.qty_in_base = 1
            target_package.save()
        binding = TransferProductBinding.objects.select_related(
            "source_product", "target_product"
        ).get(pk=binding.pk)
        before = report_binding(binding, price_basis="current")
        self.assertEqual(before["blockers"], [])
        self.assertEqual(before["proposed_prices"]["price"], 3)
        target.refresh_from_db()
        self.assertEqual(target.base_uom_id, package.uom_id)
        with self.assertRaises(ValidationError), transaction.atomic():
            repair_binding(
                binding.pk, signature="outdated", price_basis="current", actor=self.operator
            )
        result = repair_binding(
            binding.pk, signature=before["signature"], price_basis="current", actor=self.operator
        )
        self.assertEqual(result["differences"], [])
        target.refresh_from_db()
        self.assertEqual(target.base_uom_id, self.product.base_uom_id)
        self.assertEqual(target.price, 3)
        line.refresh_from_db()
        self.assertEqual(line.base_qty, 75)
        self.assertEqual(line.base_price, 3)
        self.assertEqual(line.ratio, 50)
        self.assertTrue(
            AuditEvent.objects.filter(
                action="products.transfer.repair", object_id=str(target.pk)
            ).exists()
        )

    def test_repair_refuses_nonzero_inventory_and_pending_orders(self):
        from allapp.products.transfer_audit import repair_binding, report_binding

        order, _ = self.create_order()
        binding = TransferProductBinding.objects.get(target_product=order.lines.get().product)
        report = report_binding(binding)
        self.assertIn("接收商品仍有未完成销售出库单", report["blockers"])
        with self.assertRaises(ValidationError), transaction.atomic():
            repair_binding(
                binding.pk, signature=report["signature"], price_basis=None, actor=self.operator
            )

    def test_rest_serializer_reports_protected_field_error(self):
        from rest_framework.exceptions import ValidationError as ApiValidationError

        order, _ = self.create_order()
        target = order.lines.get().product
        serializer = ProductSerializer(target, data={"name": "attempted rename"}, partial=True)
        serializer.is_valid(raise_exception=True)
        with self.assertRaises(ApiValidationError), transaction.atomic():
            serializer.save()
        target.refresh_from_db()
        self.assertEqual(target.name, self.product.name)

    def test_excel_update_cannot_rename_managed_target(self):
        import io
        import os
        import tempfile

        from django.core.management import call_command
        from openpyxl import Workbook

        from allapp.products.models import ProductCategory

        category = ProductCategory.objects.create(code="TRANSFER-CATEGORY", name="test")
        self.product.category = category
        self.product.save(update_fields=["category"])
        order, _ = self.create_order()
        target = order.lines.get().product
        workbook = Workbook()
        workbook.active.title = "sheet1"
        workbook.active.append(["owner", "货主商品编码", "name", "base_uom", "category"])
        workbook.active.append(
            [
                self.target_owner.code,
                target.code,
                "attempted import rename",
                self.uom.name,
                category.code,
            ]
        )
        output = io.StringIO()
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as stream:
            path = stream.name
        try:
            workbook.save(path)
            call_command("import_product_master_sheet", "--file", path, "--update", stdout=output)
        finally:
            os.unlink(path)
        target.refresh_from_db()
        self.assertEqual(target.name, self.product.name)
        self.assertIn("受来源保护", output.getvalue())

    def test_multiple_receiving_owners_share_rules_but_not_archives(self):
        from allapp.baseinfo.models import Owner
        from allapp.products.transfer_protection import protected_fields

        other_owner = Owner.objects.create(code="OTHER", name="另一接收货主")
        first = map_product(self.product, self.target_owner)
        second = map_product(self.product, other_owner)
        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(map_product(self.product, self.target_owner).pk, first.pk)
        self.assertEqual(map_product(self.product, other_owner).pk, second.pk)
        self.product.name = "统一更正名称"
        self.product.save(update_fields=["name"])
        for product in (first, second):
            product.refresh_from_db()
            self.assertEqual(product.name, self.product.name)
            self.assertIn("base_uom_id", protected_fields(product.pk))
        package = self.package()
        for product in (first, second):
            self.assertEqual(product.packages.get(uom=package.uom).qty_in_base, 50)

    def test_direct_binding_save_cannot_create_a_cycle(self):
        target = map_product(self.product, self.target_owner)
        with self.assertRaisesMessage(ValidationError, "循环"), transaction.atomic():
            TransferProductBinding.objects.create(
                source_product=target, target_product=self.product
            )
        self.assertEqual(TransferProductBinding.objects.count(), 1)

    def test_same_name_products_are_separate_and_each_is_protected(self):
        other = Product.objects.create(
            owner=self.owner, code="OTHER-SOURCE", name=self.product.name, base_uom=self.uom
        )
        first = map_product(self.product, self.target_owner)
        second = map_product(other, self.target_owner)
        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(TransferProductBinding.objects.count(), 2)
        for target in (first, second):
            target.spec = "cannot rewrite"
            with self.assertRaises(ValidationError), transaction.atomic():
                target.save()

    def test_source_correction_does_not_silently_overwrite_legacy_conflict(self):
        target = Product.objects.create(
            owner=self.target_owner, code="OLD-CONFLICT", name="旧名称冲突", base_uom=self.uom
        )
        OwnerProductMapping.objects.create(
            source_product=self.product, target_owner=self.target_owner, target_product=target
        )
        original = self.product.name
        self.product.name = "更正名称"
        with self.assertRaisesMessage(ValidationError, "历史差异"), transaction.atomic():
            self.product.save()
        self.product.refresh_from_db()
        target.refresh_from_db()
        self.assertEqual(self.product.name, original)
        self.assertEqual(target.name, "旧名称冲突")

    def test_transfer_line_cannot_be_rebound_after_creation(self):
        order, _ = self.create_order()
        line = order.lines.get()
        line.product = self.product
        with self.assertRaisesMessage(ValidationError, "不能改绑"), transaction.atomic():
            line.save()
        line.refresh_from_db()
        self.assertEqual(line.product.owner_id, self.target_owner.pk)

    def test_admin_shows_provenance_and_source_can_add_but_not_rewrite_packages(self):
        from django.urls import reverse

        self.package()
        order, _ = self.create_order()
        target = order.lines.get().product
        self.operator.is_staff = self.operator.is_superuser = True
        self.operator.save(update_fields=["is_staff", "is_superuser"])
        self.client.force_login(self.operator)
        response = self.client.get(reverse("admin:products_product_change", args=[target.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "转货来源管理")
        fields = response.context["adminform"].form.fields
        self.assertNotIn("name", fields)
        self.assertNotIn("base_uom", fields)
        self.assertIn("price", fields)
        source_response = self.client.get(
            reverse("admin:products_product_change", args=[self.product.pk])
        )
        self.assertEqual(source_response.status_code, 200)
        forms = source_response.context["inline_admin_formsets"][0].formset.forms
        self.assertTrue(forms[0].fields["qty_in_base"].disabled)
        self.assertFalse(forms[-1].fields["qty_in_base"].disabled)
