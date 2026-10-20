import os
import runpy
from pathlib import Path
from unittest.mock import patch

from django.core.exceptions import DisallowedHost, ImproperlyConfigured
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, override_settings

from wmsmaster.security import AndroidApiSecurityMiddleware, http_api_authority


@override_settings(
    DEBUG=False,
    ALLOWED_HOSTS=["8.148.198.200", "other.example"],
    SECURE_SSL_REDIRECT=True,
    SECURE_HSTS_SECONDS=31536000,
    SECURE_REDIRECT_EXEMPT=[r"^healthz/live$", r"^healthz/ready$"],
    WMS_ANDROID_HTTP_API_ORIGIN="http://8.148.198.200:8080",
    SESSION_COOKIE_SECURE=True,
    CSRF_COOKIE_SECURE=True,
)
class AndroidHttpPolicyTests(SimpleTestCase):
    def response(self, path, host="8.148.198.200:8080", secure=False, **extra):
        request = RequestFactory().get(path, HTTP_HOST=host, secure=secure, **extra)
        return AndroidApiSecurityMiddleware(lambda request: HttpResponse("ok"))(request)

    def test_exact_http_origin_api_remains_http_with_security_headers(self):
        response = self.response("/api/token/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertNotIn("Strict-Transport-Security", response)

    def test_exception_disabled_by_default(self):
        with override_settings(WMS_ANDROID_HTTP_API_ORIGIN=""):
            self.assertEqual(self.response("/api/token/").status_code, 301)

    def test_non_api_paths_and_other_origins_still_redirect(self):
        for path in ["/admin/", "/", "/media/image.jpg", "/api", "/api-other/"]:
            with self.subTest(path=path):
                self.assertEqual(self.response(path).status_code, 301)
        for host in ["8.148.198.200", "8.148.198.200:8081", "other.example:8080"]:
            with self.subTest(host=host):
                response = self.response("/api/token/?next=test", host=host)
                self.assertEqual(response.status_code, 301)
                self.assertEqual(response["Location"], f"https://{host}/api/token/?next=test")

    def test_invalid_host_and_forged_forwarded_host_do_not_bypass(self):
        with self.assertRaises(DisallowedHost):
            self.response("/api/token/", host="evil.example:8080")
        with override_settings(USE_X_FORWARDED_HOST=False):
            response = self.response(
                "/api/token/",
                host="other.example:8080",
                HTTP_X_FORWARDED_HOST="8.148.198.200:8080",
            )
            self.assertEqual(response.status_code, 301)

    def test_https_and_existing_health_exemption_preserved(self):
        response = self.response("/api/token/", secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("max-age=31536000", response["Strict-Transport-Security"])
        self.assertEqual(self.response("/healthz/live").status_code, 200)

    def test_malformed_config_fails_closed(self):
        for value in [
            "*",
            "http://*",
            "https://8.148.198.200:8080",
            "http://host/api/",
            "http://user:pass@host",
            "http://host?x=1",
            "http://host#x",
            "http://host:99999",
            "http://host:abc",
            "http://host:0",
        ]:
            with self.subTest(value=value), self.assertRaises(ImproperlyConfigured):
                http_api_authority(value)
        self.assertEqual(http_api_authority(""), "")
        self.assertEqual(http_api_authority("http://8.148.198.200:8080/"), "8.148.198.200:8080")

    def test_api_exception_does_not_bypass_api_authentication(self):
        response = self.client.get("/api/outbound/orders/", HTTP_HOST="8.148.198.200:8080")
        self.assertEqual(response.status_code, 401)

    def test_public_request_produces_public_product_image_url(self):
        request = RequestFactory().get("/api/products/", HTTP_HOST="8.148.198.200:8080")
        self.assertEqual(
            request.build_absolute_uri("/media/products/example.jpg"),
            "http://8.148.198.200:8080/media/products/example.jpg",
        )


class ProductionAndroidConfigurationTests(SimpleTestCase):
    def test_actual_production_settings_keep_cookie_and_admin_security(self):
        # Evaluate settings without loading real environment files or connecting to a DB.
        env = {
            "APP_ENV": "production",
            "DEBUG": "False",
            "SECRET_KEY": "test-only",
            "ALLOWED_HOSTS": "8.148.198.200",
            "DB_NAME": "wms_db_test",
            "WMS_ANDROID_HTTP_API_ORIGIN": "http://8.148.198.200:8080",
        }
        path = Path(__file__).resolve().parents[2] / "wmsmaster" / "settings.py"
        with (
            patch.dict(os.environ, env, clear=True),
            patch("wmsmaster.environment.load_environment"),
        ):
            config = runpy.run_path(str(path))
        self.assertFalse(config["DEBUG"])
        self.assertTrue(config["SECURE_SSL_REDIRECT"])
        self.assertTrue(config["SESSION_COOKIE_SECURE"])
        self.assertTrue(config["CSRF_COOKIE_SECURE"])
        self.assertFalse(config["CORS_ALLOW_ALL_ORIGINS"])
        self.assertEqual(config["ALLOWED_HOSTS"], ["8.148.198.200"])
        self.assertIn("wmsmaster.security.AndroidApiSecurityMiddleware", config["MIDDLEWARE"])
