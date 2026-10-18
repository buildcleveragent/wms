from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("baseinfo", "0007_backfill_owner_customer_bindings")]

    operations = [
        migrations.RemoveConstraint(model_name="customer", name="uq_customer_owner_code"),
        migrations.RemoveConstraint(model_name="customer", name="uq_cust_owner_ext_code"),
    ]
