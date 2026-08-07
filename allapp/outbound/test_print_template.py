from types import SimpleNamespace

from django.template.loader import render_to_string
from django.test import SimpleTestCase

from allapp.outbound.export_print import OUTBOUND_PRINT_CSS_DEFAULTS


class PickTaskPrintTemplateTests(SimpleTestCase):
    def test_print_css_controls_each_outbound_section(self):
        print_css = {
            **OUTBOUND_PRINT_CSS_DEFAULTS,
            "page_size_css": "8in 4in",
            "page_margin": "1mm 2mm",
            "sheet_width": "95%",
            "font_family": "Noto Sans SC, Arial, sans-serif",
            "body_font_size": "11px",
            "company_font_size": "21px",
            "title_font_size": "19px",
            "meta_font_size": "12px",
            "table_font_size": "10px",
            "table_header_font_size": "9px",
            "money_font_size": "14px",
            "footer_font_size": "8px",
            "table_cell_padding": "2px 3px",
        }
        order = SimpleNamespace(
            owner=SimpleNamespace(name="测试货主"),
            order_no="OUT-PRINT-1",
        )

        html = render_to_string(
            "outbound/print/pick_task.html",
            {
                "object": order,
                "lines": [],
                "print_css": print_css,
                "total_qty": 0,
                "total_amount": 0,
                "total_amount_upper": "零元整",
            },
        )

        for css in (
            "size: 8in 4in",
            "margin: 1mm 2mm",
            "width: 95%",
            "font-family: Noto Sans SC, Arial, sans-serif",
            "font-size: 21px",
            "font-size: 19px",
            "font-size: 14px",
            "font-size: 8px",
            "padding: 2px 3px",
        ):
            with self.subTest(css=css):
                self.assertIn(css, html)
