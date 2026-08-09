import re
from pathlib import Path

from django.test import SimpleTestCase

REPO_ROOT = Path(__file__).resolve().parents[2]
CLIENT_ROOT = REPO_ROOT / "wmsownersale"
FEATURE_PAGE = CLIENT_ROOT / "pages" / "features" / "index.vue"
OWNER_ACCESS = CLIENT_ROOT / "utils" / "ownerAccess.js"
PAGES_MANIFEST = CLIENT_ROOT / "pages.json"

EXPECTED_FEATURE_ROUTES = {
    "/pages/warehouses/select",
    "/pages/orders/index",
    "/pages/orders/import_drop_ship",
    "/pages/inventory/index",
    "/pages/reports/operations",
    "/pages/reports/index",
    "/pages/approval/index",
    "/pages/billing/overview",
}


def feature_items(source):
    return re.findall(
        r"\{\s*key:\s*'([^']+)'.*?path:\s*'(/pages/[^']+)'",
        source,
        flags=re.DOTALL,
    )


class OwnerFeatureMenuContractTests(SimpleTestCase):
    def setUp(self):
        self.source = FEATURE_PAGE.read_text(encoding="utf-8")
        self.menu_source = OWNER_ACCESS.read_text(encoding="utf-8")
        self.items = feature_items(self.menu_source)

    def test_every_feature_route_is_registered_and_has_a_component(self):
        manifest_source = PAGES_MANIFEST.read_text(encoding="utf-8")
        declared_routes = {
            f"/{route}"
            for route in re.findall(
                r'"path"\s*:\s*"([^"]+)"',
                manifest_source,
            )
        }
        feature_routes = {route for _, route in self.items}

        self.assertEqual(feature_routes, EXPECTED_FEATURE_ROUTES)
        self.assertTrue(feature_routes.issubset(declared_routes))
        for route in feature_routes:
            with self.subTest(route=route):
                self.assertTrue(
                    (CLIENT_ROOT / f"{route.removeprefix('/')}.vue").is_file()
                )

    def test_feature_keys_and_paths_are_unique(self):
        keys = [key for key, _ in self.items]
        routes = [route for _, route in self.items]

        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(len(routes), len(set(routes)))

    def test_obsolete_and_unimplemented_routes_are_not_exposed(self):
        obsolete_routes = (
            "/pages/orders/list",
            "/pages/admin/pending",
            "/pages/vansales/",
            "/pages/visit/",
            "/pages/customer/create",
            "/pages/replenishment/",
            "/pages/transfer/",
            "/pages/analytics/",
            "/pages/contacts/",
            "/pages/orders/near_expiry_sale",
        )

        for route in obsolete_routes:
            with self.subTest(route=route):
                self.assertNotIn(route, self.menu_source)

    def test_role_and_capability_filters_use_the_auth_store(self):
        self.assertIn("const auth = useAuth()", self.source)
        self.assertIn("buildOwnerMenu", self.source)
        self.assertIn("roles: auth.roles", self.source)
        self.assertIn("capabilities: auth.capabilities", self.source)
        self.assertIn("roleSet.has('owner_salesperson')", self.menu_source)
        self.assertIn("roleSet.has('owner_manager')", self.menu_source)
        self.assertIn("capabilities?.can_view_owner_operations", self.menu_source)
        self.assertNotIn("getStorageSync('isAdmin')", self.source)

    def test_report_tab_and_regular_pages_use_the_correct_navigation_methods(self):
        self.assertRegex(
            self.menu_source,
            r"key: 'sales_reports'.*?path: '/pages/reports/index'.*?navigation: 'tab'",
        )
        self.assertIn("if (item.navigation === 'tab')", self.source)
        self.assertIn("uni.switchTab(options)", self.source)
        self.assertIn("uni.navigateTo(options)", self.source)
        self.assertIn("fail: navigationFailed", self.source)
