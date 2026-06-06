from django.db import migrations


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("inventory", "0001_initial"),
        ("outbound", "0002_initial"),
        ("products", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="PosAvailableInventory",
            fields=[],
            options={
                "verbose_name": "POS可售库存",
                "verbose_name_plural": "POS可售库存",
                "proxy": True,
                "indexes": [],
                "constraints": [],
            },
            bases=("inventory.inventorydetail",),
        ),
        migrations.CreateModel(
            name="PosProduct",
            fields=[],
            options={
                "verbose_name": "POS商品",
                "verbose_name_plural": "POS商品",
                "proxy": True,
                "indexes": [],
                "constraints": [],
            },
            bases=("products.product",),
        ),
        migrations.CreateModel(
            name="PosProductPackage",
            fields=[],
            options={
                "verbose_name": "POS包装条码",
                "verbose_name_plural": "POS包装条码",
                "proxy": True,
                "indexes": [],
                "constraints": [],
            },
            bases=("products.productpackage",),
        ),
        migrations.CreateModel(
            name="PosSaleOrder",
            fields=[],
            options={
                "verbose_name": "POS销售单",
                "verbose_name_plural": "POS销售单",
                "proxy": True,
                "indexes": [],
                "constraints": [],
            },
            bases=("outbound.outboundorder",),
        ),
        migrations.CreateModel(
            name="PosSaleOrderLine",
            fields=[],
            options={
                "verbose_name": "POS销售明细",
                "verbose_name_plural": "POS销售明细",
                "proxy": True,
                "indexes": [],
                "constraints": [],
            },
            bases=("outbound.outboundorderline",),
        ),
    ]
