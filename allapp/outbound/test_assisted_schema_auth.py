import csv
import io
import json
import uuid

from django.conf import settings
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from allapp.accounts.models import SystemLog, UserRoleScope
from allapp.baseinfo.models import Customer, Owner
from allapp.locations.models import Warehouse
from allapp.outbound.models import OutboundOrder
from wmsmaster.settings import _validated_choice
from wmsmaster.views import profile_view


def permission(app_label, codename):
    return Permission.objects.get(
        content_type__app_label=app_label,
        codename=codename,
    )


class AssistedOutboundSchemaTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="schema-salesperson", password="x"
        )
        self.owner = Owner.objects.create(name="Schema Owner", code="SCHOWN")
        self.warehouse = Warehouse.objects.create(
            code="SCHWH", name="Schema Warehouse"
        )
        self.customer = Customer.objects.create(
            owner=self.owner,
            salesperson=self.user,
            code="SCHCUST",
            name="Schema Customer",
        )

    def test_owner_opt_in_defaults_false_and_is_exposed_in_admin(self):
        self.assertFalse(self.owner.allow_warehouse_assisted_outbound)

        model_admin = admin.site._registry[Owner]
        self.assertIn("allow_warehouse_assisted_outbound", model_admin.fields)
        self.assertIn(
            "allow_warehouse_assisted_outbound", model_admin.list_display
        )
        self.assertIn("allow_warehouse_assisted_outbound", model_admin.list_filter)

    def test_order_defaults_standard_and_assistance_request_is_unique(self):
        request_id = uuid.uuid4()
        first = OutboundOrder.objects.create(
            owner=self.owner,
            customer=self.customer,
            warehouse=self.warehouse,
            order_no="SCHEMA-OUT-1",
            assistance_request_id=request_id,
        )

        self.assertEqual(first.processing_mode, OutboundOrder.ProcessingMode.STANDARD)
        self.assertIsNone(first.assisted_by_id)
        self.assertIsNone(first.assisted_at)
        self.assertEqual(first.assistance_reason, "")

        with self.assertRaises(ValidationError) as exc:
            OutboundOrder.objects.create(
                owner=self.owner,
                customer=self.customer,
                warehouse=self.warehouse,
                order_no="SCHEMA-OUT-2",
                assistance_request_id=request_id,
            )

        self.assertIn("assistance_request_id", exc.exception.message_dict)

    def test_new_custom_permissions_are_installed(self):
        self.assertEqual(
            permission("outbound", "process_warehouse_assisted_outbound").codename,
            "process_warehouse_assisted_outbound",
        )
        self.assertEqual(
            permission("outbound", "view_all_outbound_orders").codename,
            "view_all_outbound_orders",
        )


class AssistedOutboundProfileTests(TestCase):
    def setUp(self):
        self.owner = Owner.objects.create(name="Profile Owner", code="PROFOWN")
        self.warehouse = Warehouse.objects.create(
            code="PROFWH", name="Profile Warehouse"
        )
        self.assisted_permission = permission(
            "outbound", "process_warehouse_assisted_outbound"
        )
        self.task_permission = permission("tasking", "claim_task_as_wh_operator")
        self.factory = APIRequestFactory()

    def _profile(self, user):
        request = self.factory.get("/api/auth/profile/")
        force_authenticate(request, user=user)
        return profile_view(request)

    def test_profile_returns_bindings_permissions_and_enabled_capability(self):
        user = get_user_model().objects.create_user(
            username="assisted-operator",
            password="x",
            warehouse=self.warehouse,
        )
        UserRoleScope.objects.create(
            user=user,
            role=UserRoleScope.Role.WAREHOUSE_OPERATOR,
            warehouse=self.warehouse,
        )
        user.user_permissions.add(self.assisted_permission, self.task_permission)

        response = self._profile(user)

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.data["user"]["owner_id"])
        self.assertEqual(response.data["user"]["warehouse_id"], self.warehouse.id)
        self.assertIn(
            "outbound.process_warehouse_assisted_outbound", response.data["perms"]
        )
        self.assertIn("tasking.claim_task_as_wh_operator", response.data["perms"])
        self.assertTrue(
            response.data["capabilities"][
                "can_process_warehouse_assisted_outbound"
            ]
        )

    def test_capability_is_fail_closed_for_mixed_binding_or_missing_permission(self):
        mixed_user = get_user_model().objects.create_user(
            username="mixed-operator",
            password="x",
            owner=self.owner,
            warehouse=self.warehouse,
        )
        mixed_user.user_permissions.add(self.assisted_permission, self.task_permission)
        missing_task_user = get_user_model().objects.create_user(
            username="missing-task-permission",
            password="x",
            warehouse=self.warehouse,
        )
        missing_task_user.user_permissions.add(self.assisted_permission)

        mixed_response = self._profile(mixed_user)
        missing_response = self._profile(missing_task_user)

        self.assertFalse(
            mixed_response.data["capabilities"][
                "can_process_warehouse_assisted_outbound"
            ]
        )
        self.assertFalse(
            missing_response.data["capabilities"][
                "can_process_warehouse_assisted_outbound"
            ]
        )


class OutboundAccessAuditCommandTests(TestCase):
    def setUp(self):
        self.warehouse = Warehouse.objects.create(
            code="AUDWH", name="Audit Warehouse"
        )

    def test_json_audit_reports_binding_permission_and_recent_activity_risks(self):
        user = get_user_model().objects.create_user(
            username="legacy-unbound", password="x"
        )
        user.user_permissions.add(
            permission("products", "manage_all_owner_products"),
            permission("inbound", "submit_as_owner_buyers"),
        )
        SystemLog.objects.create(
            occurred_at=timezone.now(),
            username=user.username,
            log_type="OTHER",
            module="outbound",
            content="GET /api/outbound/orders/",
        )
        before_count = get_user_model().objects.count()
        stdout = io.StringIO()

        call_command("audit_outbound_access", format="json", stdout=stdout)

        payload = json.loads(stdout.getvalue())
        row = next(item for item in payload["accounts"] if item["user_id"] == user.id)
        self.assertEqual(payload["account_count"], 1)
        self.assertEqual(row["outbound_scope"], "none")
        self.assertIn("UNBOUND_ACCOUNT", row["risk_codes"])
        self.assertIn("PRODUCT_MANAGER_WITHOUT_TASK_CLAIM", row["risk_codes"])
        self.assertIn("LEGACY_INBOUND_PERMISSION_GAP", row["risk_codes"])
        self.assertIn("RECENT_ACTIVITY_WOULD_DENY", row["risk_codes"])
        self.assertEqual(
            row["legacy_permission_gaps"],
            [
                "inbound.submit_as_owner_buyers->outbound.submit_outbound_as_owner_buyers"
            ],
        )
        self.assertEqual(get_user_model().objects.count(), before_count)

    def test_csv_audit_exposes_valid_assisted_operator_capability(self):
        user = get_user_model().objects.create_user(
            username="audited-assisted",
            password="x",
            warehouse=self.warehouse,
        )
        user.user_permissions.add(
            permission("outbound", "process_warehouse_assisted_outbound"),
            permission("tasking", "claim_task_as_wh_operator"),
        )
        stdout = io.StringIO()

        call_command("audit_outbound_access", format="csv", stdout=stdout)

        rows = list(csv.DictReader(io.StringIO(stdout.getvalue())))
        row = next(item for item in rows if int(item["user_id"]) == user.id)
        self.assertEqual(row["can_process_warehouse_assisted_outbound"], "True")
        self.assertEqual(row["outbound_scope"], "warehouse_assisted_only")
        self.assertEqual(row["task_scope"], "warehouse_assisted_only")


class OutboundLegacyAuthzSettingTests(TestCase):
    def test_mode_accepts_only_shadow_or_enforce(self):
        self.assertIn(settings.OUTBOUND_LEGACY_AUTHZ_MODE, {"shadow", "enforce"})
        self.assertEqual(
            _validated_choice("OUTBOUND_LEGACY_AUTHZ_MODE", " SHADOW ", {"shadow", "enforce"}),
            "shadow",
        )
        with self.assertRaises(ImproperlyConfigured):
            _validated_choice(
                "OUTBOUND_LEGACY_AUTHZ_MODE", "disabled", {"shadow", "enforce"}
            )
