from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("outbound", "0006_outbound_owner_reject_reason")]

    operations = [
        migrations.AddIndex(
            model_name="outboundorder",
            index=models.Index(
                fields=["owner", "biz_date", "approval_status", "id"],
                name="ix_out_own_date_appr",
            ),
        ),
        migrations.AddIndex(
            model_name="outboundorder",
            index=models.Index(
                fields=["warehouse", "biz_date", "approval_status", "id"],
                name="ix_out_wh_date_appr",
            ),
        ),
    ]
