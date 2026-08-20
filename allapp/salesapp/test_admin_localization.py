import re
from types import SimpleNamespace

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.urls import reverse

from allapp.core.admin_mixins import AUDIT_FIELDS
from allapp.salesapp.admin import (
    SaleMiniCartItemInline,
    SaleMiniProductReviewImageInline,
)
from allapp.salesapp.models import SaleMiniCart, SaleMiniProductReview


class SaleMiniAdminLocalizationTests(SimpleTestCase):
    expected_registered_models = {
        "MiniProgramUser",
        "MiniCustomerAddress",
        "SaleMiniBanner",
        "SaleProductConfig",
        "SaleMiniCart",
        "SaleMiniCartItem",
        "SaleMiniOrderMapping",
        "SaleMiniPayment",
        "SaleMiniRefund",
        "SaleMiniAfterSaleRequest",
        "SaleMiniProductReview",
        "SaleMiniPaymentEvent",
        "SaleMiniCouponTemplate",
        "SaleMiniCoupon",
        "SaleMiniOrderAdjustment",
        "SaleMiniPointLedger",
        "SaleMiniDistributionRecord",
    }

    def setUp(self):
        self.request = RequestFactory().get("/admin/")
        self.request.user = SimpleNamespace(
            has_perm=lambda *_args, **_kwargs: True,
            is_active=True,
            is_staff=True,
            is_superuser=True,
        )

    def _registered_sales_admins(self):
        return {
            model.__name__: (model, model_admin)
            for model, model_admin in admin.site._registry.items()
            if model._meta.app_label == "salesapp"
        }

    def test_all_registered_mall_models_are_covered(self):
        self.assertEqual(
            set(self._registered_sales_admins()),
            self.expected_registered_models,
        )

    def test_all_business_fields_have_chinese_names(self):
        audit_fields = set(AUDIT_FIELDS)
        for model_name, (
            model,
            _model_admin,
        ) in self._registered_sales_admins().items():
            with self.subTest(model=model_name, field="verbose_name"):
                self.assertRegex(str(model._meta.verbose_name), re.compile(r"[\u4e00-\u9fff]"))
            for field in model._meta.fields:
                if field.name == "id" or field.name in audit_fields:
                    continue
                with self.subTest(model=model_name, field=field.name):
                    self.assertRegex(str(field.verbose_name), re.compile(r"[\u4e00-\u9fff]"))

    def test_all_admin_forms_hide_base_audit_fields(self):
        audit_fields = set(AUDIT_FIELDS)
        for model_name, (
            _model,
            model_admin,
        ) in self._registered_sales_admins().items():
            form = model_admin.get_form(self.request)
            with self.subTest(model=model_name):
                self.assertTrue(audit_fields.isdisjoint(form.base_fields))

    def test_all_admin_forms_put_status_and_remark_after_business_fields(self):
        trailing_fields = ("is_active", "remark")
        for model_name, (
            _model,
            model_admin,
        ) in self._registered_sales_admins().items():
            fields = list(model_admin.get_fields(self.request))
            expected_suffix = [field for field in trailing_fields if field in fields]
            with self.subTest(model=model_name):
                if expected_suffix:
                    self.assertEqual(fields[-len(expected_suffix) :], expected_suffix)

    def test_mall_inline_forms_hide_base_audit_fields(self):
        audit_fields = set(AUDIT_FIELDS)
        inline_cases = (
            SaleMiniCartItemInline(SaleMiniCart, admin.site),
            SaleMiniProductReviewImageInline(SaleMiniProductReview, admin.site),
        )
        for inline in inline_cases:
            formset = inline.get_formset(self.request)
            with self.subTest(inline=inline.__class__.__name__):
                self.assertTrue(audit_fields.isdisjoint(formset.form.base_fields))

    def test_mall_inline_forms_put_status_and_remark_last(self):
        trailing_fields = ("is_active", "remark")
        inline_cases = (
            SaleMiniCartItemInline(SaleMiniCart, admin.site),
            SaleMiniProductReviewImageInline(SaleMiniProductReview, admin.site),
        )
        for inline in inline_cases:
            fields = list(inline.get_fields(self.request))
            expected_suffix = [field for field in trailing_fields if field in fields]
            with self.subTest(inline=inline.__class__.__name__):
                if expected_suffix:
                    self.assertEqual(fields[-len(expected_suffix) :], expected_suffix)

    def test_custom_address_column_has_chinese_heading(self):
        _model, model_admin = self._registered_sales_admins()["MiniCustomerAddress"]
        self.assertEqual(model_admin.full_address.short_description, "完整地址")


class SaleMiniAdminPageSmokeTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="sale-mini-localization-admin",
            email="sale-mini-localization@example.com",
            password="pw",
        )
        self.client.force_login(self.user)

    def test_all_mall_admin_add_pages_render_without_audit_fields(self):
        audit_field_ids = {f"id_{name}" for name in AUDIT_FIELDS}
        registered_admins = sorted(
            (
                (model, model_admin)
                for model, model_admin in admin.site._registry.items()
                if model._meta.app_label == "salesapp"
            ),
            key=lambda pair: pair[0]._meta.model_name,
        )
        permission_request = RequestFactory().get("/admin/")
        permission_request.user = self.user

        for model, model_admin in registered_admins:
            action = "add" if model_admin.has_add_permission(permission_request) else "changelist"
            url = reverse(f"admin:{model._meta.app_label}_{model._meta.model_name}_{action}")
            response = self.client.get(url)
            with self.subTest(model=model.__name__):
                self.assertEqual(response.status_code, 200)
                body = response.content.decode(response.charset or "utf-8")
                for field_id in audit_field_ids:
                    self.assertNotIn(f'id="{field_id}"', body)

                if action == "add":
                    fields = list(model_admin.get_fields(permission_request))
                    trailing_positions = [
                        body.find(f'id="id_{field}"')
                        for field in ("is_active", "remark")
                        if field in fields
                    ]
                    business_positions = [
                        body.find(f'id="id_{field}"')
                        for field in fields
                        if field not in {"is_active", "remark"}
                    ]
                    trailing_positions = [
                        position for position in trailing_positions if position >= 0
                    ]
                    business_positions = [
                        position for position in business_positions if position >= 0
                    ]
                    self.assertEqual(trailing_positions, sorted(trailing_positions))
                    if trailing_positions and business_positions:
                        self.assertLess(max(business_positions), min(trailing_positions))
