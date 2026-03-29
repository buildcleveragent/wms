from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("baseinfo", "0002_initial"),
        ("inventory", "0001_initial"),
        ("locations", "0001_initial"),
        ("products", "0003_alter_productuom_code"),
    ]

    operations = [
        migrations.CreateModel(
            name="InventorySnapshotDaily",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("snapshot_date", models.DateField(db_index=True, verbose_name="快照日期")),
                ("batch_no", models.CharField(blank=True, default="", max_length=64, verbose_name="批次号")),
                ("production_date", models.DateField(blank=True, null=True, verbose_name="生产日期")),
                ("expiry_date", models.DateField(blank=True, null=True, verbose_name="有效期至")),
                ("serial_no", models.CharField(blank=True, default="", max_length=64, verbose_name="序列号")),
                ("onhand_qty", models.DecimalField(decimal_places=4, default=0, max_digits=18, verbose_name="账面库存快照")),
                ("available_qty", models.DecimalField(decimal_places=4, default=0, max_digits=18, verbose_name="可用库存快照")),
                ("allocated_qty", models.DecimalField(decimal_places=4, default=0, max_digits=18, verbose_name="已分配快照")),
                ("locked_qty", models.DecimalField(decimal_places=4, default=0, max_digits=18, verbose_name="锁定快照")),
                ("damaged_qty", models.DecimalField(decimal_places=4, default=0, max_digits=18, verbose_name="损坏快照")),
                (
                    "unit_volume_m3_snapshot",
                    models.DecimalField(blank=True, decimal_places=6, max_digits=12, null=True, verbose_name="单位体积快照(m³)"),
                ),
                (
                    "location_area_m2_snapshot",
                    models.DecimalField(blank=True, decimal_places=4, max_digits=12, null=True, verbose_name="库位面积快照(㎡)"),
                ),
                ("snapshot_source", models.CharField(blank=True, default="", max_length=40, verbose_name="快照来源")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="创建时间")),
                (
                    "location",
                    models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="locations.location", verbose_name="库位"),
                ),
                (
                    "owner",
                    models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="baseinfo.owner", verbose_name="货主"),
                ),
                (
                    "product",
                    models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="products.product", verbose_name="商品"),
                ),
                (
                    "warehouse",
                    models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="locations.warehouse", verbose_name="仓库"),
                ),
            ],
            options={
                "verbose_name": "库存日快照",
                "verbose_name_plural": "库存日快照",
            },
        ),
        migrations.AddConstraint(
            model_name="inventorysnapshotdaily",
            constraint=models.UniqueConstraint(
                fields=(
                    "snapshot_date",
                    "owner",
                    "warehouse",
                    "location",
                    "product",
                    "batch_no",
                    "production_date",
                    "expiry_date",
                    "serial_no",
                ),
                name="ux_inv_snapshot_daily_dim",
            ),
        ),
        migrations.AddIndex(
            model_name="inventorysnapshotdaily",
            index=models.Index(fields=["snapshot_date", "owner", "warehouse"], name="idx_inv_snapshot_date_scope"),
        ),
        migrations.AddIndex(
            model_name="inventorysnapshotdaily",
            index=models.Index(fields=["owner", "warehouse", "location"], name="idx_inv_snapshot_scope_loc"),
        ),
        migrations.AddIndex(
            model_name="inventorysnapshotdaily",
            index=models.Index(fields=["product", "snapshot_date"], name="idx_inv_snapshot_product_date"),
        ),
    ]
