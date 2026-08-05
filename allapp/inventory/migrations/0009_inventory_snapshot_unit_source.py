from django.db import migrations, models


def mark_legacy_unit_sources(apps, schema_editor):
    Snapshot = apps.get_model("inventory", "InventorySnapshotDaily")
    Snapshot.objects.filter(base_unit_code="").update(base_unit_source="UNKNOWN")
    Snapshot.objects.exclude(base_unit_code="").update(
        base_unit_source="LEGACY_INFERRED"
    )


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0008_boss_dashboard_scope_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="inventorysnapshotdaily",
            name="base_unit_source",
            field=models.CharField(
                choices=[
                    ("VERIFIED", "快照生成时确认"),
                    ("LEGACY_INFERRED", "按当前商品单位回填"),
                    ("UNKNOWN", "无法确认"),
                ],
                default="UNKNOWN",
                max_length=20,
                verbose_name="基本单位来源",
            ),
        ),
        migrations.RunPython(mark_legacy_unit_sources, migrations.RunPython.noop),
    ]
