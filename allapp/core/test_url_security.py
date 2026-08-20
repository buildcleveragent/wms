from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from allapp.core.url_security import require_https_endpoint


class RequireHttpsEndpointTests(SimpleTestCase):
    def require(self, value):
        return require_https_endpoint(
            value,
            setting_name="TEST_ENDPOINT",
            allowed_hosts={"api.example.com"},
        )

    def test_accepts_and_normalizes_explicit_https_host(self):
        self.assertEqual(
            self.require(" https://api.example.com/v1/ "),
            "https://api.example.com/v1",
        )

    def test_rejects_untrusted_host_and_http(self):
        for value in ("https://evil.example/v1", "http://api.example.com/v1"):
            with self.subTest(value=value), self.assertRaises(ImproperlyConfigured):
                self.require(value)

    def test_rejects_credentials_query_fragment_and_nonstandard_port(self):
        values = (
            "https://user:secret@api.example.com/v1",
            "https://api.example.com/v1?next=evil",
            "https://api.example.com/v1#fragment",
            "https://api.example.com:8443/v1",
        )
        for value in values:
            with self.subTest(value=value), self.assertRaises(ImproperlyConfigured):
                self.require(value)
