"""Real PDF content and resource-boundary checks; no mocked renderer."""

import base64
import shutil
import subprocess
from decimal import Decimal
from io import BytesIO
from unittest.mock import patch

import pytest
from django.template.loader import render_to_string
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image
from weasyprint import HTML

from allapp.reports import tests as report_fixtures
from allapp.reports.pdf_fetcher import OfflineURLFetcher

_offline_url_fetcher = OfflineURLFetcher()


def pdf_text(data):
    if not shutil.which("pdftotext"):
        raise RuntimeError("PDF acceptance requires the pdftotext test tool.")
    result = subprocess.run(
        ["pdftotext", "-", "-"], input=data, capture_output=True, timeout=20, check=True
    )
    return result.stdout.decode("utf-8")


def sample_note(rows=1):
    """Generate deterministic synthetic report data without a database."""
    return {
        "header": {
            "title": "发运单",
            "note_no": "PDF-ACCEPTANCE",
            "owner_name": "验收货主",
            "customer_name": "测试客户",
            "customer_addr": "合成测试地址",
            "remark": '<img src="https://invalid.example/secret">文字不能成为资源',
            "date": "2026-09-06",
        },
        "items": [
            {
                "sku_code": f"ROW-{index:04d}",
                "sku_name": "中文长商品名称" * 5,
                "spec": "验收规格",
                "qty": Decimal("2"),
                "uom": "件",
                "price": Decimal("9.50"),
                "amount": Decimal("19.00"),
                "piece_qty": 1,
            }
            for index in range(rows)
        ],
        "is_preview": False,
        "total_amount": Decimal("19.00") * rows,
        "total_amount_upper": "合成金额",
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    "url",
    [
        "https://invalid.example/a",
        "http://127.0.0.1/a",
        "file:///etc/passwd",
        "/static/a.png",
        "/media/a.png",
        "ftp://invalid.example/a",
    ],
)
def test_report_fetcher_rejects_every_non_inline_resource(url):
    with pytest.raises(ValueError, match="External resources"):
        _offline_url_fetcher(url)


@pytest.mark.unit
def test_real_pdf_keeps_all_rows_amounts_and_escaped_content():
    note = sample_note(45)
    html = render_to_string("reports/dispatch_note.html", {"note": note})
    pdf = HTML(string=html, url_fetcher=_offline_url_fetcher).write_pdf(presentational_hints=False)
    text = pdf_text(pdf)
    assert pdf.startswith(b"%PDF-")
    for index in range(45):
        assert text.count(f"ROW-{index:04d}") == 1
    assert "855.00" in text
    assert "中文长商品名称" in text
    assert "发运单" in text
    assert text.count("\f") >= 2


@pytest.mark.unit
def test_nested_css_and_svg_resources_never_reach_default_fetcher(monkeypatch, tmp_path):
    import weasyprint

    secret = tmp_path / "private.txt"
    secret.write_text("PDF-PRIVATE-FILE-MUST-NOT-BE-READ")
    attempted = []
    allowed = []
    original = weasyprint.URLFetcher.fetch

    def default_fetch(fetcher, url, *args, **kwargs):
        allowed.append(url)
        assert url.startswith("data:")
        return original(fetcher, url, *args, **kwargs)

    class GuardedFetcher(OfflineURLFetcher):
        def fetch(self, url, *args, **kwargs):
            attempted.append(url)
            return super().fetch(url, *args, **kwargs)

    monkeypatch.setattr(weasyprint.URLFetcher, "fetch", default_fetch)
    image = BytesIO()
    Image.new("RGB", (8, 8), "blue").save(image, format="PNG")
    data = base64.b64encode(image.getvalue()).decode()
    svg = base64.b64encode(
        (
            '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20">'
            '<image href="https://invalid.example/nested" width="20" height="20"/></svg>'
        ).encode()
    ).decode()
    html = (
        '<style>@import url("https://invalid.example/style.css");'
        'body { background-image: url("http://127.0.0.1/private"); }</style>'
        f'<img src="{secret.as_uri()}"><img src="data:image/png;base64,{data}">'
        f'<img src="data:image/svg+xml;base64,{svg}"><p>RESOURCE-BOUNDARY</p>'
    )
    pdf = HTML(string=html, url_fetcher=GuardedFetcher()).write_pdf(presentational_hints=False)
    assert any(url.startswith("file:") for url in attempted)
    assert any(url.startswith("https:") for url in attempted)
    assert allowed and all(url.startswith("data:") for url in allowed)
    assert "RESOURCE-BOUNDARY" in pdf_text(pdf)
    assert secret.read_text() not in pdf_text(pdf)


@pytest.mark.unit
@pytest.mark.parametrize("mime", ["image/x-eps", "image/png"])
def test_inline_postscript_never_invokes_an_external_interpreter(monkeypatch, mime):
    import subprocess

    calls = []

    def forbidden_interpreter(*args, **kwargs):
        calls.append(True)
        raise AssertionError("PDF images must not invoke Ghostscript")

    # Observe the external process boundary without replacing the upstream guard.
    monkeypatch.setattr(subprocess, "Popen", forbidden_interpreter)
    eps = base64.b64encode(
        b"%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 10 10\nshowpage\n%%EOF\n"
    ).decode()
    pdf = HTML(
        string=f'<img src="data:{mime};base64,{eps}"><p>SAFE-CONTROL</p>',
        url_fetcher=_offline_url_fetcher,
    ).write_pdf()
    assert not calls
    monkeypatch.undo()
    assert "SAFE-CONTROL" in pdf_text(pdf)


class DispatchPdfEndpointTests(TestCase):
    def setUp(self):
        report_fixtures.ReportsWarehouseScopeTests.setUp(self)

    def test_real_endpoint_renders_zero_price_and_requires_permission(self):
        task = report_fixtures.ReportsWarehouseScopeTests._create_dispatch_task(
            self, base_price=Decimal("0.0000")
        )
        url = reverse("report_dispatch_pdf", args=[task.pk])
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.user)
        self.assertIn(self.client.get(url).status_code, (403, 404))
        self.client.force_login(self.staff_user)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        text = pdf_text(response.content)
        self.assertIn("配送单商品", text)
        self.assertIn("0.00", text)

    def test_busy_renderer_rejects_before_loading_note_or_rendering_html(self):
        from allapp.reports.pdf_rendering import rendering_slot

        task = report_fixtures.ReportsWarehouseScopeTests._create_dispatch_task(self)
        self.client.force_login(self.staff_user)
        with rendering_slot(), patch("allapp.reports.views._safe_dispatch_note") as prepare:
            response = self.client.get(reverse("report_dispatch_pdf", args=[task.pk]))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response["Retry-After"], "5")
        prepare.assert_not_called()

    @override_settings(PDF_MAX_LINES=1)
    def test_line_limit_returns_explicit_error_without_rendering_partial_pdf(self):
        task = report_fixtures.ReportsWarehouseScopeTests._create_dispatch_task(self)
        line = task.lines.get()
        line.pk = None
        line.save()
        self.client.force_login(self.staff_user)
        with patch("allapp.reports.pdf_rendering.render_pdf") as render:
            response = self.client.get(reverse("report_dispatch_pdf", args=[task.pk]))
        self.assertEqual(response.status_code, 422)
        self.assertContains(response, "最多支持 1 行", status_code=422)
        render.assert_not_called()
