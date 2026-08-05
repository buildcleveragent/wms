import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("baseinfo", "0005_ownerwarehousebinding"),
        ("locations", "0004_alter_location_zone_type"),
        ("products", "0004_productcategory_mall_fields"),
        ("tasking", "0006_count_production_closure"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name="replenishtaskextra",
            name="trigger",
            field=models.CharField(
                choices=[
                    ("MINMAX", "最小最大"),
                    ("DEMAND", "需求驱动"),
                    ("MANUAL", "手工申请"),
                ],
                default="MINMAX",
                max_length=10,
                verbose_name="触发类型",
            ),
        ),
        migrations.CreateModel(
            name="ReplenishmentPolicy",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("is_deleted", models.BooleanField(default=False, verbose_name="已删除")),
                ("deleted_at", models.DateTimeField(blank=True, null=True, verbose_name="删除时间")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="创建时间")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="更新时间")),
                ("is_active", models.BooleanField(default=True, verbose_name="启用状态")),
                ("remark", models.CharField(blank=True, max_length=200, null=True, verbose_name="备注")),
                ("min_qty", models.DecimalField(decimal_places=4, max_digits=18, verbose_name="补货下限")),
                ("target_qty", models.DecimalField(decimal_places=4, max_digits=18, verbose_name="补货目标")),
                ("source_zone_type", models.PositiveSmallIntegerField(choices=[(1, "拣选区"), (2, "存储区"), (3, "收货区"), (4, "发运区"), (5, "退货区"), (6, "整件区"), (7, "拆零区"), (8, "破损区"), (9, "其他")], default=2, verbose_name="来源区域类型")),
                ("priority", models.PositiveSmallIntegerField(choices=[(1, "低"), (2, "中"), (3, "高")], default=2, verbose_name="优先级")),
                ("auto_release", models.BooleanField(default=False, verbose_name="阈值任务自动发布")),
                ("demand_enabled", models.BooleanField(default=True, verbose_name="启用需求补货")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="%(class)s_created", to=settings.AUTH_USER_MODEL, verbose_name="创建人")),
                ("deleted_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="%(class)s_deleted", to=settings.AUTH_USER_MODEL, verbose_name="删除人")),
                ("owner", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="replenishment_policies", to="baseinfo.owner")),
                ("product", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="replenishment_policies", to="products.product")),
                ("replenish_uom", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="replenishment_policies", to="products.productuom")),
                ("target_location", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="replenishment_policies", to="locations.location")),
                ("updated_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="%(class)s_updated", to=settings.AUTH_USER_MODEL, verbose_name="更新人")),
                ("warehouse", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="replenishment_policies", to="locations.warehouse")),
            ],
            options={
                "verbose_name": "补货策略",
                "verbose_name_plural": "补货策略",
                "permissions": [
                    ("manage_replenishment_policy", "管理补货策略"),
                    ("evaluate_replenishment", "执行补货策略评估"),
                ],
            },
        ),
        migrations.AddField(
            model_name="replenishtaskextra",
            name="policy",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="tasks", to="tasking.replenishmentpolicy"),
        ),
        migrations.CreateModel(
            name="ReplenishmentRequest",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("is_deleted", models.BooleanField(default=False, verbose_name="已删除")),
                ("deleted_at", models.DateTimeField(blank=True, null=True, verbose_name="删除时间")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="创建时间")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="更新时间")),
                ("is_active", models.BooleanField(default=True, verbose_name="启用状态")),
                ("remark", models.CharField(blank=True, max_length=200, null=True, verbose_name="备注")),
                ("requested_qty", models.DecimalField(decimal_places=4, max_digits=18, verbose_name="申请数量")),
                ("reason", models.CharField(max_length=200, verbose_name="申请原因")),
                ("status", models.CharField(choices=[("PENDING", "待审核"), ("APPROVED", "已通过"), ("REJECTED", "已驳回"), ("CANCELLED", "已取消")], db_index=True, default="PENDING", max_length=12, verbose_name="状态")),
                ("reviewed_at", models.DateTimeField(blank=True, null=True)),
                ("review_note", models.CharField(blank=True, default="", max_length=200, verbose_name="审核意见")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="%(class)s_created", to=settings.AUTH_USER_MODEL, verbose_name="创建人")),
                ("deleted_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="%(class)s_deleted", to=settings.AUTH_USER_MODEL, verbose_name="删除人")),
                ("generated_task", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="manual_replenishment_requests", to="tasking.wmstask")),
                ("owner", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="replenishment_requests", to="baseinfo.owner")),
                ("product", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="replenishment_requests", to="products.product")),
                ("reviewed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="replenishment_requests_reviewed", to=settings.AUTH_USER_MODEL)),
                ("target_location", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="replenishment_requests", to="locations.location")),
                ("updated_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="%(class)s_updated", to=settings.AUTH_USER_MODEL, verbose_name="更新人")),
                ("warehouse", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="replenishment_requests", to="locations.warehouse")),
            ],
            options={
                "verbose_name": "补货申请",
                "verbose_name_plural": "补货申请",
                "permissions": [
                    ("request_replenishment", "申请补货"),
                    ("approve_replenishment", "审核补货申请"),
                ],
            },
        ),
        migrations.AddField(
            model_name="replenishtaskextra",
            name="request",
            field=models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="task_extra", to="tasking.replenishmentrequest"),
        ),
        migrations.AddField(
            model_name="replenishtaskextra",
            name="demand_order_ids",
            field=models.JSONField(blank=True, default=list, verbose_name="关联需求订单ID"),
        ),
        migrations.AddIndex(model_name="replenishmentpolicy", index=models.Index(fields=["warehouse", "owner", "product", "is_active"], name="ix_replen_policy_scope")),
        migrations.AddIndex(model_name="replenishmentpolicy", index=models.Index(fields=["target_location", "is_active"], name="ix_replen_policy_target")),
        migrations.AddConstraint(model_name="replenishmentpolicy", constraint=models.UniqueConstraint(fields=("owner", "warehouse", "product", "target_location", "is_deleted"), name="ux_replen_policy_scope")),
        migrations.AddConstraint(model_name="replenishmentpolicy", constraint=models.CheckConstraint(condition=models.Q(("min_qty__gte", 0), ("target_qty__gt", models.F("min_qty"))), name="ck_replen_policy_qty")),
        migrations.AddIndex(model_name="replenishmentrequest", index=models.Index(fields=["warehouse", "status", "created_at"], name="ix_replen_req_queue")),
        migrations.AddIndex(model_name="replenishmentrequest", index=models.Index(fields=["created_by", "status", "created_at"], name="ix_replen_req_creator")),
        migrations.AddConstraint(model_name="replenishmentrequest", constraint=models.CheckConstraint(condition=models.Q(("requested_qty__gt", 0)), name="ck_replen_req_qty")),
    ]
