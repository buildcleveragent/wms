from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tasking", "0003_wmstask_assisted_source_index")]

    operations = [
        migrations.AddIndex(
            model_name="wmstask",
            index=models.Index(
                fields=["task_type", "status", "owner", "finished_at", "id"],
                name="ix_task_tt_st_own_fin",
            ),
        ),
        migrations.AddIndex(
            model_name="wmstask",
            index=models.Index(
                fields=["task_type", "status", "warehouse", "finished_at", "id"],
                name="ix_task_tt_st_wh_fin",
            ),
        ),
    ]
