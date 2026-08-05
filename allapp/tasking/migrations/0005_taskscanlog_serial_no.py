from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tasking", "0004_operations_report_indexes"),
    ]

    operations = [
        migrations.AddField(
            model_name="taskscanlog",
            name="serial_no",
            field=models.CharField(
                blank=True,
                max_length=80,
                null=True,
                verbose_name="序列号",
            ),
        ),
    ]
