from django.contrib.auth import get_user_model
from django.test import TestCase


class AccountsWarehouseScopeTests(TestCase):
    def test_user_without_warehouse_stays_null(self):
        user = get_user_model().objects.create_user(
            username="user-no-warehouse",
            password="x",
        )

        self.assertIsNone(user.warehouse_id)
