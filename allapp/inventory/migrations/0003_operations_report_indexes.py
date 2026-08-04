from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("inventory", "0002_clarify_warehouse_subwarehouse_scope")]

    operations = [
        migrations.AddIndex(
            model_name="inventorytransaction",
            index=models.Index(
                fields=["tx_type", "owner", "posted_at", "id"],
                name="ix_tx_type_owner_time",
            ),
        ),
        migrations.AddIndex(
            model_name="inventorytransaction",
            index=models.Index(
                fields=["tx_type", "warehouse", "posted_at", "id"],
                name="ix_tx_type_wh_time",
            ),
        ),
    ]
