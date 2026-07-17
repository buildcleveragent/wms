from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tasking", "0002_allow_reserved_task_status_logs"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="wmstask",
            index=models.Index(
                fields=["warehouse", "task_type", "source_model", "source_pk"],
                name="ix_task_wh_type_src",
            ),
        ),
    ]
