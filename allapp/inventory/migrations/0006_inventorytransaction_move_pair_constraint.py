from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        (
            "inventory",
            "0005_remove_inventorydetail_ux_inv_dim_active_mysql_and_more",
        ),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="inventorytransaction",
            name="ck_tx_sign_by_type",
        ),
        migrations.AddConstraint(
            model_name="inventorytransaction",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(
                        ("tx_type__in", ["RECEIVE", "MOVE_IN"]),
                        ("qty_delta__gt", 0),
                    ),
                    models.Q(
                        ("tx_type__in", ["ISSUE", "MOVE_OUT"]),
                        ("qty_delta__lt", 0),
                    ),
                    ("tx_type__in", ["ADJ_GAIN", "ADJ_LOSS"]),
                    _connector="OR",
                ),
                name="ck_tx_sign_by_type",
            ),
        ),
    ]
