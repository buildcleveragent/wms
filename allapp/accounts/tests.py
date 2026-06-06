from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse


class AccountsWarehouseScopeTests(TestCase):
    def test_user_without_warehouse_stays_null(self):
        user = get_user_model().objects.create_user(
            username="user-no-warehouse",
            password="x",
        )

        self.assertIsNone(user.warehouse_id)


class PasswordChangeTests(TestCase):
    def test_authenticated_user_can_change_own_password(self):
        user = get_user_model().objects.create_user(
            username="password-user",
            password="OldPass12345",
        )
        self.client.force_login(user)

        response = self.client.get(reverse("password_change"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "修改密码")

        response = self.client.post(
            reverse("password_change"),
            {
                "old_password": "OldPass12345",
                "new_password1": "NewPass12345!",
                "new_password2": "NewPass12345!",
            },
        )

        self.assertRedirects(response, reverse("password_change_done"))
        user.refresh_from_db()
        self.assertTrue(user.check_password("NewPass12345!"))
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)
