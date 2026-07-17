from django.db import migrations, models


TASK_STATUSES = [
    "RESERVED",
    "DRAFT",
    "READY",
    "RELEASED",
    "IN_PROGRESS",
    "COMPLETED",
    "CANCELLED",
]


class Migration(migrations.Migration):
    dependencies = [
        ("tasking", "0001_initial"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="taskstatuslog",
            name="ck_tlog_old_in_set",
        ),
        migrations.RemoveConstraint(
            model_name="taskstatuslog",
            name="ck_tlog_new_in_set",
        ),
        migrations.AddConstraint(
            model_name="taskstatuslog",
            constraint=models.CheckConstraint(
                condition=models.Q(old_status__in=TASK_STATUSES),
                name="ck_tlog_old_in_set",
            ),
        ),
        migrations.AddConstraint(
            model_name="taskstatuslog",
            constraint=models.CheckConstraint(
                condition=models.Q(new_status__in=TASK_STATUSES),
                name="ck_tlog_new_in_set",
            ),
        ),
    ]
