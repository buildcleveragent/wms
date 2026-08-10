import uuid

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("products", "0011_identifier_normalized_value_indexes")]

    operations = [
        migrations.AlterField(
            model_name="product",
            name="sku",
            field=models.CharField(
                blank=True,
                help_text="系统按“货主编码+序号”自动生成，货主内唯一",
                max_length=50,
                verbose_name="仓库SKU编码",
            ),
        ),
        migrations.AlterField(
            model_name="product",
            name="vender",
            field=models.CharField(
                blank=True, max_length=200, null=True, verbose_name="厂家"
            ),
        ),
        migrations.CreateModel(
            name="Gs1LookupCache",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "canonical_gtin",
                    models.CharField(
                        max_length=14, unique=True, verbose_name="标准 GTIN-14"
                    ),
                ),
                (
                    "query_code",
                    models.CharField(max_length=16, verbose_name="查询条码"),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("FETCHING", "查询中"),
                            ("SUCCESS", "成功"),
                            ("ERROR", "失败"),
                        ],
                        default="FETCHING",
                        max_length=12,
                        verbose_name="查询状态",
                    ),
                ),
                (
                    "found",
                    models.BooleanField(blank=True, null=True, verbose_name="已查到"),
                ),
                (
                    "registered",
                    models.BooleanField(blank=True, null=True, verbose_name="已注册"),
                ),
                (
                    "payload",
                    models.JSONField(
                        blank=True, default=dict, verbose_name="服务商响应数据"
                    ),
                ),
                (
                    "provider_code",
                    models.IntegerField(
                        blank=True, null=True, verbose_name="服务商状态码"
                    ),
                ),
                (
                    "provider_message",
                    models.CharField(
                        blank=True, max_length=200, verbose_name="服务商消息"
                    ),
                ),
                (
                    "provider_request_id",
                    models.CharField(
                        blank=True,
                        db_index=True,
                        max_length=64,
                        verbose_name="服务商请求 ID",
                    ),
                ),
                (
                    "fetched_at",
                    models.DateTimeField(
                        blank=True, null=True, verbose_name="查询时间"
                    ),
                ),
                (
                    "expires_at",
                    models.DateTimeField(db_index=True, verbose_name="缓存过期时间"),
                ),
                (
                    "lease_until",
                    models.DateTimeField(
                        blank=True, null=True, verbose_name="查询租约到期"
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True, verbose_name="创建时间"),
                ),
                (
                    "updated_at",
                    models.DateTimeField(auto_now=True, verbose_name="更新时间"),
                ),
            ],
            options={
                "verbose_name": "GS1 查询缓存",
                "verbose_name_plural": "GS1 查询缓存",
                "ordering": ("-updated_at",),
            },
        ),
        migrations.CreateModel(
            name="Gs1ProviderRateLimit",
            fields=[
                (
                    "provider",
                    models.CharField(
                        max_length=32,
                        primary_key=True,
                        serialize=False,
                        verbose_name="服务商",
                    ),
                ),
                (
                    "next_allowed_at",
                    models.DateTimeField(verbose_name="下次允许调用时间"),
                ),
                (
                    "updated_at",
                    models.DateTimeField(auto_now=True, verbose_name="更新时间"),
                ),
            ],
            options={
                "verbose_name": "GS1 服务商限速",
                "verbose_name_plural": "GS1 服务商限速",
            },
        ),
    ]
