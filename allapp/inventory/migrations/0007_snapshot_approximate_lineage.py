from django.db import migrations, models


def propagate_approximate_lineage(apps, schema_editor):
    Snapshot = apps.get_model("inventory", "InventorySnapshotDaily")
    Metric = apps.get_model("billing", "BillingMetricDaily")
    Accrual = apps.get_model("billing", "BillingAccrual")

    scopes = Snapshot.objects.values_list("owner_id", "warehouse_id").distinct()
    for owner_id, warehouse_id in scopes.iterator():
        approximate = False
        dates = []
        rows = Snapshot.objects.filter(
            owner_id=owner_id, warehouse_id=warehouse_id
        ).order_by("snapshot_date", "id")
        current_date = None
        for row in rows.iterator():
            if row.snapshot_date != current_date:
                current_date = row.snapshot_date
                if row.snapshot_source == "BOOTSTRAP_DETAIL":
                    approximate = True
                if approximate:
                    dates.append(current_date)
            if approximate and row.snapshot_source != "BOOTSTRAP_DETAIL":
                Snapshot.objects.filter(pk=row.pk).update(
                    snapshot_source="TX_ROLLFORWARD_APPROX"
                )
        if not dates:
            continue
        Metric.objects.filter(
            owner_id=owner_id,
            warehouse_id=warehouse_id,
            service_date__in=dates,
            source__startswith="AUTO:",
            metric_type__in=["PALLET", "CBM", "AREA_M2"],
        ).update(source_quality="APPROXIMATE")
        Accrual.objects.filter(
            owner_id=owner_id,
            warehouse_id=warehouse_id,
            service_date__in=dates,
            status="OPEN",
            charge_type="STORAGE",
        ).update(
            source_quality="APPROXIMATE",
            source_note="Historical snapshot lineage includes BOOTSTRAP_DETAIL.",
        )


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0006_inventorytransaction_move_pair_constraint"),
        ("billing", "0004_billing_integrity"),
    ]

    operations = [
        migrations.AlterField(
            model_name="inventorysnapshotdaily",
            name="snapshot_source",
            field=models.CharField(
                blank=True,
                choices=[
                    ("BOOTSTRAP_DETAIL", "当前库存回补历史（近似）"),
                    ("TX_ROLLFORWARD", "可信事务推演"),
                    ("TX_ROLLFORWARD_APPROX", "近似基线事务推演"),
                ],
                default="",
                max_length=40,
                verbose_name="快照来源",
            ),
        ),
        migrations.RunPython(propagate_approximate_lineage, migrations.RunPython.noop),
    ]
