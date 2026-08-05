import re

import django.db.models.deletion
from django.db import migrations, models


SOURCE_RE = re.compile(r"来源任务:([^,，\s]+)(?:[,，]\s*行:(\d+))?")


def backfill_dashboard_scope(apps, schema_editor):
    Snapshot = apps.get_model("inventory", "InventorySnapshotDaily")
    Difference = apps.get_model("inventory", "ReviewDifference")
    Task = apps.get_model("tasking", "WmsTask")
    TaskLine = apps.get_model("tasking", "WmsTaskLine")

    for snapshot in Snapshot.objects.filter(base_unit_code="").iterator():
        code = (
            Snapshot.objects.filter(pk=snapshot.pk)
            .values_list("product__base_uom__code", flat=True)
            .first()
            or ""
        )
        if code:
            Snapshot.objects.filter(pk=snapshot.pk).update(base_unit_code=code)

    for difference in Difference.objects.filter(owner__isnull=True).iterator():
        owner_id = None
        source_task_id = None
        source_line_id = None
        match = SOURCE_RE.search(difference.note or "")
        if match:
            task = Task.objects.filter(
                task_no=match.group(1), warehouse_id=difference.warehouse_id
            ).first()
            if task:
                owner_id = task.owner_id
                source_task_id = task.pk
                if match.group(2):
                    source_line_id = (
                        TaskLine.objects.filter(pk=int(match.group(2)), task_id=task.pk)
                        .values_list("pk", flat=True)
                        .first()
                    )
        if owner_id is None:
            owner_ids = list(
                difference.lines.values_list("product__owner_id", flat=True)
                .distinct()
                .order_by("product__owner_id")[:2]
            )
            if len(owner_ids) == 1:
                owner_id = owner_ids[0]
        Difference.objects.filter(pk=difference.pk).update(
            owner_id=owner_id,
            source_task_id=source_task_id,
            source_task_line_id=source_line_id,
        )


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0007_snapshot_approximate_lineage"),
        ("tasking", "0008_reloclineextra_from_container_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="inventorysnapshotdaily",
            name="base_unit_code",
            field=models.CharField(
                blank=True,
                default="",
                help_text="生成快照时的商品基本单位编码；空值表示历史记录无法确认。",
                max_length=30,
                verbose_name="基本单位快照",
            ),
        ),
        migrations.AddField(
            model_name="reviewdifference",
            name="owner",
            field=models.ForeignKey(
                blank=True,
                help_text="历史无法唯一确认归属的差异单允许为空。",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="review_differences",
                to="baseinfo.owner",
                verbose_name="货主",
            ),
        ),
        migrations.AddField(
            model_name="reviewdifference",
            name="source_task",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="review_differences",
                to="tasking.wmstask",
                verbose_name="来源任务",
            ),
        ),
        migrations.AddField(
            model_name="reviewdifference",
            name="source_task_line",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="review_differences",
                to="tasking.wmstaskline",
                verbose_name="来源任务行",
            ),
        ),
        migrations.RunPython(backfill_dashboard_scope, migrations.RunPython.noop),
    ]
