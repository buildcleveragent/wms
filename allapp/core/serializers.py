from rest_framework import serializers

from .models import PrintConfig


class PrintConfigSerializer(serializers.ModelSerializer):
    page_size = serializers.CharField(source="effective_page_size_css", read_only=True)

    class Meta:
        model = PrintConfig
        fields = (
            "id",
            "code",
            "name",
            "module",
            "print_method",
            "printer_type",
            "paper_mode",
            "paper_width",
            "paper_height",
            "page_size_css",
            "page_size",
            "page_margin",
            "sheet_width",
            "sheet_padding_top",
            "sheet_padding_right",
            "sheet_padding_bottom",
            "sheet_padding_left",
            "body_font_size",
            "company_font_size",
            "title_font_size",
            "meta_font_size",
            "table_font_size",
            "table_header_font_size",
            "money_font_size",
            "footer_font_size",
            "body_line_height",
            "meta_line_height",
            "table_line_height",
            "money_line_height",
            "footer_line_height",
            "table_cell_padding",
            "money_gap",
            "money_margin_top",
            "extra",
            "is_default",
            "is_active",
            "sort_order",
            "remark",
        )
