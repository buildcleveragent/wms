from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("inbound", "0003_noorderreceiverequest_and_permission")]

    operations = [
        migrations.AddIndex(
            model_name="inboundorder",
            index=models.Index(
                fields=["owner", "biz_date", "approval_status", "id"],
                name="ix_in_own_date_appr_id",
            ),
        ),
        migrations.AddIndex(
            model_name="inboundorder",
            index=models.Index(
                fields=["warehouse", "biz_date", "approval_status", "id"],
                name="ix_in_wh_date_appr_id",
            ),
        ),
    ]
