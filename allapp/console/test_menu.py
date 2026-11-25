from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Group, Permission
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.urls import reverse

from allapp.accounts.access import AccessScope
from allapp.accounts.models import UserRoleScope
from allapp.accounts.roles import ROLE_GROUP_TEMPLATES
from allapp.baseinfo.models import Owner
from allapp.console.menu import build_ribbon
from allapp.locations.models import Warehouse


def create_navigation_accounts():
    """Create realistic staff and role bindings for both HTTP and browser checks."""
    warehouse = Warehouse.objects.create(code="NAV-WH", name="导航验收仓")
    owner = Owner.objects.create(code="NAV-OW", name="导航验收货主")
    users = {}
    for name, role, scope in (
        ("nav-warehouse", UserRoleScope.Role.WAREHOUSE_MANAGER, {"warehouse": warehouse}),
        ("nav-owner", UserRoleScope.Role.OWNER_MANAGER, {"owner": owner}),
        ("nav-empty", UserRoleScope.Role.OWNER_MANAGER, {"owner": owner}),
    ):
        user = get_user_model().objects.create_user(
            username=name, password="test-navigation", is_staff=True, **scope
        )
        UserRoleScope.objects.create(user=user, role=role, **scope)
        if name != "nav-empty":
            template = ROLE_GROUP_TEMPLATES[role]
            group, _ = Group.objects.get_or_create(name=template.group_name)
            group.permissions.set([permission(code) for code in template.permissions])
            user.groups.add(group)
        users[name] = user
    return users


def permission(qualified_name):
    app_label, codename = qualified_name.split(".")
    return Permission.objects.get(content_type__app_label=app_label, codename=codename)


def action_urls(menu):
    return {
        action["url"]
        for tab in menu["tabs"]
        for group in tab["groups"]
        for action in group["actions"]
    }


class AnonymousRibbonTests(SimpleTestCase):
    def test_anonymous_user_has_no_tabs(self):
        request = RequestFactory().get("/")
        request.user = AnonymousUser()
        self.assertEqual(build_ribbon(request), {"tabs": [], "active_index": 0})


class NavigationAccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.users = create_navigation_accounts()

    def menu(self, name, path="/"):
        request = RequestFactory().get(path)
        request.user = get_user_model().objects.get(username=name)
        return build_ribbon(request)

    def test_roles_only_get_authorized_destinations_without_requiring_business_rows(self):
        inbound = reverse("admin:inbound_inboundorder_changelist")
        stock = reverse("admin:inventory_inventorydetail_changelist")
        receive = reverse("tasking_console:task_receive_list")
        for name, allowed, denied in (
            ("nav-warehouse", [stock, receive], []),
            ("nav-owner", [inbound], [stock, receive]),
            ("nav-empty", [], [stock, receive, inbound, reverse("admin:index")]),
        ):
            with self.subTest(name=name):
                menu = self.menu(name)
                urls = action_urls(menu)
                self.assertTrue(set(allowed).issubset(urls))
                self.assertTrue(set(denied).isdisjoint(urls))
                self.client.force_login(self.users[name])
                for url in allowed:
                    self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(self.menu("nav-empty")["tabs"], [])

    def test_nonstaff_owner_cannot_navigate_to_admin(self):
        user = self.users["nav-owner"]
        user.is_staff = False
        user.save(update_fields=["is_staff"])
        self.assertFalse(
            any(
                url.startswith(reverse("admin:index"))
                for url in action_urls(self.menu(user.username))
            )
        )

    def test_catalog_reads_match_destination_and_active_tab_uses_route(self):
        user = self.users["nav-empty"]
        catalog = reverse("console:sale_mini_product_listing")
        user.user_permissions.add(permission("salesapp.change_saleproductconfig"))
        self.assertNotIn(catalog, action_urls(self.menu(user.username)))
        self.client.force_login(user)
        self.assertEqual(self.client.get(catalog).status_code, 403)

        user.user_permissions.set([permission("products.view_product")])
        menu = self.menu(user.username, catalog)
        self.assertIn(catalog, action_urls(menu))
        self.assertEqual(menu["tabs"][menu["active_index"]]["label"], "商城")
        self.assertEqual(self.client.get(catalog).status_code, 200)

    def test_custom_admin_permissions_and_scope_are_honored(self):
        user = self.users["nav-empty"]
        stock = reverse("admin:inventory_inventorysummary_changelist")
        groups = reverse("admin:auth_group_changelist")
        user.user_permissions.set(
            [permission("auth.view_group"), permission("inventory.view_inventorysummary")]
        )
        self.client.force_login(user)
        self.assertNotIn(groups, action_urls(self.menu(user.username)))
        self.assertEqual(self.client.get(groups).status_code, 403)
        self.assertIn(stock, action_urls(self.menu(user.username)))
        UserRoleScope.objects.filter(user=user).delete()
        self.assertNotIn(stock, action_urls(self.menu(user.username)))
        self.assertEqual(self.client.get(stock).status_code, 403)

    def test_read_scope_cache_follows_the_account_and_skips_writes(self):
        request = RequestFactory().get("/")
        request.user = self.users["nav-warehouse"]
        warehouse_scope = AccessScope.for_request(request)
        self.assertTrue(warehouse_scope.warehouse_ids)
        with self.assertNumQueries(0):
            self.assertIs(AccessScope.for_request(request), warehouse_scope)
        request.user = self.users["nav-owner"]
        self.assertTrue(AccessScope.for_request(request).owner_ids)

        request = RequestFactory().post("/")
        request.user = self.users["nav-empty"]
        self.assertTrue(AccessScope.for_request(request).is_valid)
        UserRoleScope.objects.filter(user=request.user).delete()
        self.assertFalse(AccessScope.for_request(request).is_valid)

    def test_staff_superuser_sees_registered_user_admin_and_billing(self):
        user = get_user_model().objects.create_superuser(username="nav-root", password="pw")
        urls = action_urls(self.menu(user.username))
        self.assertIn(reverse("admin:accounts_user_changelist"), urls)
        self.assertIn(reverse("console:billing_overview"), urls)
        self.assertIn(reverse("admin:index"), urls)
