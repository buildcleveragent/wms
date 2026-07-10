import tempfile
from pathlib import Path

import pytest
from django.test import SimpleTestCase, override_settings

pytestmark = pytest.mark.api


class ApiDownloadTests(SimpleTestCase):
    def test_download_page_renders_timestamped_apk_link(self):
        response = self.client.get("/api/v1/download/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "/api/v1/bv2/bv2_")
        self.assertContains(response, ".apk")

    def test_apk_download_returns_file_response_with_headers(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            media_dir = base_dir / "media"
            media_dir.mkdir()
            apk = media_dir / "bv2.apk"
            apk.write_bytes(b"fake-apk")

            with override_settings(BASE_DIR=base_dir):
                response = self.client.get("/api/v1/bv2/custom-client.apk")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response["Content-Type"], "application/vnd.android.package-archive"
        )
        self.assertEqual(response["Content-Length"], "8")
        self.assertIn("custom-client.apk", response["Content-Disposition"])
        self.assertEqual(b"".join(response.streaming_content), b"fake-apk")

    def test_apk_download_returns_404_when_file_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with override_settings(BASE_DIR=Path(tmpdir)):
                response = self.client.get("/api/v1/bv2/missing.apk")

        self.assertEqual(response.status_code, 404)
