from types import SimpleNamespace

from django.template.loader import render_to_string
from django.test import SimpleTestCase

from allapp.outbound.export_print import OUTBOUND_PRINT_CSS_DEFAULTS


class PickTaskPrintTemplateTests(SimpleTestCase):
    def test_pick_omits_contact_details_but_preserves_work_instructions(self):
        context = {
            "kind": "pick",
            "document_title": "拣货单",
            "warehouse_name": "实际出库仓",
            "task": {"task_no": "PICK-001"},
            "object": {"order_no": "ORDER-001"},
            "sender_name": "货主甲",
            "customer_name": "客户乙",
            "sender_phone": "13800000001",
            "receiver_phone": "13800000002",
            "receiver_name": "收货人丙",
            "receiver_address": "客户路1号",
            "warehouse_address": "仓库路2号",
            "warehouse_phone": "0771-1234567",
            "is_pickup": True,
            "print_css": OUTBOUND_PRINT_CSS_DEFAULTS,
            "lines": [
                {
                    "product": {"name": "茶叶", "spec": "250g×4罐", "base_uom": {"name": "件"}},
                    "location": {"code": "A-01"},
                    "lot_no": "LOT-001",
                    "base_uom": {"name": "件"},
                    "base_qty": "2.125",
                }
            ],
        }
        html = render_to_string("outbound/print/pick_task.html", context)
        for value in (
            "PICK-001",
            "ORDER-001",
            "货主甲",
            "客户乙",
            "250g×4罐",
            "A-01",
            "LOT-001",
            "2.125",
            "实拣填写",
            "拣货人",
        ):
            self.assertIn(value, html)
        for field in (
            "sender_phone",
            "receiver_phone",
            "receiver_name",
            "receiver_address",
            "warehouse_address",
            "warehouse_phone",
        ):
            self.assertNotIn(context[field], html)
        for value in ("客户自提", "单价", "金额", "过账时间"):
            self.assertNotIn(value, html)
        self.assertEqual(html.count("实际出库仓"), 1)

    def test_delivery_warehouse_address_and_phone_share_row_without_repeated_name(self):
        html = render_to_string(
            "outbound/print/_header.html",
            {
                "kind": "delivery",
                "warehouse_name": "实际出库仓",
                "warehouse_address": "仓库路2号",
                "warehouse_phone": "0771-1234567",
            },
        )
        self.assertEqual(html.count("实际出库仓"), 1)
        warehouse_row = html.split('class="meta-line warehouse-address"', 1)[1].split("</div>", 1)[
            0
        ]
        self.assertIn("仓库路2号", warehouse_row)
        self.assertIn("0771-1234567", warehouse_row)

    def test_dispatch_print_distinguishes_unpriced_from_legacy_numeric_amounts(self):
        legacy = {"sku_name": "历史商品", "qty": 2, "price": 12.5, "amount": 25}
        note = {"items": [legacy], "total_amount": 25, "total_amount_upper": "贰拾伍元整"}
        html = render_to_string("reports/dispatch_note.html", {"note": note})
        self.assertNotIn("未计价", html)
        self.assertIn("12.50", html)
        note.update(
            items=[{**legacy, "is_priced": False, "price": None, "amount": None}],
            amount_status="UNPRICED",
            unpriced_line_count=1,
            total_amount=0,
        )
        html = render_to_string("reports/dispatch_note.html", {"note": note})
        self.assertIn("历史商品（未计价）", html)
        self.assertNotIn("12.50", html)
        self.assertNotIn("金额：0.00", html)
        note.update(items=[legacy, *note["items"]], amount_status="PARTIAL", total_amount=25)
        html = render_to_string("reports/dispatch_note.html", {"note": note})
        self.assertIn("已计价小计 ¥25.00，另有 1 行未计价", html)

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
