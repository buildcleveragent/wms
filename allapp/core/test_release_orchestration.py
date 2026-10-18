import os
import subprocess
import tempfile
from pathlib import Path

from django.test import SimpleTestCase


class ReleaseOrchestrationTests(SimpleTestCase):
    def _release(self, *, fail_migration=False, fail_volumes=False):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            events = root / "events"
            programs = {
                "docker": """#!/bin/sh
echo "docker $*" >> "$TEST_EVENTS"
case "$*" in
  *'check_volume_access.py'*) [ "$TEST_FAIL_VOLUMES" = 1 ] && exit 8 ;;
  *'migrate --noinput'*) [ "$TEST_FAIL_MIGRATION" = 1 ] && exit 9 ;;
  *'ps -q scheduler'*) echo scheduler-id ;;
esac
exit 0
""",
                "systemctl": """#!/bin/sh
echo "systemctl $*" >> "$TEST_EVENTS"
case "$*" in
  'is-active --quiet '*.service) exit 3 ;;
esac
exit 0
""",
                "backup": """#!/bin/sh
echo backup >> "$TEST_EVENTS"
""",
            }
            for name, script in programs.items():
                file = root / name
                file.write_text(script)
                file.chmod(0o700)
            result = subprocess.run(
                [
                    "/bin/sh",
                    "docker/production_release.sh",
                    "example.invalid/wms@sha256:test",
                ],
                cwd=Path(__file__).resolve().parents[2],
                env={
                    **os.environ,
                    "PATH": f"{root}:/usr/bin:/bin",
                    "TEST_EVENTS": str(events),
                    "TEST_FAIL_MIGRATION": "1" if fail_migration else "0",
                    "TEST_FAIL_VOLUMES": "1" if fail_volumes else "0",
                    "WMS_DB_BACKUP_SCRIPT": str(root / "backup"),
                    "WMS_MEDIA_BACKUP_SCRIPT": str(root / "backup"),
                    "WMS_OBSERVE_SECONDS": "0",
                },
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result, events.read_text().splitlines()

    def test_payment_jobs_pause_before_migration_and_resume_after_validation(self):
        result, events = self._release()
        self.assertEqual(result.returncode, 0, result.stderr)
        pause = next(i for i, event in enumerate(events) if event.startswith("systemctl stop"))
        migrate = next(i for i, event in enumerate(events) if "migrate --noinput" in event)
        resume = next(
            i
            for i, event in enumerate(events)
            if event.startswith("systemctl start wms-sale-mini-expire.timer")
        )
        accuracy = next(i for i, event in enumerate(events) if "reconcile_data_accuracy" in event)
        self.assertLess(pause, migrate)
        self.assertLess(accuracy, resume)
        self.assertIn("systemctl start wms-sale-mini-expire.service", events)
        self.assertIn("systemctl start wms-sale-mini-reconcile.service", events)

    def test_failed_migration_keeps_writers_stopped_and_reports_recovery(self):
        result, events = self._release(fail_migration=True)
        self.assertEqual(result.returncode, 9)
        self.assertIn("restore the verified database/media backup", result.stderr)
        self.assertFalse(any(event.startswith("systemctl start") for event in events))
        self.assertIn("stop scheduler web", events[-1])

    def test_unwritable_volumes_abort_before_stopping_writers_or_migrating(self):
        result, events = self._release(fail_volumes=True)
        self.assertEqual(result.returncode, 8)
        self.assertFalse(any("systemctl stop" in event for event in events))
        self.assertFalse(any("stop scheduler web" in event for event in events))
        self.assertFalse(any("migrate --noinput" in event for event in events))
        self.assertFalse(any(event == "backup" for event in events))
