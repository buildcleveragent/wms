"""Real Django login, authorization and console navigation on touch and keyboard."""

import json
import os
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings
from django.urls import reverse

from allapp.console.test_menu import create_navigation_accounts

ROOT = Path(__file__).resolve().parents[1]


@override_settings(ALLOWED_HOSTS=["localhost", "127.0.0.1", "testserver"])
class ConsoleNavigationBrowserTests(StaticLiveServerTestCase):
    def test_real_role_menus_on_touch_and_keyboard(self):
        create_navigation_accounts()
        with TemporaryDirectory(prefix="console-nav-browser-") as directory:
            artifacts = Path(os.environ.get("WMS_BROWSER_ARTIFACTS", directory)) / "menu"
            artifacts.mkdir(parents=True, exist_ok=True)
            config = {
                "origin": self.live_server_url,
                "artifacts": str(artifacts),
                "home": reverse("dashboard_home"),
                "login": reverse("admin:login"),
                "stock": reverse("admin:inventory_inventorydetail_changelist"),
                "inbound": reverse("admin:inbound_inboundorder_changelist"),
            }
            result = subprocess.run(
                ["node", str(ROOT / "scripts/console-navigation-browser.mjs")],
                input=json.dumps(config),
                text=True,
                capture_output=True,
                timeout=360,
            )
            (artifacts / "browser.log").write_text(result.stderr + result.stdout)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
