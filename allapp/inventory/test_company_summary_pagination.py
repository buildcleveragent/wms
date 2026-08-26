from django.test import SimpleTestCase
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from allapp.inventory.views import (
    CompanyInventoryPagination,
    CompanyInventorySummaryViewSet,
    DefaultPagination,
    OwnerInventorySummaryViewSet,
)


class CompanyInventoryPaginationTests(SimpleTestCase):
    def setUp(self):
        self.factory = APIRequestFactory()

    def page_size_for(self, value=None):
        params = {} if value is None else {"page_size": value}
        request = Request(self.factory.get("/api/inventory/company-summary/", params))
        return CompanyInventoryPagination().get_page_size(request)

    def test_company_inventory_defaults_to_100_rows(self):
        self.assertEqual(self.page_size_for(), 100)
        self.assertIs(CompanyInventorySummaryViewSet.pagination_class, CompanyInventoryPagination)

    def test_company_inventory_accepts_500_and_1000_rows(self):
        self.assertEqual(self.page_size_for(500), 500)
        self.assertEqual(self.page_size_for(1000), 1000)

    def test_company_inventory_caps_page_size_at_1000(self):
        self.assertEqual(self.page_size_for(1001), 1000)

    def test_owner_inventory_keeps_existing_pagination_limit(self):
        self.assertEqual(DefaultPagination.page_size, 10)
        self.assertEqual(DefaultPagination.max_page_size, 100)
        self.assertIs(OwnerInventorySummaryViewSet.pagination_class, DefaultPagination)
