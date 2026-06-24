from decimal import Decimal

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("pos", "0002_possale_idempotency_fingerprint"),
    ]

    operations = [
        migrations.CreateModel(
            name="PosShift",
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
                    "shift_no",
                    models.CharField(
                        max_length=100, unique=True, verbose_name="班次号"
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("OPEN", "进行中"),
                            ("CLOSED", "已交班"),
                            ("REOPENED", "已重开"),
                        ],
                        db_index=True,
                        default="OPEN",
                        max_length=20,
                        verbose_name="状态",
                    ),
                ),
                ("opened_at", models.DateTimeField(verbose_name="开班时间")),
                (
                    "closed_at",
                    models.DateTimeField(
                        blank=True, null=True, verbose_name="交班时间"
                    ),
                ),
                (
                    "opening_cash_amount",
                    models.DecimalField(
                        decimal_places=2,
                        default=Decimal("0.00"),
                        max_digits=18,
                        verbose_name="备用金",
                    ),
                ),
                (
                    "expected_cash_amount",
                    models.DecimalField(
                        decimal_places=2,
                        default=Decimal("0.00"),
                        max_digits=18,
                        verbose_name="现金应点金额",
                    ),
                ),
                (
                    "actual_cash_amount",
                    models.DecimalField(
                        decimal_places=2,
                        default=Decimal("0.00"),
                        max_digits=18,
                        verbose_name="现金实点金额",
                    ),
                ),
                (
                    "cash_difference",
                    models.DecimalField(
                        decimal_places=2,
                        default=Decimal("0.00"),
                        max_digits=18,
                        verbose_name="现金差异",
                    ),
                ),
                (
                    "total_sales_amount",
                    models.DecimalField(
                        decimal_places=2,
                        default=Decimal("0.00"),
                        max_digits=18,
                        verbose_name="净销售额",
                    ),
                ),
                (
                    "total_voided_amount",
                    models.DecimalField(
                        decimal_places=2,
                        default=Decimal("0.00"),
                        max_digits=18,
                        verbose_name="作废金额",
                    ),
                ),
                (
                    "sale_count",
                    models.PositiveIntegerField(default=0, verbose_name="销售单数"),
                ),
                (
                    "completed_count",
                    models.PositiveIntegerField(default=0, verbose_name="完成单数"),
                ),
                (
                    "voided_count",
                    models.PositiveIntegerField(default=0, verbose_name="作废单数"),
                ),
                (
                    "remark",
                    models.CharField(
                        blank=True, default="", max_length=200, verbose_name="备注"
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
                (
                    "cashier",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="pos_shifts",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "closed_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="closed_pos_shifts",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "opened_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="opened_pos_shifts",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "warehouse",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="pos_shifts",
                        to="locations.warehouse",
                    ),
                ),
            ],
            options={
                "verbose_name": "POS班次",
                "verbose_name_plural": "POS班次",
                "indexes": [
                    models.Index(
                        fields=["warehouse", "cashier", "status"],
                        name="idx_pos_shift_wh_cashier",
                    ),
                    models.Index(fields=["opened_at"], name="idx_pos_shift_opened"),
                ],
            },
        ),
        migrations.AddField(
            model_name="possale",
            name="shift",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="sales",
                to="pos.posshift",
            ),
        ),
        migrations.CreateModel(
            name="PosShiftPaymentSummary",
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
                    "method",
                    models.CharField(
                        choices=[
                            ("CASH", "现金"),
                            ("WECHAT", "微信"),
                            ("ALIPAY", "支付宝"),
                            ("BANK_CARD", "银行卡"),
                            ("OTHER", "其他"),
                        ],
                        max_length=20,
                        verbose_name="支付方式",
                    ),
                ),
                (
                    "sale_count",
                    models.PositiveIntegerField(default=0, verbose_name="销售单数"),
                ),
                (
                    "expected_amount",
                    models.DecimalField(
                        decimal_places=2,
                        default=Decimal("0.00"),
                        max_digits=18,
                        verbose_name="系统金额",
                    ),
                ),
                (
                    "actual_amount",
                    models.DecimalField(
                        decimal_places=2,
                        default=Decimal("0.00"),
                        max_digits=18,
                        verbose_name="实点金额",
                    ),
                ),
                (
                    "difference",
                    models.DecimalField(
                        decimal_places=2,
                        default=Decimal("0.00"),
                        max_digits=18,
                        verbose_name="差异",
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
                (
                    "shift",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="payment_summaries",
                        to="pos.posshift",
                    ),
                ),
            ],
            options={
                "verbose_name": "POS班次支付汇总",
                "verbose_name_plural": "POS班次支付汇总",
                "constraints": [
                    models.UniqueConstraint(
                        fields=("shift", "method"),
                        name="ux_pos_shift_payment_method",
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name="PosPrintLog",
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
                    "print_type",
                    models.CharField(
                        choices=[
                            ("RECEIPT", "销售小票"),
                            ("SHIFT_SUMMARY", "交班单"),
                            ("POS_STATS", "POS统计"),
                        ],
                        max_length=30,
                        verbose_name="打印类型",
                    ),
                ),
                (
                    "printed_at",
                    models.DateTimeField(auto_now_add=True, verbose_name="打印时间"),
                ),
                (
                    "copy_no",
                    models.PositiveIntegerField(default=1, verbose_name="打印次数"),
                ),
                (
                    "payload_hash",
                    models.CharField(
                        blank=True,
                        default="",
                        max_length=64,
                        verbose_name="内容指纹",
                    ),
                ),
                (
                    "remark",
                    models.CharField(
                        blank=True, default="", max_length=200, verbose_name="备注"
                    ),
                ),
                (
                    "printed_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="pos_print_logs",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "sale",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="print_logs",
                        to="pos.possale",
                    ),
                ),
                (
                    "shift",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="print_logs",
                        to="pos.posshift",
                    ),
                ),
            ],
            options={
                "verbose_name": "POS打印日志",
                "verbose_name_plural": "POS打印日志",
                "indexes": [
                    models.Index(
                        fields=["sale", "print_type"], name="idx_pos_print_sale"
                    ),
                    models.Index(
                        fields=["shift", "print_type"], name="idx_pos_print_shift"
                    ),
                ],
            },
        ),
        migrations.AddIndex(
            model_name="possale",
            index=models.Index(
                fields=["shift", "created_at"], name="idx_pos_sale_shift"
            ),
        ),
    ]
