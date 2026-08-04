from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("accounts", "0004_auditevent")]

    operations = [
        migrations.CreateModel(
            name="LoginThrottleCacheEntry",
            fields=[
                (
                    "cache_key",
                    models.CharField(max_length=255, primary_key=True, serialize=False),
                ),
                ("value", models.TextField()),
                ("expires", models.DateTimeField(db_index=True)),
            ],
            options={
                "verbose_name": "登录限流缓存",
                "verbose_name_plural": "登录限流缓存",
                "db_table": "wms_login_throttle_cache",
            },
        )
    ]
