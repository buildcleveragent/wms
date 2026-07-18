import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0003_userrolescope"),
        ("baseinfo", "0002_initial"),
        ("locations", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="AuditEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("occurred_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("username", models.CharField(blank=True, db_index=True, default="", max_length=150)),
                ("action", models.CharField(db_index=True, max_length=40)),
                ("module", models.CharField(db_index=True, max_length=80)),
                ("object_type", models.CharField(blank=True, default="", max_length=100)),
                ("object_id", models.CharField(blank=True, default="", max_length=80)),
                ("request_id", models.CharField(blank=True, db_index=True, default="", max_length=64)),
                ("ip_address", models.GenericIPAddressField(blank=True, null=True)),
                ("method", models.CharField(blank=True, default="", max_length=12)),
                ("path", models.CharField(blank=True, default="", max_length=500)),
                ("succeeded", models.BooleanField(db_index=True, default=True)),
                ("before", models.JSONField(blank=True, default=dict)),
                ("after", models.JSONField(blank=True, default=dict)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("event_hash", models.CharField(editable=False, max_length=64, unique=True)),
                ("actor", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="audit_events", to=settings.AUTH_USER_MODEL)),
                ("owner", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to="baseinfo.owner")),
                ("warehouse", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to="locations.warehouse")),
            ],
            options={
                "verbose_name": "不可变审计事件",
                "verbose_name_plural": "不可变审计事件",
                "ordering": ("-occurred_at", "-id"),
                "indexes": [
                    models.Index(fields=["module", "action", "occurred_at"], name="idx_audit_mod_action_time"),
                    models.Index(fields=["owner", "occurred_at"], name="idx_audit_owner_time"),
                    models.Index(fields=["warehouse", "occurred_at"], name="idx_audit_wh_time"),
                    models.Index(fields=["object_type", "object_id"], name="idx_audit_object"),
                ],
            },
        )
    ]
