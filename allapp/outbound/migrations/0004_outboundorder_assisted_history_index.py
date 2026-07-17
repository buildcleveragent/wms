from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("outbound", "0003_alter_outboundorder_options_and_more"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="outboundorder",
            index=models.Index(
                fields=["warehouse", "processing_mode", "assisted_at", "id"],
                name="ix_out_wh_mode_assist",
            ),
        ),
    ]
