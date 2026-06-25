from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import RequestFactory, TestCase
from django.urls import reverse

from allapp.accounts.admin import PermissionMatrixWidget


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


class GroupAdminPermissionMatrixTests(TestCase):
    def setUp(self):
        self.request = RequestFactory().get("/admin/auth/group/add/")
        self.request.user = get_user_model().objects.create_superuser(
            username="admin",
            password="admin",
            email="admin@example.com",
        )
        self.model_admin = admin.site._registry[Group]

    def test_group_admin_uses_permission_matrix_widget(self):
        form_class = self.model_admin.get_form(self.request)
        form = form_class()

        self.assertIsInstance(form.fields["permissions"].widget, PermissionMatrixWidget)
        html = form["permissions"].as_widget()

        self.assertIn("data-permission-matrix", html)
        self.assertIn('type="checkbox"', html)
        self.assertIn("POS销售单", html)

    def test_group_admin_permission_matrix_saves_selected_permissions(self):
        permission = Permission.objects.get(codename="add_possale")
        form_class = self.model_admin.get_form(self.request)
        form = form_class(
            data={
                "name": "POS收银员",
                "permissions": [str(permission.pk)],
            }
        )

        self.assertTrue(form.is_valid(), form.errors)
        group = form.save()

        self.assertTrue(group.permissions.filter(pk=permission.pk).exists())
