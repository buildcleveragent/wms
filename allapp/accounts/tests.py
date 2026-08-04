import json
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient, APIRequestFactory, force_authenticate
from rest_framework_simplejwt.tokens import AccessToken

from allapp.accounts.access import AccessScope, scope_queryset_for_user
from allapp.accounts.admin import PermissionMatrixWidget
from allapp.accounts.audit import record_audit_event
from allapp.accounts.models import AuditEvent, UserRoleScope
from allapp.accounts.role_memberships import (
    ROLE_GROUP_NAMES,
    sync_user_role_membership,
)
from allapp.accounts.roles import ROLE_GROUP_TEMPLATES, role_group_name
from allapp.baseinfo.models import Owner
from allapp.locations.models import Warehouse
from wmsmaster.views import profile_view


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


@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
)
class AuthenticationAuditTests(TestCase):
    login_url = "/api/auth/login/"

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="jwt-audit-user",
            password="StrongPass123!",
        )
        self.client = APIClient()

    def test_jwt_login_returns_tokens_and_records_request_context(self):
        response = self.client.post(
            self.login_url,
            {"username": self.user.username, "password": "StrongPass123!"},
            format="json",
            HTTP_X_REQUEST_ID="login-request-123",
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)
        self.assertEqual(response.data["user"]["id"], self.user.pk)

        event = AuditEvent.objects.get(action="LOGIN", module="authentication")
        self.assertEqual(event.actor_id, self.user.pk)
        self.assertEqual(event.username, self.user.username)
        self.assertEqual(event.request_id, "login-request-123")
        self.assertEqual(event.method, "POST")
        self.assertEqual(event.path, self.login_url)
        self.assertTrue(event.succeeded)

    def test_failed_login_is_audited_without_storing_password(self):
        supplied_password = "NeverPersistThisPassword!"

        response = self.client.post(
            self.login_url,
            {"username": self.user.username, "password": supplied_password},
            format="json",
        )

        self.assertEqual(response.status_code, 401)
        event = AuditEvent.objects.get(
            action="LOGIN_FAILED",
            module="authentication.session",
        )
        self.assertFalse(event.succeeded)
        self.assertEqual(event.metadata["attempted_identity"], self.user.username)
        persisted_payload = json.dumps(
            {
                "before": event.before,
                "after": event.after,
                "metadata": event.metadata,
            }
        )
        self.assertNotIn(supplied_password, persisted_payload)

    @patch("wmsmaster.auth_views.record_audit_event", side_effect=RuntimeError("down"))
    def test_audit_store_failure_does_not_block_jwt_login(self, mocked_audit):
        response = self.client.post(
            self.login_url,
            {"username": self.user.username, "password": "StrongPass123!"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("access", response.data)
        mocked_audit.assert_called_once()


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class JwtSessionLifecycleTests(TestCase):
    def setUp(self):
        self.password = "StrongPass123!"
        self.user = get_user_model().objects.create_user(
            username="jwt-session-user",
            password=self.password,
        )
        self.client = APIClient()

    def login(self):
        response = self.client.post(
            "/api/auth/login/",
            {"username": self.user.username, "password": self.password},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def test_refresh_rotates_and_blacklists_the_old_refresh(self):
        tokens = self.login()
        rotated = self.client.post(
            "/api/auth/refresh/", {"refresh": tokens["refresh"]}, format="json"
        )
        self.assertEqual(rotated.status_code, 200, rotated.data)
        self.assertIn("refresh", rotated.data)

        replay = self.client.post(
            "/api/auth/refresh/", {"refresh": tokens["refresh"]}, format="json"
        )
        self.assertEqual(replay.status_code, 401)

    def test_logout_blacklists_refresh(self):
        tokens = self.login()
        response = self.client.post(
            "/api/auth/logout/", {"refresh": tokens["refresh"]}, format="json"
        )
        self.assertEqual(response.status_code, 204)
        self.client.credentials()
        replay = self.client.post(
            "/api/auth/refresh/", {"refresh": tokens["refresh"]}, format="json"
        )
        self.assertEqual(replay.status_code, 401)

    def test_password_change_revokes_existing_access_and_refresh(self):
        tokens = self.login()
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {tokens["access"]}')
        changed = self.client.post(
            "/api/auth/password/change/",
            {
                "old_password": self.password,
                "new_password1": "NewStrongPass123!",
                "new_password2": "NewStrongPass123!",
            },
            format="json",
        )
        self.assertEqual(changed.status_code, 200, changed.data)
        self.user.refresh_from_db()
        self.assertNotEqual(
            AccessToken(tokens["access"]).get("hash_password"),
            AccessToken.for_user(self.user).get("hash_password"),
        )

        profile = self.client.get("/api/auth/profile/")
        self.assertEqual(profile.status_code, 401)
        self.client.credentials()
        refreshed = self.client.post(
            "/api/auth/refresh/", {"refresh": tokens["refresh"]}, format="json"
        )
        self.assertEqual(refreshed.status_code, 401)

    def test_health_endpoints_are_public_and_ready_checks_database(self):
        self.assertEqual(self.client.get("/healthz/live").status_code, 200)
        self.assertEqual(self.client.get("/healthz/ready").status_code, 200)


class AuditEventImmutabilityTests(TestCase):
    def test_audit_events_cannot_be_updated_or_deleted(self):
        event = record_audit_event(action="TEST", module="accounts.tests")

        event.action = "TAMPERED"
        with self.assertRaisesMessage(ValidationError, "append-only"):
            event.save()
        with self.assertRaisesMessage(ValidationError, "append-only"):
            AuditEvent.objects.filter(pk=event.pk).update(action="TAMPERED")
        with self.assertRaisesMessage(ValidationError, "append-only"):
            event.delete()
        with self.assertRaisesMessage(ValidationError, "append-only"):
            AuditEvent.objects.filter(pk=event.pk).delete()

        event.refresh_from_db()
        self.assertEqual(event.action, "TEST")


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

    def test_only_superusers_can_manage_groups(self):
        staff = get_user_model().objects.create_user(
            username="ordinary-staff",
            is_staff=True,
        )
        request = RequestFactory().get("/admin/auth/group/")
        request.user = staff

        self.assertFalse(self.model_admin.has_module_permission(request))
        self.assertFalse(self.model_admin.has_view_permission(request))
        self.assertFalse(self.model_admin.has_add_permission(request))
        self.assertFalse(self.model_admin.has_change_permission(request))
        self.assertFalse(self.model_admin.has_delete_permission(request))

    def test_canonical_group_name_is_read_only_and_delete_is_denied(self):
        call_command("sync_wms_role_groups", stdout=StringIO())
        group = Group.objects.get(
            name=role_group_name(UserRoleScope.Role.WAREHOUSE_OPERATOR)
        )

        form = self.model_admin.get_form(self.request, group)(instance=group)

        self.assertTrue(form.fields["name"].disabled)
        self.assertIn("UserRoleScope", form.fields["permissions"].help_text)
        self.assertFalse(self.model_admin.has_delete_permission(self.request, group))

    def test_canonical_group_cannot_be_renamed_through_orm(self):
        call_command("sync_wms_role_groups", stdout=StringIO())
        group = Group.objects.get(
            name=role_group_name(UserRoleScope.Role.WAREHOUSE_OPERATOR)
        )
        group.name = "被篡改的规范组"

        with self.assertRaisesMessage(ValidationError, "规范角色组名称固定"):
            group.save()

    def test_group_permission_change_and_custom_delete_are_audited(self):
        call_command("sync_wms_role_groups", stdout=StringIO())
        group = Group.objects.get(
            name=role_group_name(UserRoleScope.Role.WAREHOUSE_OPERATOR)
        )
        extra = Permission.objects.get(
            content_type__app_label="auth",
            codename="view_group",
        )
        permission_ids = list(group.permissions.values_list("pk", flat=True)) + [
            extra.pk
        ]
        request = RequestFactory().post(f"/admin/auth/group/{group.pk}/change/")
        request.user = self.request.user
        form = self.model_admin.get_form(request, group)(
            data={"name": group.name, "permissions": permission_ids},
            instance=group,
        )
        self.assertTrue(form.is_valid(), form.errors)
        changed_group = form.save(commit=False)

        self.model_admin.save_model(request, changed_group, form, change=True)
        self.model_admin.save_related(request, form, [], change=True)

        event = AuditEvent.objects.get(
            action="ROLE_GROUP_UPDATE", object_id=str(group.pk)
        )
        self.assertTrue(event.metadata["canonical"])
        self.assertIn("auth.view_group", event.metadata["permissions"]["added"])

        custom_group = Group.objects.create(name="自定义辅助权限组")
        custom_group_id = custom_group.pk
        self.model_admin.delete_model(request, custom_group)
        self.assertTrue(
            AuditEvent.objects.filter(
                action="ROLE_GROUP_DELETE",
                object_id=str(custom_group_id),
            ).exists()
        )

    def test_group_change_page_uses_object_delete_url_and_can_delete_legacy_group(self):
        legacy_group = Group.objects.create(name="系统管理员")
        legacy_group_id = legacy_group.pk
        self.client.force_login(self.request.user)
        change_url = reverse("admin:auth_group_change", args=(legacy_group_id,))
        delete_url = reverse("admin:auth_group_delete", args=(legacy_group_id,))

        response = self.client.get(change_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'href="{delete_url}"')
        self.assertNotContains(response, 'href="delete/"')

        response = self.client.post(delete_url, {"post": "yes"})

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Group.objects.filter(pk=legacy_group_id).exists())
        self.assertTrue(
            AuditEvent.objects.filter(
                action="ROLE_GROUP_DELETE",
                object_id=str(legacy_group_id),
            ).exists()
        )

    def test_user_group_and_direct_permission_changes_are_superuser_only_and_audited(
        self,
    ):
        target = get_user_model().objects.create_user(username="authorization-target")
        group = Group.objects.create(name="授权审计辅助组")
        permission = Permission.objects.get(
            content_type__app_label="auth",
            codename="view_group",
        )
        user_admin = admin.site._registry[get_user_model()]
        request = RequestFactory().post(f"/admin/accounts/user/{target.pk}/change/")
        request.user = self.request.user
        form = SimpleNamespace(
            instance=target,
            save_m2m=lambda: (
                target.groups.add(group),
                target.user_permissions.add(permission),
            ),
        )

        user_admin.save_model(request, target, form, change=True)
        user_admin.save_related(request, form, [], change=True)

        event = AuditEvent.objects.get(
            action="USER_AUTHORIZATION_UPDATE",
            object_id=str(target.pk),
        )
        self.assertIn(group.name, event.metadata["groups"]["added"])
        self.assertIn("auth.view_group", event.metadata["user_permissions"]["added"])

        staff_request = RequestFactory().get(
            f"/admin/accounts/user/{target.pk}/change/"
        )
        staff_request.user = get_user_model().objects.create_user(
            username="user-editor",
            is_staff=True,
        )
        readonly = user_admin.get_readonly_fields(staff_request, target)
        self.assertIn("groups", readonly)
        self.assertIn("user_permissions", readonly)
        self.assertIn("is_superuser", readonly)

    def test_user_admin_hides_role_groups_and_syncs_them_from_inline_scope(self):
        call_command("sync_wms_role_groups", stdout=StringIO())
        target = get_user_model().objects.create_user(username="admin-scope-target")
        warehouse = Warehouse.objects.create(code="ADMWH", name="Admin Scope WH")
        auxiliary = Group.objects.create(name="Admin Auxiliary")
        UserRoleScope.objects.create(
            user=target,
            role=UserRoleScope.Role.WAREHOUSE_OPERATOR,
            warehouse=warehouse,
        )
        user_admin = admin.site._registry[get_user_model()]
        request = RequestFactory().post(f"/admin/accounts/user/{target.pk}/change/")
        request.user = self.request.user

        form_class = user_admin.get_form(request, target)
        rendered_form = form_class(instance=target)
        available_groups = set(
            rendered_form.fields["groups"].queryset.values_list("name", flat=True)
        )
        self.assertIn(auxiliary.name, available_groups)
        self.assertTrue(available_groups.isdisjoint(ROLE_GROUP_NAMES))

        form = SimpleNamespace(
            instance=target,
            save_m2m=lambda: target.groups.add(auxiliary),
        )
        user_admin.save_model(request, target, form, change=True)
        user_admin.save_related(request, form, [], change=True)

        self.assertTrue(
            target.groups.filter(
                name=role_group_name(UserRoleScope.Role.WAREHOUSE_OPERATOR)
            ).exists()
        )
        self.assertTrue(target.groups.filter(pk=auxiliary.pk).exists())
        event = AuditEvent.objects.get(
            action="USER_AUTHORIZATION_UPDATE", object_id=str(target.pk)
        )
        self.assertEqual(
            event.metadata["canonical_group_sync"]["desired_group"],
            role_group_name(UserRoleScope.Role.WAREHOUSE_OPERATOR),
        )


class UserRoleScopeModelTests(TestCase):
    def setUp(self):
        self.owner = Owner.objects.create(name="Role Owner", code="ROLEOWN")
        self.warehouse = Warehouse.objects.create(
            code="ROLEWH1", name="Role Warehouse 1"
        )
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


class RoleMembershipSyncTests(TestCase):
    def setUp(self):
        call_command("sync_wms_role_groups", stdout=StringIO())
        self.owner = Owner.objects.create(name="Membership Owner", code="MEMOWNER")
        self.warehouse = Warehouse.objects.create(code="MEMWH1", name="Membership WH 1")
        self.other_warehouse = Warehouse.objects.create(
            code="MEMWH2", name="Membership WH 2"
        )
        self.user = get_user_model().objects.create_user(username="membership-user")
        self.auxiliary_group = Group.objects.create(name="Membership Auxiliary")
        self.user.groups.add(self.auxiliary_group)

    def role_group_names(self):
        return set(
            self.user.groups.filter(name__in=ROLE_GROUP_NAMES).values_list(
                "name", flat=True
            )
        )

    def test_scope_syncs_canonical_group_and_preserves_auxiliary_configuration(self):
        extra_permission = Permission.objects.get(
            content_type__app_label="auth", codename="view_group"
        )
        self.user.user_permissions.add(extra_permission)
        UserRoleScope.objects.create(
            user=self.user,
            role=UserRoleScope.Role.WAREHOUSE_OPERATOR,
            warehouse=self.warehouse,
        )

        change = sync_user_role_membership(self.user)

        expected = role_group_name(UserRoleScope.Role.WAREHOUSE_OPERATOR)
        self.assertEqual(self.role_group_names(), {expected})
        self.assertTrue(self.user.groups.filter(pk=self.auxiliary_group.pk).exists())
        self.assertTrue(self.user.user_permissions.filter(pk=extra_permission.pk).exists())
        self.assertEqual(change.added, (expected,))

    def test_role_change_replaces_only_recognized_role_groups(self):
        scope = UserRoleScope.objects.create(
            user=self.user,
            role=UserRoleScope.Role.WAREHOUSE_OPERATOR,
            warehouse=self.warehouse,
        )
        old_legacy_group = Group.objects.create(name="仓库操作员")
        self.user.groups.add(old_legacy_group)
        sync_user_role_membership(self.user)

        scope.role = UserRoleScope.Role.WAREHOUSE_MANAGER
        scope.save()
        sync_user_role_membership(self.user)

        self.assertEqual(
            self.role_group_names(),
            {role_group_name(UserRoleScope.Role.WAREHOUSE_MANAGER)},
        )
        self.assertTrue(self.user.groups.filter(pk=self.auxiliary_group.pk).exists())

    def test_last_inactive_scope_removes_role_group(self):
        scope = UserRoleScope.objects.create(
            user=self.user,
            role=UserRoleScope.Role.OWNER_MANAGER,
            owner=self.owner,
        )
        sync_user_role_membership(self.user)

        scope.is_active = False
        scope.save()
        change = sync_user_role_membership(self.user)

        self.assertFalse(self.role_group_names())
        self.assertTrue(change.removed)

    def test_warehouse_boss_keeps_group_until_last_scope_is_deleted(self):
        first = UserRoleScope.objects.create(
            user=self.user,
            role=UserRoleScope.Role.WAREHOUSE_BOSS,
            warehouse=self.warehouse,
        )
        second = UserRoleScope.objects.create(
            user=self.user,
            role=UserRoleScope.Role.WAREHOUSE_BOSS,
            warehouse=self.other_warehouse,
        )
        expected = role_group_name(UserRoleScope.Role.WAREHOUSE_BOSS)
        legacy_supervisor = Group.objects.create(name="仓库主管")
        self.user.groups.add(legacy_supervisor)
        sync_user_role_membership(self.user)
        self.assertFalse(self.user.groups.filter(pk=legacy_supervisor.pk).exists())

        first.delete()
        sync_user_role_membership(self.user)
        self.assertEqual(self.role_group_names(), {expected})

        second.delete()
        sync_user_role_membership(self.user)
        self.assertFalse(self.role_group_names())

    def test_sync_does_not_change_owner_customized_group_permissions(self):
        group = Group.objects.get(
            name=role_group_name(UserRoleScope.Role.OWNER_SALESPERSON)
        )
        extra_permission = Permission.objects.get(
            content_type__app_label="auth", codename="view_group"
        )
        group.permissions.add(extra_permission)
        before = set(group.permissions.values_list("pk", flat=True))
        UserRoleScope.objects.create(
            user=self.user,
            role=UserRoleScope.Role.OWNER_SALESPERSON,
            owner=self.owner,
        )

        sync_user_role_membership(self.user)

        self.assertEqual(set(group.permissions.values_list("pk", flat=True)), before)


class AccessScopeTests(TestCase):
    def setUp(self):
        self.owner = Owner.objects.create(name="Scope Owner", code="SCOPEOWN")
        self.other_owner = Owner.objects.create(
            name="Scope Other Owner",
            code="SCOPEOTH",
        )
        self.warehouse = Warehouse.objects.create(
            code="SCOPEW1", name="Scope Warehouse 1"
        )
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
            scope.filter_queryset(self.target_queryset()).values_list(
                "username", flat=True
            )
        )

        self.assertEqual(scope.roles, {UserRoleScope.Role.WAREHOUSE_OPERATOR})
        self.assertEqual(scope.warehouse_ids, {self.warehouse.id})
        self.assertEqual(usernames, {"target-own-wh", "target-other-owner"})
        self.assertTrue(scope.allows(warehouse_id=str(self.warehouse.id)))
        self.assertFalse(scope.allows(warehouse_id=self.other_warehouse.id))

    def test_explicit_role_is_not_changed_by_other_role_permission_markers(self):
        user = get_user_model().objects.create_user(username="scope-extra-permission")
        UserRoleScope.objects.create(
            user=user,
            role=UserRoleScope.Role.WAREHOUSE_OPERATOR,
            warehouse=self.warehouse,
        )
        self.grant(user, "tasking", "taskconfirm_as_wh_manager")

        scope = AccessScope.for_user(user)

        self.assertTrue(scope.is_valid)
        self.assertEqual(scope.roles, {UserRoleScope.Role.WAREHOUSE_OPERATOR})
        self.assertEqual(scope.warehouse_ids, {self.warehouse.id})

    def test_matching_role_group_is_valid_but_conflicting_group_fails_closed(self):
        user = get_user_model().objects.create_user(username="scope-group-check")
        UserRoleScope.objects.create(
            user=user,
            role=UserRoleScope.Role.WAREHOUSE_OPERATOR,
            warehouse=self.warehouse,
        )
        matching_group = Group.objects.create(
            name=role_group_name(UserRoleScope.Role.WAREHOUSE_OPERATOR)
        )
        conflicting_group = Group.objects.create(
            name=role_group_name(UserRoleScope.Role.WAREHOUSE_MANAGER)
        )
        user.groups.add(matching_group)

        self.assertTrue(AccessScope.for_user(user).is_valid)

        user.groups.add(conflicting_group)
        scope = AccessScope.for_user(user)
        self.assertFalse(scope.is_valid)
        self.assertEqual(scope.denial_reason, "role_scope_and_group_conflict")

    def test_profile_capability_can_change_without_expanding_explicit_role(self):
        user = get_user_model().objects.create_user(username="scope-profile-capability")
        UserRoleScope.objects.create(
            user=user,
            role=UserRoleScope.Role.WAREHOUSE_OPERATOR,
            warehouse=self.warehouse,
        )
        self.grant(user, "products", "add_product")
        self.grant(user, "products", "manage_all_owner_products")
        self.grant(user, "tasking", "taskconfirm_as_wh_manager")
        request = APIRequestFactory().get(reverse("auth_profile"))
        force_authenticate(request, user=user)

        response = profile_view(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data["user"]["roles"],
            [UserRoleScope.Role.WAREHOUSE_OPERATOR],
        )
        self.assertTrue(response.data["capabilities"]["can_import_products"])

    def test_profile_compatibility_ids_are_derived_from_explicit_scope(self):
        user = get_user_model().objects.create_user(
            username="scope-profile-derived",
            owner=self.other_owner,
            warehouse=self.other_warehouse,
        )
        UserRoleScope.objects.create(
            user=user,
            role=UserRoleScope.Role.OWNER_SALESPERSON,
            owner=self.owner,
        )
        request = APIRequestFactory().get(reverse("auth_profile"))
        force_authenticate(request, user=user)

        response = profile_view(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["user"]["owner_id"], self.owner.id)
        self.assertIsNone(response.data["user"]["warehouse_id"])
        self.assertEqual(response.data["user"]["scopes"]["owner_ids"], [self.owner.id])

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
            scope.filter_queryset(self.target_queryset()).values_list(
                "username", flat=True
            )
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
            scope.filter_queryset(self.target_queryset()).values_list(
                "username", flat=True
            )
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

    def test_conflicting_explicit_rows_fail_closed_even_if_validation_was_bypassed(
        self,
    ):
        user = get_user_model().objects.create_user(
            username="scope-conflicting-explicit"
        )
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
                name__in=[
                    template.group_name for template in ROLE_GROUP_TEMPLATES.values()
                ]
            ).exists()
        )
        self.assertIn("[dry-run]", output.getvalue())

    def test_command_creates_all_groups_with_template_permissions_and_is_idempotent(
        self,
    ):
        call_command("sync_wms_role_groups", stdout=StringIO())
        call_command("sync_wms_role_groups", stdout=StringIO())

        self.assertEqual(
            Group.objects.filter(
                name__in=[
                    template.group_name for template in ROLE_GROUP_TEMPLATES.values()
                ]
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

    def test_default_preserves_removed_permissions_and_ensure_defaults_restores_them(
        self,
    ):
        call_command("sync_wms_role_groups", stdout=StringIO())
        template = ROLE_GROUP_TEMPLATES[UserRoleScope.Role.WAREHOUSE_OPERATOR]
        group = Group.objects.get(name=template.group_name)
        app_label, codename = template.permissions[0].split(".", 1)
        default_permission = Permission.objects.get(
            content_type__app_label=app_label,
            codename=codename,
        )
        group.permissions.remove(default_permission)

        call_command("sync_wms_role_groups", stdout=StringIO())
        self.assertFalse(group.permissions.filter(pk=default_permission.pk).exists())

        call_command(
            "sync_wms_role_groups",
            ensure_defaults=True,
            stdout=StringIO(),
        )
        self.assertTrue(group.permissions.filter(pk=default_permission.pk).exists())

    def test_ensure_defaults_and_prune_are_mutually_exclusive(self):
        with self.assertRaisesMessage(CommandError, "不能同时使用"):
            call_command(
                "sync_wms_role_groups",
                ensure_defaults=True,
                prune=True,
                stdout=StringIO(),
            )


class SyncWmsUserRoleMembershipsCommandTests(TestCase):
    def setUp(self):
        call_command("sync_wms_role_groups", stdout=StringIO())
        self.owner = Owner.objects.create(name="Command Owner", code="CMDOWNER")
        self.warehouse = Warehouse.objects.create(code="CMDWH", name="Command WH")

    def test_dry_run_is_read_only_and_apply_is_idempotent_and_audited(self):
        user = get_user_model().objects.create_user(username="command-member")
        UserRoleScope.objects.create(
            user=user,
            role=UserRoleScope.Role.OWNER_MANAGER,
            owner=self.owner,
        )
        output = StringIO()

        call_command(
            "sync_wms_user_role_memberships", dry_run=True, stdout=output
        )
        self.assertFalse(user.groups.filter(name__in=ROLE_GROUP_NAMES).exists())
        self.assertIn("未写入", output.getvalue())

        call_command("sync_wms_user_role_memberships", stdout=StringIO())
        self.assertTrue(
            user.groups.filter(
                name=role_group_name(UserRoleScope.Role.OWNER_MANAGER)
            ).exists()
        )
        self.assertTrue(
            AuditEvent.objects.filter(
                action="USER_ROLE_GROUP_SYNC", object_id=str(user.pk)
            ).exists()
        )
        event_count = AuditEvent.objects.filter(action="USER_ROLE_GROUP_SYNC").count()

        call_command("sync_wms_user_role_memberships", stdout=StringIO())
        self.assertEqual(
            AuditEvent.objects.filter(action="USER_ROLE_GROUP_SYNC").count(),
            event_count,
        )

    def test_unscoped_user_loses_only_recognized_role_group(self):
        user = get_user_model().objects.create_user(username="command-unscoped")
        role_group = Group.objects.get(
            name=role_group_name(UserRoleScope.Role.WAREHOUSE_OPERATOR)
        )
        auxiliary = Group.objects.create(name="Command Auxiliary")
        user.groups.add(role_group, auxiliary)

        call_command("sync_wms_user_role_memberships", stdout=StringIO())

        self.assertFalse(user.groups.filter(pk=role_group.pk).exists())
        self.assertTrue(user.groups.filter(pk=auxiliary.pk).exists())

    def test_invalid_explicit_roles_abort_without_partial_membership_changes(self):
        valid_user = get_user_model().objects.create_user(username="command-valid")
        invalid_user = get_user_model().objects.create_user(username="command-invalid")
        UserRoleScope.objects.create(
            user=valid_user,
            role=UserRoleScope.Role.WAREHOUSE_OPERATOR,
            warehouse=self.warehouse,
        )
        UserRoleScope.objects.bulk_create(
            [
                UserRoleScope(
                    user=invalid_user,
                    role=UserRoleScope.Role.WAREHOUSE_OPERATOR,
                    warehouse=self.warehouse,
                ),
                UserRoleScope(
                    user=invalid_user,
                    role=UserRoleScope.Role.OWNER_MANAGER,
                    owner=self.owner,
                ),
            ]
        )

        with self.assertRaisesMessage(CommandError, "校验失败"):
            call_command("sync_wms_user_role_memberships", stdout=StringIO())

        self.assertFalse(valid_user.groups.filter(name__in=ROLE_GROUP_NAMES).exists())


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
        group_conflict_user = get_user_model().objects.create_user(
            username="audit-group-conflict-user"
        )
        UserRoleScope.objects.create(
            user=group_conflict_user,
            role=UserRoleScope.Role.OWNER_MANAGER,
            owner=owner,
        )
        conflicting_group = Group.objects.create(
            name=role_group_name(UserRoleScope.Role.WAREHOUSE_MANAGER)
        )
        group_conflict_user.groups.add(conflicting_group)
        scope_count = UserRoleScope.objects.count()
        group_count = Group.objects.count()
        output = StringIO()

        call_command("audit_wms_role_scopes", format="csv", stdout=output)

        csv_output = output.getvalue()
        self.assertIn("audit-mixed-user", csv_output)
        self.assertIn("LEGACY_OWNER_WAREHOUSE_MIXED", csv_output)
        self.assertIn("MISSING_EXPLICIT_SCOPE", csv_output)
        self.assertIn("audit-conflict-user", csv_output)
        self.assertIn("LEGACY_PERMISSION_ROLE_CONFLICT", csv_output)
        self.assertTrue(AccessScope.for_user(conflict_user).is_valid)
        self.assertIn("audit-group-conflict-user", csv_output)
        self.assertIn("ROLE_GROUP_CONFLICT", csv_output)
        self.assertIn("group_roles", csv_output)
        self.assertIn("legacy_inferred_roles", csv_output)
        self.assertEqual(UserRoleScope.objects.count(), scope_count)
        self.assertEqual(Group.objects.count(), group_count)
