from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("tasking", "0005_taskscanlog_serial_no"),
    ]

    operations = [
        migrations.AddField(
            model_name="counttaskextra",
            name="scope_payload",
            field=models.JSONField(
                blank=True, default=dict, verbose_name="盘点范围条件"
            ),
        ),
        migrations.AddField(
            model_name="counttaskextra",
            name="root_task",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="count_rounds_as_root",
                to="tasking.wmstask",
                verbose_name="根盘点任务",
            ),
        ),
        migrations.AddField(
            model_name="counttaskextra",
            name="parent_task",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="count_recount_tasks",
                to="tasking.wmstask",
                verbose_name="上轮盘点任务",
            ),
        ),
        migrations.AddField(
            model_name="counttaskextra",
            name="round_no",
            field=models.PositiveSmallIntegerField(default=1, verbose_name="盘点轮次"),
        ),
        migrations.AddField(
            model_name="counttaskextra",
            name="snapshot_at",
            field=models.DateTimeField(
                blank=True, null=True, verbose_name="账面快照时间"
            ),
        ),
        migrations.AddIndex(
            model_name="counttaskextra",
            index=models.Index(
                fields=["root_task", "round_no"], name="ix_cnttsk_root_round"
            ),
        ),
        migrations.CreateModel(
            name="CountScopeLock",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "batch_no",
                    models.CharField(
                        blank=True, default="", max_length=64, verbose_name="批次号"
                    ),
                ),
                (
                    "lock_key",
                    models.CharField(
                        db_index=True, max_length=255, verbose_name="锁范围键"
                    ),
                ),
                (
                    "active_key",
                    models.CharField(
                        blank=True,
                        max_length=255,
                        null=True,
                        unique=True,
                        verbose_name="活动锁唯一键",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "released_at",
                    models.DateTimeField(blank=True, db_index=True, null=True),
                ),
                (
                    "active_task",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="active_count_scope_locks",
                        to="tasking.wmstask",
                        verbose_name="当前盘点任务",
                    ),
                ),
                (
                    "location",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="locations.location",
                        verbose_name="库位",
                    ),
                ),
                (
                    "owner",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="baseinfo.owner",
                        verbose_name="货主",
                    ),
                ),
                (
                    "product",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        to="products.product",
                        verbose_name="商品",
                    ),
                ),
                (
                    "released_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="released_count_scope_locks",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "root_task",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="count_scope_locks",
                        to="tasking.wmstask",
                        verbose_name="根盘点任务",
                    ),
                ),
                (
                    "warehouse",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="locations.warehouse",
                        verbose_name="仓库",
                    ),
                ),
            ],
            options={
                "verbose_name": "盘点范围锁",
                "verbose_name_plural": "盘点范围锁",
            },
        ),
        migrations.AddIndex(
            model_name="countscopelock",
            index=models.Index(
                fields=["warehouse", "released_at"], name="ix_cntlock_wh_rel"
            ),
        ),
        migrations.AddIndex(
            model_name="countscopelock",
            index=models.Index(
                fields=["owner", "location", "product", "released_at"],
                name="ix_cntlock_scope",
            ),
        ),
        migrations.RemoveConstraint(
            model_name="taskscanlog",
            name="ck_tscan_ok_qty",
        ),
        migrations.AddConstraint(
            model_name="taskscanlog",
            constraint=models.CheckConstraint(
                condition=(
                    ~models.Q(status="OK")
                    | models.Q(qty_base_delta__isnull=False)
                    | models.Q(qty_base__isnull=False)
                ),
                name="ck_tscan_ok_qty",
            ),
        ),
    ]
