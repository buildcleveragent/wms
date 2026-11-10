from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.management import call_command
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from allapp.accounts.models import AuditEvent, UserRoleScope
from allapp.locations.models import Warehouse
from allapp.reports.boss_automation import (
    RUN_KEY,
    automation_status,
    configuration,
    heartbeat,
    run_scheduled_check,
)
from allapp.reports.models import BossCheckState


class BossAutomationTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser(
            username="automation-admin", password="x"
        )
        self.boss = get_user_model().objects.create_user(username="automation-boss")
        warehouse = Warehouse.objects.create(code="AUT-W", name="Warehouse")
        UserRoleScope.objects.create(user=self.boss, role="warehouse_boss", warehouse=warehouse)
        self.boss.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="reports", codename="view_boss_dashboard"
            )
        )
        self.client = APIClient()
        self.client.force_authenticate(self.admin)
        self.url = "/api/reports/boss/automation-settings/"

    def configure(self, **data):
        response = self.client.patch(self.url, data, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def test_default_off_and_disabled_worker_does_not_scan_business(self):
        self.assertEqual(configuration()["enabled"], False)
        with (
            CaptureQueriesContext(connection) as queries,
            patch("allapp.reports.boss_attention.check_deadlines") as scan,
        ):
            heartbeat()
            self.assertEqual(run_scheduled_check(), {"skipped": "disabled"})
        scan.assert_not_called()
        business_tables = [
            "inbound_inboundorder",
            "outbound_outboundorder",
            "tasking_wmstask",
            "billing_billingevent",
        ]
        self.assertFalse(
            any(table in q["sql"].lower() for table in business_tables for q in queries)
        )
        self.assertEqual(automation_status()["state"], "disabled")

    def test_permissions_validation_and_audit(self):
        self.client.force_authenticate(self.boss)
        self.assertFalse(self.client.get(self.url).data["can_manage"])
        self.assertEqual(
            self.client.patch(self.url, {"enabled": True}, format="json").status_code, 403
        )
        self.client.force_authenticate(self.admin)
        for value in [0, 1441, 1.5, True, "5", None]:
            self.assertEqual(
                self.client.patch(self.url, {"interval_minutes": value}, format="json").status_code,
                400,
            )
        self.assertEqual(
            self.client.patch(self.url, {"enabled": "false"}, format="json").status_code, 400
        )
        response = self.configure(enabled=True, interval_minutes=10)
        self.assertTrue(response["can_manage"])
        self.assertEqual(response["interval_minutes"], 10)
        self.assertEqual(
            AuditEvent.objects.filter(action="boss.automation.configure", actor=self.admin).count(),
            1,
        )
        self.client.force_authenticate(self.boss)
        self.assertEqual(self.client.get(self.url).data["interval_minutes"], 10)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(self.url).status_code, 401)

    def test_enabling_frequency_changes_and_restart_do_not_catch_up(self):
        self.configure(enabled=True, interval_minutes=10)
        heartbeat()
        with patch(
            "allapp.reports.boss_attention.check_deadlines", return_value={"count": 2}
        ) as scan:
            self.assertEqual(run_scheduled_check(), {"count": 2})
            self.assertEqual(run_scheduled_check(), {"skipped": "not_due"})
            finished = BossCheckState.objects.get(key=RUN_KEY).checked_at
            with patch(
                "allapp.reports.boss_automation.timezone.now",
                return_value=finished + timedelta(minutes=8),
            ):
                heartbeat()
                self.assertEqual(automation_status()["state"], "healthy")
                self.assertEqual(run_scheduled_check(), {"skipped": "not_due"})
            self.configure(interval_minutes=1)
            BossCheckState.objects.filter(key=RUN_KEY).update(
                checked_at=timezone.now() - timedelta(days=1)
            )
            self.assertEqual(run_scheduled_check(), {"count": 2})
            self.assertEqual(run_scheduled_check(), {"skipped": "not_due"})
            self.assertEqual(scan.call_count, 2)

    def test_failure_retries_at_configured_interval_and_marks_health(self):
        self.configure(enabled=True, interval_minutes=5)
        heartbeat()
        with patch(
            "allapp.reports.boss_attention.check_deadlines",
            side_effect=RuntimeError("source failure"),
        ) as scan:
            with self.assertRaises(RuntimeError):
                run_scheduled_check()
            self.assertEqual(automation_status()["state"], "failed")
            self.assertEqual(run_scheduled_check(), {"skipped": "not_due"})
            self.assertEqual(scan.call_count, 1)
        self.assertTrue(BossCheckState.objects.get(key=RUN_KEY).failed)

    def test_disable_during_cycle_finishes_current_check_and_stops_next(self):
        self.configure(enabled=True)

        def scan(**kwargs):
            disabled = self.configure(enabled=False)
            self.assertEqual(disabled["state"], "disabled")
            self.assertTrue(disabled["running"])
            return {"completed": 1}

        with patch("allapp.reports.boss_attention.check_deadlines", side_effect=scan) as mocked:
            self.assertEqual(run_scheduled_check(), {"completed": 1})
            self.assertEqual(run_scheduled_check(), {"skipped": "disabled"})
            self.assertEqual(mocked.call_count, 1)
        self.assertIsNotNone(BossCheckState.objects.get(key=RUN_KEY).checked_at)

    def test_worker_health_and_frequency_based_delay(self):
        self.configure(enabled=True, interval_minutes=30)
        self.assertEqual(automation_status()["state"], "worker_unavailable")
        heartbeat()
        self.assertEqual(automation_status()["state"], "waiting")
        now = timezone.now()
        BossCheckState.objects.update_or_create(key=RUN_KEY, defaults={"checked_at": now})
        for minutes, expected in [(20, "healthy"), (31, "healthy"), (33, "delayed")]:
            with patch(
                "allapp.reports.boss_automation.timezone.now",
                return_value=now + timedelta(minutes=minutes),
            ):
                heartbeat()
                self.assertEqual(automation_status()["state"], expected)

    def test_legacy_command_respects_switch_and_force_is_explicit(self):
        with patch("allapp.reports.boss_attention.check_deadlines", return_value={}) as scan:
            call_command("check_boss_deadlines", stdout=StringIO())
            scan.assert_not_called()
            call_command("check_boss_deadlines", force=True, stdout=StringIO())
            scan.assert_called_once_with(lock_held=True)

    def test_other_connection_lock_prevents_duplicate_scan(self):
        self.configure(enabled=True)
        other = connection.copy()
        key = "boss-deadlines:" + connection.settings_dict["NAME"]
        try:
            with other.cursor() as cursor:
                cursor.execute("SELECT GET_LOCK(%s, 0)", [key])
                self.assertEqual(cursor.fetchone()[0], 1)
            with patch("allapp.reports.boss_attention.check_deadlines") as scan:
                self.assertEqual(run_scheduled_check(), {"skipped": "busy"})
                scan.assert_not_called()
        finally:
            with other.cursor() as cursor:
                cursor.execute("SELECT RELEASE_LOCK(%s)", [key])
            other.close()

    def test_interrupted_check_is_not_reported_as_healthy(self):
        self.configure(enabled=True, interval_minutes=10)
        heartbeat()
        now = timezone.now()
        BossCheckState.objects.create(
            key=RUN_KEY,
            checked_at=now - timedelta(minutes=1),
            attempted_at=now,
            failed=False,
        )
        self.assertEqual(automation_status()["state"], "failed")

    def test_generic_settings_admin_cannot_bypass_superuser_permission(self):
        from django.contrib.admin.sites import AdminSite
        from django.core.exceptions import PermissionDenied
        from django.test import RequestFactory

        from allapp.core.admin import SystemSettingAdmin
        from allapp.core.models import SystemSetting

        self.configure(enabled=False)
        row = SystemSetting.objects.get(namespace="boss", key="automation_settings")
        self.boss.is_staff = True
        self.boss.save(update_fields=["is_staff"])
        self.boss.user_permissions.add(
            *Permission.objects.filter(
                content_type__app_label="core", content_type__model="systemsetting"
            )
        )
        request = RequestFactory().post("/admin/core/systemsetting/")
        request.user = self.boss
        admin = SystemSettingAdmin(SystemSetting, AdminSite())
        self.assertFalse(admin.get_queryset(request).filter(pk=row.pk).exists())
        self.assertFalse(admin.has_change_permission(request, row))
        self.assertFalse(admin.has_delete_permission(request, row))
        with self.assertRaises(PermissionDenied):
            admin.save_model(request, row, None, True)
        with self.assertRaises(PermissionDenied):
            admin.save_model(
                request, SystemSetting(namespace="boss", key="automation_settings"), None, False
            )
        with self.assertRaises(PermissionDenied):
            admin.delete_queryset(request, SystemSetting.objects.filter(pk=row.pk))
        ordinary = SystemSetting(namespace="other", key="unrelated", name="other", value="test")
        admin.save_model(request, ordinary, None, False)
        self.assertIsNotNone(ordinary.pk)
        request.user = self.admin
        self.assertTrue(admin.has_change_permission(request, row))
