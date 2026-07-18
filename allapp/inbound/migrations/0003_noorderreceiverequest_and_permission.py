import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("inbound", "0002_pdanoorderreceive"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="NoOrderReceiveRequest",
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
                ("request_id", models.CharField(max_length=64, verbose_name="请求ID")),
                (
                    "payload_hash",
                    models.CharField(max_length=64, verbose_name="请求内容摘要"),
                ),
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True, verbose_name="创建时间"),
                ),
                (
                    "updated_at",
                    models.DateTimeField(auto_now=True, verbose_name="更新时间"),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="no_order_receive_requests",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="请求人",
                    ),
                ),
                (
                    "owner",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="no_order_receive_requests",
                        to="baseinfo.owner",
                        verbose_name="货主",
                    ),
                ),
                (
                    "task",
                    models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="no_order_receive_request",
                        to="tasking.wmstask",
                        verbose_name="收货任务",
                    ),
                ),
                (
                    "warehouse",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="no_order_receive_requests",
                        to="locations.warehouse",
                        verbose_name="仓库",
                    ),
                ),
            ],
            options={
                "verbose_name": "无订单收货请求",
                "verbose_name_plural": "无订单收货请求",
                "indexes": [
                    models.Index(
                        fields=["warehouse", "created_at"],
                        name="ix_no_order_recv_wh_time",
                    ),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("created_by", "request_id"),
                        name="uq_no_order_recv_user_req",
                    ),
                ],
            },
        ),
    ]
