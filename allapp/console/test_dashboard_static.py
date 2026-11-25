"""Guard the offline dashboard asset in the ordinary backend CI job."""

import hashlib
from pathlib import Path

from django.contrib.staticfiles import finders
from django.template.loader import render_to_string
from django.templatetags.static import static
from django.test import SimpleTestCase


class DashboardStaticTests(SimpleTestCase):
    def test_page_loads_verified_echarts_bundle(self):
        name = "vendor/echarts/6.1.0/echarts.common.min.js"
        html = render_to_string("console/home.html")
        self.assertInHTML(f'<script defer src="{static(name)}"></script>', html, count=1)
        source = finders.find(name)
        self.assertIsNotNone(source)
        with Path(source).open("rb") as asset:
            self.assertEqual(
                hashlib.file_digest(asset, "sha256").hexdigest(),
                "61f13280aba3161f77fc801bf5f9c786f55f10432153a0683fc5cc0700b013d5",
            )
