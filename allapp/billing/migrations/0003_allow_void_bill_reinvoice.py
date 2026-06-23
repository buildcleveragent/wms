from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0002_initial"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="bill",
            name="ux_bill_owner_wh_period",
        ),
    ]
