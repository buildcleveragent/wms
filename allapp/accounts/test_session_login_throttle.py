from django.core.cache import caches
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, override_settings

from .login_middleware import SessionLoginThrottleMiddleware


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
    LOGIN_THROTTLE_CACHE_ALIAS="default",
    LOGIN_USERNAME_IP_RATE="2/min",
    LOGIN_IP_RATE="10/min",
)
class SessionLoginThrottleTests(SimpleTestCase):
    def setUp(self):
        caches["default"].clear()
        self.factory = RequestFactory()
        self.middleware = SessionLoginThrottleMiddleware(lambda request: HttpResponse("login"))

    def test_alternate_session_login_routes_share_a_single_limit(self):
        responses = [
            self.middleware(self.factory.post(path, {"username": "user"}))
            for path in ("/accounts/login/", "/api-auth/login/", "/admin/login/")
        ]
        self.assertEqual([response.status_code for response in responses], [200, 200, 429])
        self.assertGreater(int(responses[-1]["Retry-After"]), 0)

    def test_login_page_get_and_unrelated_posts_are_not_throttled(self):
        for _ in range(12):
            self.assertEqual(self.middleware(self.factory.get("/admin/login/")).status_code, 200)
            self.assertEqual(self.middleware(self.factory.post("/business/")).status_code, 200)
