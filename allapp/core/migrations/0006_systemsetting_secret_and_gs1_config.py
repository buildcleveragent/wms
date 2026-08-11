from django.db import migrations, models


def create_gs1_settings(apps, schema_editor):
    SystemSetting = apps.get_model("core", "SystemSetting")
    SystemSetting.objects.update_or_create(
        namespace="integration",
        key="apizero_gs1_enabled",
        defaults={
            "name": "启用 ApiZero GS1 查询",
            "value_type": "boolean",
            "value": "false",
            "default_value": "false",
            "description": "启用后，PDA 新 GTIN 本地未命中时调用 ApiZero 查询。",
            "client_visible": False,
            "is_secret": False,
            "is_active": True,
            "sort_order": 100,
            "options": {},
        },
    )
    SystemSetting.objects.update_or_create(
        namespace="integration",
        key="apizero_gs1_api_key",
        defaults={
            "name": "ApiZero GS1 API Key",
            "value_type": "string",
            "value": "",
            "default_value": "",
            "description": "ApiZero 条码查询密钥；保存后加密存储且不向前端公开。",
            "client_visible": False,
            "is_secret": True,
            "is_active": True,
            "sort_order": 110,
            "options": {},
        },
    )


def remove_gs1_settings(apps, schema_editor):
    SystemSetting = apps.get_model("core", "SystemSetting")
    SystemSetting.objects.filter(
        namespace="integration",
        key__in=("apizero_gs1_enabled", "apizero_gs1_api_key"),
    ).delete()


class Migration(migrations.Migration):
    dependencies = [("core", "0005_printconfig_font_and_outbound_default")]

    operations = [
        migrations.AddField(
            model_name="systemsetting",
            name="is_secret",
            field=models.BooleanField(default=False, verbose_name="密钥配置"),
        ),
        migrations.RunPython(create_gs1_settings, remove_gs1_settings),
    ]
