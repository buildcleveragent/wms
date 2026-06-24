from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("pos", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="possale",
            name="idempotency_fingerprint",
            field=models.CharField(
                blank=True, default="", max_length=64, verbose_name="幂等请求指纹"
            ),
        ),
    ]
