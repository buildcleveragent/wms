from io import StringIO

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from allapp.accounts.admin import PermissionMatrixWidget
from allapp.accounts.access import AccessScope, scope_queryset_for_user
from allapp.accounts.models import UserRoleScope
from allapp.accounts.roles import ROLE_GROUP_TEMPLATES
from allapp.baseinfo.models import Owner
from allapp.locations.models import Warehouse


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


class UserRoleScopeModelTests(TestCase):
    def setUp(self):
        self.owner = Owner.objects.create(name="Role Owner", code="ROLEOWN")
        self.warehouse = Warehouse.objects.create(code="ROLEWH1", name="Role Warehouse 1")
        self.other_warehouse = Warehouse.objects.create(
            code="ROLEWH2",
            name="Role Warehouse 2",
        )
        self.user = get_user_model().objects.create_user(
            username="role-scope-user",
            password="x",
        )

    def test_warehouse_role_requires_only_warehouse_target(self):
        with self.assertRaises(ValidationError):
            UserRoleScope.objects.create(
                user=self.user,
                role=UserRoleScope.Role.WAREHOUSE_OPERATOR,
            )

        with self.assertRaises(ValidationError):
            UserRoleScope.objects.create(
                user=self.user,
                role=UserRoleScope.Role.WAREHOUSE_OPERATOR,
                owner=self.owner,
                warehouse=self.warehouse,
            )

    def test_owner_role_requires_only_owner_target(self):
        with self.assertRaises(ValidationError):
            UserRoleScope.objects.create(
                user=self.user,
                role=UserRoleScope.Role.OWNER_MANAGER,
                warehouse=self.warehouse,
            )

    def test_warehouse_boss_can_have_multiple_warehouse_scopes(self):
        UserRoleScope.objects.create(
            user=self.user,
            role=UserRoleScope.Role.WAREHOUSE_BOSS,
            warehouse=self.warehouse,
        )
        UserRoleScope.objects.create(
            user=self.user,
            role=UserRoleScope.Role.WAREHOUSE_BOSS,
            warehouse=self.other_warehouse,
        )

        self.assertEqual(self.user.role_scopes.filter(is_active=True).count(), 2)

    def test_non_boss_role_rejects_multiple_active_scopes(self):
        UserRoleScope.objects.create(
            user=self.user,
            role=UserRoleScope.Role.WAREHOUSE_MANAGER,
            warehouse=self.warehouse,
        )

        with self.assertRaises(ValidationError):
            UserRoleScope.objects.create(
                user=self.user,
                role=UserRoleScope.Role.WAREHOUSE_MANAGER,
                warehouse=self.other_warehouse,
            )

    def test_user_rejects_two_active_roles(self):
        UserRoleScope.objects.create(
            user=self.user,
            role=UserRoleScope.Role.WAREHOUSE_OPERATOR,
            warehouse=self.warehouse,
        )

        with self.assertRaises(ValidationError):
            UserRoleScope.objects.create(
                user=self.user,
                role=UserRoleScope.Role.OWNER_SALESPERSON,
                owner=self.owner,
            )


class AccessScopeTests(TestCase):
    def setUp(self):
        self.owner = Owner.objects.create(name="Scope Owner", code="SCOPEOWN")
        self.other_owner = Owner.objects.create(
            name="Scope Other Owner",
            code="SCOPEOTH",
        )
        self.warehouse = Warehouse.objects.create(code="SCOPEW1", name="Scope Warehouse 1")
        self.other_warehouse = Warehouse.objects.create(
            code="SCOPEW2",
            name="Scope Warehouse 2",
        )
        User = get_user_model()
        self.target_own_wh = User.objects.create_user(
            username="target-own-wh",
            owner=self.owner,
            warehouse=self.warehouse,
        )
        self.target_own_other_wh = User.objects.create_user(
            username="target-own-other-wh",
            owner=self.owner,
            warehouse=self.other_warehouse,
        )
        self.target_other_owner = User.objects.create_user(
            username="target-other-owner",
            owner=self.other_owner,
            warehouse=self.warehouse,
        )

    def target_queryset(self):
        return get_user_model().objects.filter(username__startswith="target-")

    def grant(self, user, app_label, codename):
        permission = Permission.objects.get(
            content_type__app_label=app_label,
            codename=codename,
        )
        user.user_permissions.add(permission)

    def test_superuser_is_global(self):
        superuser = get_user_model().objects.create_superuser(
            username="scope-superuser",
            password="x",
        )

        scope = AccessScope.for_user(superuser)

        self.assertTrue(scope.is_valid)
        self.assertTrue(scope.is_global)
        self.assertEqual(scope.filter_queryset(self.target_queryset()).count(), 3)
        self.assertTrue(scope.allows(owner_id=999, warehouse_id=999))
        self.assertEqual(
            scope.as_dict(),
            {
                "roles": ["superuser"],
                "owner_ids": [],
                "warehouse_ids": [],
                "is_global": True,
                "source": "superuser",
            },
        )

    def test_unbound_user_fails_closed(self):
        user = get_user_model().objects.create_user(username="scope-unbound")

        scope = AccessScope.for_user(user)

        self.assertFalse(scope.is_valid)
        self.assertEqual(scope.denial_reason, "unbound_user")
        self.assertFalse(scope.allows(owner_id=self.owner.id))
        self.assertFalse(scope_queryset_for_user(self.target_queryset(), user).exists())

    def test_explicit_warehouse_role_filters_to_warehouse(self):
        user = get_user_model().objects.create_user(username="scope-warehouse")
        UserRoleScope.objects.create(
            user=user,
            role=UserRoleScope.Role.WAREHOUSE_OPERATOR,
            warehouse=self.warehouse,
        )

        scope = AccessScope.for_user(user)
        usernames = set(
            scope.filter_queryset(self.target_queryset()).values_list("username", flat=True)
        )

        self.assertEqual(scope.roles, {UserRoleScope.Role.WAREHOUSE_OPERATOR})
        self.assertEqual(scope.warehouse_ids, {self.warehouse.id})
        self.assertEqual(usernames, {"target-own-wh", "target-other-owner"})
        self.assertTrue(scope.allows(warehouse_id=str(self.warehouse.id)))
        self.assertFalse(scope.allows(warehouse_id=self.other_warehouse.id))

    def test_explicit_owner_role_filters_owner_and_ignores_legacy_warehouse(self):
        user = get_user_model().objects.create_user(
            username="scope-owner-explicit",
            owner=self.owner,
            warehouse=self.warehouse,
        )
        UserRoleScope.objects.create(
            user=user,
            role=UserRoleScope.Role.OWNER_MANAGER,
            owner=self.owner,
        )

        scope = AccessScope.for_user(user)
        usernames = set(
            scope.filter_queryset(self.target_queryset()).values_list("username", flat=True)
        )

        self.assertEqual(scope.owner_ids, {self.owner.id})
        self.assertFalse(scope.warehouse_ids)
        self.assertEqual(usernames, {"target-own-wh", "target-own-other-wh"})
        self.assertTrue(
            scope.allows(
                owner_id=self.owner.id,
                warehouse_id=self.other_warehouse.id,
            )
        )

    def test_warehouse_boss_filters_to_all_explicit_warehouses(self):
        user = get_user_model().objects.create_user(username="scope-boss")
        UserRoleScope.objects.create(
            user=user,
            role=UserRoleScope.Role.WAREHOUSE_BOSS,
            warehouse=self.warehouse,
        )
        UserRoleScope.objects.create(
            user=user,
            role=UserRoleScope.Role.WAREHOUSE_BOSS,
            warehouse=self.other_warehouse,
        )

        scope = AccessScope.for_user(user)

        self.assertTrue(scope.is_valid)
        self.assertEqual(
            scope.warehouse_ids,
            {self.warehouse.id, self.other_warehouse.id},
        )
        self.assertEqual(scope.filter_queryset(self.target_queryset()).count(), 3)

    @override_settings(WMS_ACCESS_SCOPE_LEGACY_FALLBACK=True)
    def test_legacy_owner_role_never_expands_to_bound_warehouse(self):
        user = get_user_model().objects.create_user(
            username="scope-owner-legacy",
            owner=self.owner,
            warehouse=self.warehouse,
        )
        self.grant(user, "inbound", "submit_as_owner_buyers")

        scope = AccessScope.for_user(user)
        usernames = set(
            scope.filter_queryset(self.target_queryset()).values_list("username", flat=True)
        )

        self.assertTrue(scope.is_valid)
        self.assertEqual(scope.roles, {UserRoleScope.Role.OWNER_SALESPERSON})
        self.assertEqual(scope.owner_ids, {self.owner.id})
        self.assertFalse(scope.warehouse_ids)
        self.assertEqual(usernames, {"target-own-wh", "target-own-other-wh"})

    def test_legacy_binding_is_denied_by_default_without_explicit_scope(self):
        user = get_user_model().objects.create_user(
            username="scope-owner-strict",
            owner=self.owner,
            warehouse=self.warehouse,
        )
        self.grant(user, "inbound", "submit_as_owner_buyers")

        scope = AccessScope.for_user(user)

        self.assertFalse(scope.is_valid)
        self.assertEqual(scope.denial_reason, "missing_explicit_role_scope")
        self.assertFalse(scope.filter_queryset(self.target_queryset()).exists())

    @override_settings(WMS_ACCESS_SCOPE_LEGACY_FALLBACK=True)
    def test_conflicting_legacy_roles_fail_closed(self):
        user = get_user_model().objects.create_user(
            username="scope-conflicting-legacy",
            owner=self.owner,
            warehouse=self.warehouse,
        )
        self.grant(user, "inbound", "submit_as_owner_buyers")
        self.grant(user, "tasking", "taskconfirm_as_wh_manager")

        scope = AccessScope.for_user(user)

        self.assertFalse(scope.is_valid)
        self.assertEqual(scope.denial_reason, "conflicting_legacy_roles")
        self.assertFalse(scope.filter_queryset(self.target_queryset()).exists())

    def test_conflicting_explicit_rows_fail_closed_even_if_validation_was_bypassed(self):
        user = get_user_model().objects.create_user(username="scope-conflicting-explicit")
        UserRoleScope.objects.bulk_create(
            [
                UserRoleScope(
                    user=user,
                    role=UserRoleScope.Role.WAREHOUSE_OPERATOR,
                    warehouse=self.warehouse,
                ),
                UserRoleScope(
                    user=user,
                    role=UserRoleScope.Role.OWNER_SALESPERSON,
                    owner=self.owner,
                ),
            ]
        )

        scope = AccessScope.for_user(user)

        self.assertFalse(scope.is_valid)
        self.assertEqual(scope.denial_reason, "conflicting_explicit_roles")


class SyncWmsRoleGroupsCommandTests(TestCase):
    def test_dry_run_does_not_create_groups(self):
        output = StringIO()

        call_command("sync_wms_role_groups", dry_run=True, stdout=output)

        self.assertFalse(
            Group.objects.filter(
                name__in=[template.group_name for template in ROLE_GROUP_TEMPLATES.values()]
            ).exists()
        )
        self.assertIn("[dry-run]", output.getvalue())

    def test_command_creates_all_groups_with_template_permissions_and_is_idempotent(self):
        call_command("sync_wms_role_groups", stdout=StringIO())
        call_command("sync_wms_role_groups", stdout=StringIO())

        self.assertEqual(
            Group.objects.filter(
                name__in=[template.group_name for template in ROLE_GROUP_TEMPLATES.values()]
            ).count(),
            5,
        )
        for template in ROLE_GROUP_TEMPLATES.values():
            group = Group.objects.get(name=template.group_name)
            actual = {
                f"{permission.content_type.app_label}.{permission.codename}"
                for permission in group.permissions.select_related("content_type")
            }
            self.assertTrue(set(template.permissions).issubset(actual))

    def test_prune_is_opt_in(self):
        call_command("sync_wms_role_groups", stdout=StringIO())
        template = next(iter(ROLE_GROUP_TEMPLATES.values()))
        group = Group.objects.get(name=template.group_name)
        extra = Permission.objects.get(
            content_type__app_label="auth",
            codename="view_group",
        )
        group.permissions.add(extra)

        call_command("sync_wms_role_groups", stdout=StringIO())
        self.assertTrue(group.permissions.filter(pk=extra.pk).exists())

        call_command("sync_wms_role_groups", prune=True, stdout=StringIO())
        self.assertFalse(group.permissions.filter(pk=extra.pk).exists())


class AuditWmsRoleScopesCommandTests(TestCase):
    def test_csv_reports_mixed_binding_missing_scope_and_role_conflict_read_only(self):
        owner = Owner.objects.create(name="Audit Scope Owner", code="AUDSCOPE")
        warehouse = Warehouse.objects.create(code="AUDWH1", name="Audit Warehouse")
        get_user_model().objects.create_user(
            username="audit-mixed-user",
            owner=owner,
            warehouse=warehouse,
        )
        conflict_user = get_user_model().objects.create_user(
            username="audit-conflict-user"
        )
        UserRoleScope.objects.create(
            user=conflict_user,
            role=UserRoleScope.Role.OWNER_MANAGER,
            owner=owner,
        )
        warehouse_permission = Permission.objects.get(
            content_type__app_label="tasking",
            codename="taskconfirm_as_wh_manager",
        )
        conflict_user.user_permissions.add(warehouse_permission)
        scope_count = UserRoleScope.objects.count()
        group_count = Group.objects.count()
        output = StringIO()

        call_command("audit_wms_role_scopes", format="csv", stdout=output)

        csv_output = output.getvalue()
        self.assertIn("audit-mixed-user", csv_output)
        self.assertIn("LEGACY_OWNER_WAREHOUSE_MIXED", csv_output)
        self.assertIn("MISSING_EXPLICIT_SCOPE", csv_output)
        self.assertIn("audit-conflict-user", csv_output)
        self.assertIn("ROLE_GROUP_CONFLICT", csv_output)
        self.assertEqual(UserRoleScope.objects.count(), scope_count)
        self.assertEqual(Group.objects.count(), group_count)
