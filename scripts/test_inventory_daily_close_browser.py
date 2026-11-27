"""Exercise the scoped daily-close workflow through Django and Chromium."""

import json
import os
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings

from allapp.console.test_daily_close import DailyCloseFixture, route


@override_settings(ALLOWED_HOSTS=["localhost", "127.0.0.1", "testserver"])
class DailyCloseBrowserTests(DailyCloseFixture, StaticLiveServerTestCase):
    def test_review_and_sign_across_mobile_and_desktop(self):
        self.setUpTestData()
        sessions = {}
        for role in ("manager", "finance", "owner_finance"):
            self.client.force_login(getattr(self, role))
            sessions[role] = self.client.cookies[settings.SESSION_COOKIE_NAME].value
            self.client.cookies.clear()
        with TemporaryDirectory(prefix="inventory-daily-close-") as directory:
            artifacts = Path(os.getenv("WMS_BROWSER_ARTIFACTS", directory)) / "daily-close"
            artifacts.mkdir(parents=True, exist_ok=True)
            config = {
                "origin": self.live_server_url,
                "list": route("list"),
                "detail": route("detail", self.close.pk),
                "externalDetail": route("detail", self.other_close.pk),
                "artifacts": str(artifacts),
                "cookie": settings.SESSION_COOKIE_NAME,
                "sessions": sessions,
            }
            result = subprocess.run(
                ["node", str(Path(__file__).with_name("inventory-daily-close-browser.mjs"))],
                input=json.dumps(config),
                text=True,
                capture_output=True,
                timeout=120,
            )
            (artifacts / "browser.log").write_text(result.stderr + result.stdout)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
