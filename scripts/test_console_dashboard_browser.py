"""Exercise rendered Web templates and collected static files without a database."""

import os
import subprocess
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest
from django.core.management import call_command
from django.template import engines
from django.template.loader import render_to_string
from django.urls import reverse


@pytest.fixture
def static_origin(tmp_path, settings):
    settings.STATIC_ROOT = tmp_path / "static"
    call_command("collectstatic", interactive=False, verbosity=0)
    handler = partial(SimpleHTTPRequestHandler, directory=tmp_path)
    with ThreadingHTTPServer(("127.0.0.1", 0), handler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}"
        finally:
            server.shutdown()
            thread.join()


def test_console_dashboard_without_public_network(tmp_path, static_origin):
    (tmp_path / "index.html").write_text(render_to_string("console/home.html"), encoding="utf-8")
    subprocess.run(
        [
            "node",
            str(Path(__file__).with_name("console-dashboard-browser.mjs")),
            static_origin,
            reverse("console:dashboard_summary"),
            os.environ.get("WMS_BROWSER_ARTIFACTS", str(tmp_path / "evidence")),
        ],
        check=True,
        timeout=180,
    )


def test_legacy_account_menu_is_optional(tmp_path, static_origin):
    page = "registration/password_change_done.html"
    (tmp_path / "no-menu.html").write_text(render_to_string(page), encoding="utf-8")
    account = '<a href="/admin/accounts/">账号</a>'
    existing_group = '<a href="/admin/auth/group/" class="existing-group">组</a>'
    for variant, sidebar in (
        ("with-menu", f'<div class="dropdown-menu">{account}</div>'),
        ("existing-group", f'<div class="dropdown-menu">{account}{existing_group}</div>'),
        ("link-only", account),
    ):
        template = engines["django"].from_string(
            '{% extends "registration/password_change_done.html" %}{% block sidebar %}'
            + sidebar
            + "{% endblock %}"
        )
        (tmp_path / f"{variant}.html").write_text(template.render(), encoding="utf-8")
    subprocess.run(
        [
            "node",
            str(Path(__file__).with_name("legacy-account-menu-browser.mjs")),
            static_origin,
            os.environ.get("WMS_BROWSER_ARTIFACTS", str(tmp_path / "evidence")),
        ],
        check=True,
        timeout=60,
    )
