from django.apps import AppConfig


class ReportsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "allapp.reports"
    verbose_name = "报表管理"  # 左侧一级菜单中文

    def ready(self):
        from django.db.models.signals import post_delete

        from .models import OwnerArchiveJob
        from .owner_archive_jobs import discard_published_file

        post_delete.connect(
            discard_published_file,
            sender=OwnerArchiveJob,
            dispatch_uid="reports.delete_owner_archive",
        )
