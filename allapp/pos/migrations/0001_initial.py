from decimal import Decimal

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("baseinfo", "0002_initial"),
        ("locations", "0002_alter_subwarehouse_options"),
        ("outbound", "0002_initial"),
        ("products", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="PosSale",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("sale_no", models.CharField(max_length=100, unique=True, verbose_name="POS销售单号")),
                (
                    "src_bill_no",
                    models.CharField(blank=True, db_index=True, default="", max_length=100, verbose_name="小票号/外部单号"),
                ),
                (
                    "idempotency_key",
                    models.CharField(blank=True, max_length=100, null=True, unique=True, verbose_name="幂等键"),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[("COMPLETED", "已完成"), ("VOIDED", "已撤销")],
                        db_index=True,
                        default="COMPLETED",
                        max_length=20,
                        verbose_name="状态",
                    ),
                ),
                (
                    "total_amount",
                    models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=18, verbose_name="应收金额"),
                ),
                ("remark", models.CharField(blank=True, default="", max_length=200, verbose_name="备注")),
                ("voided_at", models.DateTimeField(blank=True, null=True, verbose_name="撤销时间")),
                ("void_reason", models.CharField(blank=True, default="", max_length=200, verbose_name="撤销原因")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="创建时间")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="更新时间")),
                (
                    "cashier",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="pos_sales",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "selected_customer",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="pos_sales",
                        to="baseinfo.customer",
                    ),
                ),
                (
                    "voided_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="voided_pos_sales",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "warehouse",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="pos_sales",
                        to="locations.warehouse",
                    ),
                ),
            ],
            options={
                "verbose_name": "POS销售单",
                "verbose_name_plural": "POS销售单",
            },
        ),
        migrations.CreateModel(
            name="PosPayment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
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
                ("amount_due", models.DecimalField(decimal_places=2, max_digits=18, verbose_name="应收金额")),
                ("amount_received", models.DecimalField(decimal_places=2, max_digits=18, verbose_name="实收金额")),
                (
                    "change_amount",
                    models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=18, verbose_name="找零"),
                ),
                ("reference_no", models.CharField(blank=True, default="", max_length=100, verbose_name="支付参考号")),
                (
                    "status",
                    models.CharField(
                        choices=[("PAID", "已收款"), ("VOIDED", "已撤销")],
                        db_index=True,
                        default="PAID",
                        max_length=20,
                        verbose_name="状态",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="创建时间")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="更新时间")),
                (
                    "sale",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="payment",
                        to="pos.possale",
                    ),
                ),
            ],
            options={
                "verbose_name": "POS收款",
                "verbose_name_plural": "POS收款",
            },
        ),
        migrations.CreateModel(
            name="PosSaleLine",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("line_no", models.PositiveIntegerField(verbose_name="行号")),
                ("qty", models.DecimalField(decimal_places=3, max_digits=18, verbose_name="基本数量")),
                ("price", models.DecimalField(decimal_places=4, max_digits=18, verbose_name="基本单价")),
                ("amount", models.DecimalField(decimal_places=2, max_digits=18, verbose_name="金额")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="创建时间")),
                (
                    "outbound_order_line",
                    models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="pos_sale_line",
                        to="outbound.outboundorderline",
                    ),
                ),
                (
                    "owner",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="pos_sale_lines",
                        to="baseinfo.owner",
                    ),
                ),
                (
                    "product",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="pos_sale_lines",
                        to="products.product",
                    ),
                ),
                (
                    "sale",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="lines",
                        to="pos.possale",
                    ),
                ),
            ],
            options={
                "verbose_name": "POS销售明细",
                "verbose_name_plural": "POS销售明细",
            },
        ),
        migrations.CreateModel(
            name="PosSaleOrder",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "amount",
                    models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=18, verbose_name="订单金额"),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="创建时间")),
                (
                    "outbound_order",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="pos_sale_order",
                        to="outbound.outboundorder",
                    ),
                ),
                (
                    "owner",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="pos_sale_orders",
                        to="baseinfo.owner",
                    ),
                ),
                (
                    "sale",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="sale_orders",
                        to="pos.possale",
                    ),
                ),
            ],
            options={
                "verbose_name": "POS销售出库关联",
                "verbose_name_plural": "POS销售出库关联",
            },
        ),
        migrations.AddIndex(
            model_name="possale",
            index=models.Index(fields=["warehouse", "created_at"], name="idx_pos_sale_wh_created"),
        ),
        migrations.AddIndex(
            model_name="possale",
            index=models.Index(fields=["status", "created_at"], name="idx_pos_sale_status"),
        ),
        migrations.AddIndex(
            model_name="pospayment",
            index=models.Index(fields=["method", "created_at"], name="idx_pos_pay_method"),
        ),
        migrations.AddIndex(
            model_name="pospayment",
            index=models.Index(fields=["status", "created_at"], name="idx_pos_pay_status"),
        ),
        migrations.AddIndex(
            model_name="possaleline",
            index=models.Index(fields=["sale", "owner"], name="idx_pos_line_sale_owner"),
        ),
        migrations.AddIndex(
            model_name="possaleline",
            index=models.Index(fields=["product"], name="idx_pos_line_product"),
        ),
        migrations.AddConstraint(
            model_name="possaleline",
            constraint=models.UniqueConstraint(fields=("sale", "line_no"), name="ux_pos_line_no"),
        ),
        migrations.AddIndex(
            model_name="possaleorder",
            index=models.Index(fields=["sale", "owner"], name="idx_pos_sale_order_owner"),
        ),
        migrations.AddConstraint(
            model_name="possaleorder",
            constraint=models.UniqueConstraint(fields=("sale", "owner"), name="ux_pos_sale_owner_order"),
        ),
    ]
