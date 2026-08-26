from django.contrib import admin
from django.test import RequestFactory, SimpleTestCase
from django.urls import reverse

from allapp.products.models import Product, ProductPackage


class ProductAdminPaginationTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.product_admin = admin.site._registry[Product]

    def test_product_changelist_uses_500_rows_per_page(self):
        request = self.factory.get(reverse("admin:products_product_changelist"))
        paginator = self.product_admin.get_paginator(
            request,
            range(501),
            self.product_admin.list_per_page,
        )

        self.assertEqual(self.product_admin.list_per_page, 500)
        self.assertEqual(paginator.per_page, 500)
        self.assertEqual(paginator.count, 501)
        self.assertEqual(paginator.num_pages, 2)
        self.assertEqual(len(paginator.page(1).object_list), 500)
        self.assertEqual(len(paginator.page(2).object_list), 1)

    def test_product_package_admin_keeps_shared_page_size(self):
        package_admin = admin.site._registry[ProductPackage]

        self.assertEqual(package_admin.list_per_page, 30)
