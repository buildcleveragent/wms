from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("reports", "0001_initial")]

    operations = [
        migrations.AlterModelOptions(
            name="etljobrun",
            options={
                "permissions": [
                    ("view_warehouse_operations", "查看仓库运营报表"),
                    ("view_owner_operations", "查看货主运营报表"),
                    ("view_boss_dashboard", "查看仓库老板经营看板"),
                    ("view_warehouse_finance", "查看仓库经营财务数据"),
                    ("export_operations", "导出运营报表"),
                ],
                "verbose_name": "ETL运行日志",
                "verbose_name_plural": "ETL运行日志",
            },
        )
    ]
