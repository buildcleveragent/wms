import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("pos", "0003_pos_shift_print_export_foundation"),
    ]

    operations = [
        migrations.AddField(
            model_name="posprintlog",
            name="source",
            field=models.CharField(
                choices=[
                    ("FRONTEND_HTML", "前端HTML"),
                    ("BACKEND_HTML", "后端HTML"),
                ],
                db_index=True,
                default="BACKEND_HTML",
                max_length=30,
                verbose_name="打印来源",
            ),
        ),
        migrations.AddField(
            model_name="posshift",
            name="reopen_count",
            field=models.PositiveIntegerField(default=0, verbose_name="重开次数"),
        ),
        migrations.AddField(
            model_name="posshift",
            name="reopen_reason",
            field=models.CharField(
                blank=True, default="", max_length=200, verbose_name="重开原因"
            ),
        ),
        migrations.AddField(
            model_name="posshift",
            name="reopened_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="重开时间"),
        ),
        migrations.AddField(
            model_name="posshift",
            name="reopened_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="reopened_pos_shifts",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
