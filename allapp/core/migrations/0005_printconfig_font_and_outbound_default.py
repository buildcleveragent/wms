from django.db import migrations, models


def create_outbound_print_config(apps, schema_editor):
    PrintConfig = apps.get_model("core", "PrintConfig")
    PrintConfig.objects.filter(module="outbound", is_default=True).exclude(
        code="outbound_dot_241_93"
    ).update(is_default=False)
    PrintConfig.objects.update_or_create(
        code="outbound_dot_241_93",
        defaults={
            "name": "出库单 Epson 9.5 × 3 2/3 英寸连续纸",
            "module": "outbound",
            "print_method": "backend_html",
            "printer_type": "dot_matrix",
            "paper_mode": "dot_241_93",
            "paper_width": "9.5in",
            "paper_height": "3.6667in",
            "page_size_css": "9.5in 3.6667in",
            "page_margin": "5mm",
            "sheet_width": "100%",
            "sheet_padding_top": "0",
            "sheet_padding_right": "0",
            "sheet_padding_bottom": "0",
            "sheet_padding_left": "0",
            "font_family": "Microsoft YaHei, Arial, sans-serif",
            "body_font_size": "13px",
            "company_font_size": "22px",
            "title_font_size": "22px",
            "meta_font_size": "13px",
            "table_font_size": "13px",
            "table_header_font_size": "13px",
            "money_font_size": "13px",
            "footer_font_size": "12px",
            "body_line_height": "1.4",
            "meta_line_height": "1.4",
            "table_line_height": "18px",
            "money_line_height": "18px",
            "footer_line_height": "1.4",
            "table_cell_padding": "6px 6px",
            "money_gap": "6px",
            "money_margin_top": "5px",
            "is_default": True,
            "is_active": True,
            "sort_order": 10,
            "remark": "拣货任务出库单默认针式三等分连续纸配置。",
        },
    )


def remove_outbound_print_config(apps, schema_editor):
    PrintConfig = apps.get_model("core", "PrintConfig")
    PrintConfig.objects.filter(code="outbound_dot_241_93").delete()


class Migration(migrations.Migration):
    dependencies = [("core", "0004_normalize_print_config_mm_names")]

    operations = [
        migrations.AddField(
            model_name="printconfig",
            name="font_family",
            field=models.CharField(
                default="Microsoft YaHei, Arial, sans-serif",
                help_text="使用逗号分隔字体名称，不需要引号。",
                max_length=200,
                verbose_name="字体",
            ),
        ),
        migrations.RunPython(
            create_outbound_print_config,
            remove_outbound_print_config,
        ),
    ]
