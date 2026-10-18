from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import RequestFactory, TestCase
from django.urls import reverse

from allapp.accounts.models import UserRoleScope
from allapp.baseinfo.admin import CustomerAdmin
from allapp.baseinfo.models import Customer, Owner, OwnerCustomerBinding


class CustomerAdminSalespersonTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = Owner.objects.create(name="货主甲", code="CADMIN-A")
        cls.other_owner = Owner.objects.create(name="货主乙", code="CADMIN-B")
        cls.empty_owner = Owner.objects.create(name="无业务员货主", code="CADMIN-C")
        cls.admin_user = get_user_model().objects.create_superuser(
            username="customer-admin", email="admin@example.test"
        )
        cls.salesperson = cls.make_salesperson("sales-a", cls.owner)
        cls.other_salesperson = cls.make_salesperson("sales-b", cls.other_owner)
        cls.scope_only_salesperson = cls.make_salesperson("sales-scope-only", cls.owner)
        cls.scope_only_salesperson.owner = None
        cls.scope_only_salesperson.save(update_fields=["owner"])
        cls.stale_legacy_salesperson = cls.make_salesperson("sales-stale-owner", cls.other_owner)
        cls.stale_legacy_salesperson.owner = cls.owner
        cls.stale_legacy_salesperson.save(update_fields=["owner"])
        inactive = cls.make_salesperson("sales-inactive", cls.owner)
        inactive.is_active = False
        inactive.save(update_fields=["is_active"])
        inactive_scope = cls.make_salesperson("sales-inactive-scope", cls.owner)
        inactive_scope.role_scopes.update(is_active=False)
        get_user_model().objects.create_user(username="unscoped-user", owner=cls.owner)
        cls.manager = get_user_model().objects.create_user(
            username="customer-manager", owner=cls.owner, is_staff=True
        )
        UserRoleScope.objects.create(
            user=cls.manager, owner=cls.owner, role=UserRoleScope.Role.OWNER_MANAGER
        )
        cls.manager.user_permissions.add(
            *Permission.objects.filter(
                content_type__app_label="baseinfo",
                codename__in=["add_customer", "change_customer", "view_customer"],
            )
        )

    @classmethod
    def make_salesperson(cls, username, owner):
        user = get_user_model().objects.create_user(username=username, owner=owner)
        UserRoleScope.objects.create(
            user=user, owner=owner, role=UserRoleScope.Role.OWNER_SALESPERSON
        )
        return user

    def setUp(self):
        self.factory = RequestFactory()
        self.model_admin = CustomerAdmin(Customer, admin.site)
        self.add_url = reverse("admin:baseinfo_customer_add")
        self.client.force_login(self.admin_user)

    def form(self, *, data=None, initial=None, user=None, query=None):
        request = self.factory.get(self.add_url, query or {})
        request.user = user or self.admin_user
        return self.model_admin.get_form(request)(data=data, initial=initial)

    def payload(self, *, owner=None, salesperson=None):
        return {
            "owner": (owner or self.owner).pk,
            "salesperson": (salesperson or self.salesperson).pk,
            "code": "CUSTOMER-ADMIN-001",
            "name": "测试终端客户",
            "is_active": "on",
            "owner_bindings-TOTAL_FORMS": "0",
            "owner_bindings-INITIAL_FORMS": "0",
            "owner_bindings-MIN_NUM_FORMS": "0",
            "owner_bindings-MAX_NUM_FORMS": "1000",
            "_save": "保存",
        }

    def assert_salespeople(self, form, expected):
        self.assertCountEqual(form.fields["salesperson"].queryset, expected)

    def test_no_selected_owner_has_no_salesperson_options(self):
        self.assert_salespeople(self.form(), [])

    def test_initial_owner_uses_active_salesperson_role_scope(self):
        self.assert_salespeople(
            self.form(initial={"owner": self.owner.pk}),
            [self.salesperson, self.scope_only_salesperson],
        )

    def test_add_page_preselected_owner_renders_filtered_options_and_linkage(self):
        response = self.client.get(self.add_url, {"owner": self.owner.pk})
        self.assertEqual(response.status_code, 200)
        form = response.context["adminform"].form
        self.assert_salespeople(form, [self.salesperson, self.scope_only_salesperson])
        html = response.content.decode()
        self.assertTrue("admin/customer_owner_linkage.js" in html, "Missing linkage script")
        options_url = reverse("admin:baseinfo_customer_salesperson_options")
        self.assertTrue(
            f'data-source-url="{options_url}"' in html,
            "Salesperson select must expose its options endpoint in the rendered HTML",
        )

    def test_posted_owner_takes_precedence_over_initial_and_query_string(self):
        form = self.form(
            data=self.payload(owner=self.other_owner, salesperson=self.other_salesperson),
            initial={"owner": self.owner.pk},
            query={"owner": self.owner.pk},
        )
        self.assert_salespeople(form, [self.other_salesperson, self.stale_legacy_salesperson])
        self.assertTrue(form.is_valid(), form.errors)

    def test_blank_posted_owner_does_not_fall_back_to_initial_owner(self):
        form = self.form(data={**self.payload(), "owner": ""}, initial={"owner": self.owner.pk})
        self.assert_salespeople(form, [])
        self.assertFalse(form.is_valid())

    def test_cross_owner_salesperson_is_rejected_without_saving(self):
        response = self.client.post(self.add_url, self.payload(salesperson=self.other_salesperson))
        self.assertEqual(response.status_code, 200)
        self.assertIn("salesperson", response.context["adminform"].form.errors)
        self.assertFalse(Customer.objects.exists())
        self.assertFalse(OwnerCustomerBinding.objects.exists())

    def test_selected_owner_salesperson_saves_customer_and_binding(self):
        response = self.client.post(self.add_url, self.payload())
        self.assertEqual(response.status_code, 302)
        customer = Customer.objects.get(code="CUSTOMER-ADMIN-001")
        self.assertEqual(
            (customer.owner_id, customer.salesperson_id), (self.owner.pk, self.salesperson.pk)
        )
        binding = customer.owner_bindings.get()
        self.assertEqual(
            (binding.owner_id, binding.salesperson_id), (self.owner.pk, self.salesperson.pk)
        )

    def test_invalid_or_empty_owner_has_no_options(self):
        for owner in ("", "invalid", "-1", str(self.empty_owner.pk)):
            with self.subTest(owner=owner):
                self.assert_salespeople(self.form(initial={"owner": owner}), [])

    def test_form_cannot_load_salespeople_for_an_inaccessible_owner(self):
        self.assert_salespeople(
            self.form(initial={"owner": self.other_owner.pk}, user=self.manager), []
        )

    def test_options_endpoint_matches_form_filter(self):
        response = self.client.get(
            reverse("admin:baseinfo_customer_salesperson_options"), {"owner": self.owner.pk}
        )
        self.assertEqual(response.status_code, 200)
        self.assertCountEqual(
            response.json()["results"],
            [
                {"id": self.salesperson.pk, "text": str(self.salesperson)},
                {"id": self.scope_only_salesperson.pk, "text": str(self.scope_only_salesperson)},
            ],
        )

    def test_options_endpoint_returns_empty_for_missing_invalid_or_empty_owner(self):
        url = reverse("admin:baseinfo_customer_salesperson_options")
        for query in ({}, {"owner": "bad"}, {"owner": "-1"}, {"owner": self.empty_owner.pk}):
            with self.subTest(query=query):
                response = self.client.get(url, query)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), {"results": []})

    def test_options_endpoint_respects_requesting_users_owner_scope(self):
        self.client.force_login(self.manager)
        url = reverse("admin:baseinfo_customer_salesperson_options")
        own_response = self.client.get(url, {"owner": self.owner.pk})
        self.assertEqual(own_response.status_code, 200)
        self.assertCountEqual(
            [item["id"] for item in own_response.json()["results"]],
            [self.salesperson.pk, self.scope_only_salesperson.pk],
        )
        response = self.client.get(url, {"owner": self.other_owner.pk})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"results": []})

    def test_options_endpoint_requires_customer_permission(self):
        staff = get_user_model().objects.create_user(username="unprivileged-staff", is_staff=True)
        self.client.force_login(staff)
        response = self.client.get(
            reverse("admin:baseinfo_customer_salesperson_options"), {"owner": self.owner.pk}
        )
        self.assertEqual(response.status_code, 403)

    def test_options_endpoint_requires_admin_login(self):
        self.client.logout()
        response = self.client.get(
            reverse("admin:baseinfo_customer_salesperson_options"), {"owner": self.owner.pk}
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("admin:login"), response.url)
