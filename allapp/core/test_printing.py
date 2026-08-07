from types import SimpleNamespace

from django.test import SimpleTestCase

from allapp.core.printing import normalize_print_css

DEFAULTS = {
    "page_size_css": "9.5in 3.6667in",
    "page_margin": "5mm",
    "sheet_width": "100%",
    "font_family": "Microsoft YaHei, Arial, sans-serif",
    "body_font_size": "13px",
    "body_line_height": "1.4",
    "table_cell_padding": "6px 6px",
}


class NormalizePrintCssTests(SimpleTestCase):
    def test_accepts_supported_css_values(self):
        config = SimpleNamespace(
            page_size_css="8in 4in",
            page_margin="1mm 2mm",
            sheet_width="95%",
            font_family="Noto Sans SC, Arial, sans-serif",
            body_font_size="11.5pt",
            body_line_height="1.25",
            table_cell_padding="2px 4px",
        )

        self.assertEqual(
            normalize_print_css(config, DEFAULTS),
            {
                "page_size_css": "8in 4in",
                "page_margin": "1mm 2mm",
                "sheet_width": "95%",
                "font_family": "Noto Sans SC, Arial, sans-serif",
                "body_font_size": "11.5pt",
                "body_line_height": "1.25",
                "table_cell_padding": "2px 4px",
            },
        )

    def test_rejects_invalid_or_injectable_css_values(self):
        config = SimpleNamespace(
            page_size_css="9.5in; color: red",
            page_margin="calc(1px + 1mm)",
            sheet_width="",
            font_family="Arial; background: red",
            body_font_size="large",
            body_line_height="-1",
            table_cell_padding="1px 2px 3px 4px 5px",
        )

        self.assertEqual(normalize_print_css(config, DEFAULTS), DEFAULTS)
        self.assertEqual(normalize_print_css(None, DEFAULTS), DEFAULTS)
