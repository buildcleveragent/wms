import json
from unittest import mock

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import RequestFactory, TestCase
from django.urls import reverse

from allapp.accounts.models import UserRoleScope
from allapp.baseinfo.models import Owner

from .admin import ProductBarcodeAddInline, ProductBarcodeHistoryInline
from .identifier_services import add_product_barcode
from .models import Product, ProductBarcode, ProductPackage, ProductUom


class ProductBarcodeAdminInlineTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = Owner.objects.create(code="ADMIN-BC", name="Admin 条码货主")
        cls.each = ProductUom.objects.create(code="ADMIN-EA", name="件")
        cls.carton = ProductUom.objects.create(code="ADMIN-CTN", name="箱")
        cls.inactive_uom = ProductUom.objects.create(code="ADMIN-BAG", name="袋")
        cls.other_uom = ProductUom.objects.create(code="ADMIN-PAL", name="托")
        cls.product = Product.objects.create(
            owner=cls.owner,
            code="ADMIN-P1",
            name="Admin 条码商品",
            base_uom=cls.each,
            expiry_control=False,
            expiry_basis=None,
        )
        cls.package = ProductPackage.objects.create(
            product=cls.product,
            uom=cls.carton,
            qty_in_base=24,
        )
        cls.inactive_package = ProductPackage.objects.create(
            product=cls.product,
            uom=cls.inactive_uom,
            qty_in_base=6,
            is_active=False,
        )
        cls.other_product = Product.objects.create(
            owner=cls.owner,
            code="ADMIN-P2",
            name="Admin 其他商品",
            base_uom=cls.each,
            expiry_control=False,
            expiry_basis=None,
        )
        cls.other_package = ProductPackage.objects.create(
            product=cls.other_product,
            uom=cls.other_uom,
            qty_in_base=50,
        )
        cls.existing = add_product_barcode(
            product=cls.product,
            barcode="ADMIN-EXISTING",
            barcode_type=ProductBarcode.BarcodeType.OTHER,
        )
        cls.superuser = get_user_model().objects.create_superuser(
            username="admin-barcode-superuser",
            email="admin-barcode@example.com",
            password="x",
        )
        cls.staff_without_permission = get_user_model().objects.create_user(
            username="admin-barcode-viewer",
            password="x",
            is_staff=True,
        )

    def setUp(self):
        self.factory = RequestFactory()
        self.inline = ProductBarcodeAddInline(Product, admin.site)
        self.history_inline = ProductBarcodeHistoryInline(Product, admin.site)
        self.request = self.factory.get("/")
        self.request.user = self.superuser

    def _formset(self, rows):
        formset_class = self.inline.get_formset(self.request, self.product)
        prefix = formset_class.get_default_prefix()
        data = {
            f"{prefix}-TOTAL_FORMS": str(len(rows)),
            f"{prefix}-INITIAL_FORMS": "0",
            f"{prefix}-MIN_NUM_FORMS": "0",
            f"{prefix}-MAX_NUM_FORMS": "1000",
        }
        for index, row in enumerate(rows):
            data[f"{prefix}-{index}-product"] = str(self.product.pk)
            for field, value in row.items():
                data[f"{prefix}-{index}-{field}"] = value
        return formset_class(data=data, instance=self.product, prefix=prefix)

    def test_change_page_renders_editable_add_row_and_readonly_history(self):
        self.client.force_login(self.superuser)

        response = self.client.get(
            reverse("admin:products_product_change", args=[self.product.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "新增商品条码")
        self.assertContains(response, 'name="barcodes-0-barcode"')
        self.assertContains(response, "商品条码历史")
        self.assertContains(response, self.existing.barcode)
        self.assertNotContains(response, 'name="barcodes-2-0-barcode"')

    def test_add_inline_saves_multiple_rows_through_identifier_service(self):
        formset = self._formset(
            [
                {
                    "barcode": " admin-new-other ",
                    "barcode_type": ProductBarcode.BarcodeType.OTHER,
                    "is_active": "on",
                },
                {
                    "barcode": "6901234567890",
                    "barcode_type": ProductBarcode.BarcodeType.CARTON,
                    "package": str(self.package.pk),
                    "is_primary": "on",
                    "is_active": "on",
                },
            ]
        )
        self.assertTrue(formset.is_valid(), formset.errors)

        with mock.patch(
            "allapp.products.admin.add_product_barcode",
            wraps=add_product_barcode,
        ) as service:
            saved = formset.save()

        self.assertEqual(len(saved), 2)
        self.assertEqual(service.call_count, 2)
        other = ProductBarcode.objects.get(normalized_value="ADMIN-NEW-OTHER")
        carton = ProductBarcode.objects.get(normalized_value="6901234567890")
        self.assertFalse(other.is_primary)
        self.assertTrue(other.is_active)
        self.assertEqual(carton.qty_in_base, 24)
        self.assertTrue(carton.is_primary)
        self.product.refresh_from_db()
        self.assertEqual(self.product.carton_barcode, "6901234567890")
        self.assertEqual(self.product.carton_package_id, self.package.pk)

    def test_product_change_post_creates_barcode_from_editable_inline(self):
        self.client.force_login(self.superuser)
        data = {
            "owner": str(self.owner.pk),
            "name": self.product.name,
            "spec": "",
            "code": self.product.code,
            "carton_package": "",
            "base_uom": str(self.each.pk),
            "price": "",
            "purchase_price": "",
            "min_price": "",
            "max_discount": "",
            "pricing_strategy": "WAC",
            "category": "",
            "vender": "",
            "brand": "",
            "min_stock": "",
            "max_stock": "",
            "weight": "",
            "net_content": "",
            "volume": "",
            "batch_control": "on",
            "expiry_basis": "",
            "shelf_life_days": "",
            "pick_policy": Product.PickPolicy.AUTO,
            "material_quality": "",
            "packages-TOTAL_FORMS": "0",
            "packages-INITIAL_FORMS": "0",
            "packages-MIN_NUM_FORMS": "0",
            "packages-MAX_NUM_FORMS": "1000",
            "barcodes-TOTAL_FORMS": "1",
            "barcodes-INITIAL_FORMS": "0",
            "barcodes-MIN_NUM_FORMS": "0",
            "barcodes-MAX_NUM_FORMS": "1000",
            "barcodes-0-product": str(self.product.pk),
            "barcodes-0-barcode": "ADMIN-POSTED-INLINE",
            "barcodes-0-barcode_type": ProductBarcode.BarcodeType.OTHER,
            "barcodes-0-package": "",
            "barcodes-0-is_active": "on",
            "barcodes-2-TOTAL_FORMS": "0",
            "barcodes-2-INITIAL_FORMS": "0",
            "barcodes-2-MIN_NUM_FORMS": "0",
            "barcodes-2-MAX_NUM_FORMS": "0",
            "external_identifiers-TOTAL_FORMS": "0",
            "external_identifiers-INITIAL_FORMS": "0",
            "external_identifiers-MIN_NUM_FORMS": "0",
            "external_identifiers-MAX_NUM_FORMS": "0",
            "_continue": "on",
        }

        response = self.client.post(
            reverse("admin:products_product_change", args=[self.product.pk]),
            data,
        )

        self.assertRedirects(
            response,
            reverse("admin:products_product_change", args=[self.product.pk]),
        )
        self.assertTrue(
            ProductBarcode.objects.filter(
                product=self.product,
                normalized_value="ADMIN-POSTED-INLINE",
            ).exists()
        )

    def test_invalid_row_blocks_all_rows_and_reports_field_errors(self):
        formset = self._formset(
            [
                {
                    "barcode": "ADMIN-SHOULD-NOT-SAVE",
                    "barcode_type": ProductBarcode.BarcodeType.OTHER,
                    "is_active": "on",
                },
                {
                    "barcode": self.existing.barcode,
                    "barcode_type": ProductBarcode.BarcodeType.OTHER,
                    "is_active": "on",
                },
                {
                    "barcode": "12345",
                    "barcode_type": ProductBarcode.BarcodeType.GTIN,
                    "is_active": "on",
                },
            ]
        )

        self.assertFalse(formset.is_valid())
        self.assertIn("barcode", formset.forms[1].errors)
        self.assertIn("barcode", formset.forms[2].errors)
        self.assertFalse(
            ProductBarcode.all_objects.filter(
                normalized_value="ADMIN-SHOULD-NOT-SAVE"
            ).exists()
        )

    def test_package_choices_are_scoped_to_active_current_product_packages(self):
        formset_class = self.inline.get_formset(self.request, self.product)
        formset = formset_class(instance=self.product)

        package_ids = set(
            formset.empty_form.fields["package"].queryset.values_list("pk", flat=True)
        )

        self.assertEqual(package_ids, {self.package.pk})
        invalid = self._formset(
            [
                {
                    "barcode": "ADMIN-WRONG-PACKAGE",
                    "barcode_type": ProductBarcode.BarcodeType.PACKAGE,
                    "package": str(self.other_package.pk),
                    "is_active": "on",
                }
            ]
        )
        self.assertFalse(invalid.is_valid())
        self.assertIn("package", invalid.forms[0].errors)

    def test_add_permissions_require_saved_product_and_barcode_permission(self):
        self.assertTrue(self.inline.has_add_permission(self.request, self.product))
        self.assertFalse(self.inline.has_add_permission(self.request, None))
        self.assertFalse(
            self.history_inline.has_add_permission(self.request, self.product)
        )

        request = self.factory.get("/")
        request.user = self.staff_without_permission
        self.assertFalse(self.inline.has_add_permission(request, self.product))


class ProductBarcodeStandaloneAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = Owner.objects.create(code="ADMIN-SA", name="独立条码货主 A")
        cls.other_owner = Owner.objects.create(code="ADMIN-SB", name="独立条码货主 B")
        cls.each = ProductUom.objects.create(code="ADMIN-S-EA", name="件")
        cls.carton = ProductUom.objects.create(code="ADMIN-S-CTN", name="箱")
        cls.bag = ProductUom.objects.create(code="ADMIN-S-BAG", name="袋")
        cls.product = Product.objects.create(
            owner=cls.owner,
            code="ADMIN-SP1",
            name="独立条码商品 A",
            base_uom=cls.each,
            expiry_control=False,
            expiry_basis=None,
        )
        cls.other_product = Product.objects.create(
            owner=cls.other_owner,
            code="ADMIN-SP2",
            name="独立条码商品 B",
            base_uom=cls.each,
            expiry_control=False,
            expiry_basis=None,
        )
        cls.package = ProductPackage.objects.create(
            product=cls.product,
            uom=cls.carton,
            qty_in_base=24,
        )
        cls.inactive_package = ProductPackage.objects.create(
            product=cls.product,
            uom=cls.bag,
            qty_in_base=6,
            is_active=False,
        )
        cls.other_package = ProductPackage.objects.create(
            product=cls.other_product,
            uom=cls.carton,
            qty_in_base=30,
        )
        cls.superuser = get_user_model().objects.create_superuser(
            username="standalone-barcode-admin",
            email="standalone-barcode@example.com",
            password="x",
        )
        cls.owner_user = get_user_model().objects.create_user(
            username="standalone-owner-user",
            password="x",
            is_staff=True,
        )
        cls.owner_user.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="products",
                codename="add_productbarcode",
            )
        )
        UserRoleScope.objects.create(
            user=cls.owner_user,
            role=UserRoleScope.Role.OWNER_MANAGER,
            owner=cls.owner,
        )

    def setUp(self):
        self.client.force_login(self.superuser)
        self.add_url = reverse("admin:products_productbarcode_add")

    @staticmethod
    def _forward(**values):
        return json.dumps(values)

    def test_add_page_has_business_fields_in_requested_order(self):
        response = self.client.get(self.add_url)

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        expected_names = (
            "owner",
            "product",
            "barcode",
            "barcode_type",
            "package",
            "is_primary",
            "valid_from_0",
            "valid_to_0",
            "remark",
            "is_active",
        )
        positions = [content.index(f'name="{name}"') for name in expected_names]
        self.assertEqual(positions, sorted(positions))
        for hidden_field in (
            "is_deleted",
            "deleted_at_0",
            "deleted_by",
            "created_by",
            "updated_by",
            "normalized_value",
            "qty_in_base",
        ):
            self.assertNotIn(f'name="{hidden_field}"', content)
        self.assertContains(response, "admin/product_barcode_form.js")

    def test_linked_autocomplete_filters_owner_product_and_package(self):
        owner_url = reverse("admin:products_productbarcode_owner_autocomplete")
        product_url = reverse("admin:products_productbarcode_product_autocomplete")
        package_url = reverse("admin:products_productbarcode_package_autocomplete")

        owner_payload = self.client.get(owner_url).json()
        self.assertEqual(
            {int(item["id"]) for item in owner_payload["results"]},
            {self.owner.pk, self.other_owner.pk},
        )
        self.assertEqual(
            self.client.get(product_url).json()["results"],
            [],
        )
        product_payload = self.client.get(
            product_url,
            {"forward": self._forward(owner=self.owner.pk)},
        ).json()
        self.assertEqual(
            {int(item["id"]) for item in product_payload["results"]},
            {self.product.pk},
        )
        package_payload = self.client.get(
            package_url,
            {"forward": self._forward(product=self.product.pk)},
        ).json()
        self.assertEqual(
            {int(item["id"]) for item in package_payload["results"]},
            {self.package.pk},
        )

    def test_autocomplete_respects_owner_access_scope(self):
        self.client.force_login(self.owner_user)
        owner_url = reverse("admin:products_productbarcode_owner_autocomplete")
        product_url = reverse("admin:products_productbarcode_product_autocomplete")

        owner_payload = self.client.get(owner_url).json()
        self.assertEqual(
            {int(item["id"]) for item in owner_payload["results"]},
            {self.owner.pk},
        )
        cross_owner_payload = self.client.get(
            product_url,
            {"forward": self._forward(owner=self.other_owner.pk)},
        ).json()
        self.assertEqual(cross_owner_payload["results"], [])

    def test_add_saves_remark_inactive_state_and_actor_through_service(self):
        response = self.client.post(
            self.add_url,
            {
                "owner": str(self.owner.pk),
                "product": str(self.product.pk),
                "barcode": "ADMIN-STANDALONE-CARTON",
                "barcode_type": ProductBarcode.BarcodeType.CARTON,
                "package": str(self.package.pk),
                "valid_from_0": "",
                "valid_from_1": "",
                "valid_to_0": "",
                "valid_to_1": "",
                "remark": "独立页面新增",
                "_save": "保存",
            },
        )

        self.assertRedirects(
            response,
            reverse("admin:products_productbarcode_changelist"),
        )
        record = ProductBarcode.all_objects.get(
            normalized_value="ADMIN-STANDALONE-CARTON"
        )
        self.assertEqual(record.owner_id, self.owner.pk)
        self.assertEqual(record.package_id, self.package.pk)
        self.assertEqual(record.qty_in_base, 24)
        self.assertEqual(record.remark, "独立页面新增")
        self.assertFalse(record.is_active)
        self.assertEqual(record.created_by_id, self.superuser.pk)
        self.assertEqual(record.updated_by_id, self.superuser.pk)

    def test_tampered_owner_or_package_is_rejected_without_writes(self):
        base_data = {
            "owner": str(self.owner.pk),
            "product": str(self.product.pk),
            "barcode": "ADMIN-TAMPERED",
            "barcode_type": ProductBarcode.BarcodeType.CARTON,
            "package": str(self.other_package.pk),
            "valid_from_0": "",
            "valid_from_1": "",
            "valid_to_0": "",
            "valid_to_1": "",
            "remark": "",
            "is_active": "on",
            "_save": "保存",
        }
        wrong_package = self.client.post(self.add_url, base_data)
        self.assertEqual(wrong_package.status_code, 200)
        self.assertContains(wrong_package, "选择一个有效的选项")

        wrong_owner = self.client.post(
            self.add_url,
            {
                **base_data,
                "owner": str(self.other_owner.pk),
                "package": str(self.package.pk),
            },
        )
        self.assertEqual(wrong_owner.status_code, 200)
        self.assertContains(wrong_owner, "选择一个有效的选项")
        self.assertFalse(
            ProductBarcode.all_objects.filter(
                normalized_value="ADMIN-TAMPERED"
            ).exists()
        )
