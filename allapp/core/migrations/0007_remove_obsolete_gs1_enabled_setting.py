from django.db import migrations


def remove_obsolete_enabled_setting(apps, schema_editor):
    SystemSetting = apps.get_model("core", "SystemSetting")
    SystemSetting.objects.filter(
        namespace="integration",
        key="apizero_gs1_enabled",
    ).delete()


def restore_enabled_setting(apps, schema_editor):
    SystemSetting = apps.get_model("core", "SystemSetting")
    SystemSetting.objects.update_or_create(
        namespace="integration",
        key="apizero_gs1_enabled",
        defaults={
            "name": "启用 ApiZero GS1 查询",
            "value_type": "boolean",
            "value": "false",
            "default_value": "false",
            "description": "旧版 GS1 启用开关。",
            "client_visible": False,
            "is_secret": False,
            "is_active": True,
            "sort_order": 100,
            "options": {},
        },
    )


class Migration(migrations.Migration):
    dependencies = [("core", "0006_systemsetting_secret_and_gs1_config")]

    operations = [
        migrations.RunPython(
            remove_obsolete_enabled_setting,
            restore_enabled_setting,
        )
    ]
