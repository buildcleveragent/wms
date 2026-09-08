"""Exercise URL boundaries through a real local HTTP server."""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from scripts.putaway_load_test import JsonClient, validate_base_url

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://localhost",
        "https://user:secret@example.com",
        "http://localhost/api",
        "http://localhost?token=x",
        "http://localhost#x",
        "http://localhost:bad",
        "http://localhost\\@example.com",
        " http://localhost",
    ],
)
def test_rejects_non_origin_urls_before_network(url):
    with pytest.raises(ValueError):
        validate_base_url(url)


def test_http_success_and_redirects_never_forward_authorization():
    paths = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            paths.append(self.path)
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "/private")
            else:
                self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"ok": true}')

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = JsonClient(f"http://127.0.0.1:{server.server_port}", timeout=2)
        client.token = "synthetic-test-token"
        success, payload = client.request("GET", "/ok", operation="probe")
        assert success.ok and payload == {"ok": True}
        failure, _ = client.request("GET", "/redirect", operation="probe")
        assert not failure.ok and failure.status == 302
        assert paths == ["/ok", "/redirect"]
        with pytest.raises(ValueError):
            client.request("GET", "//example.com", operation="probe")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
