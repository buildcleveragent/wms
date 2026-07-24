from django.apps import AppConfig


class SalesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "allapp.salesapp"
    verbose_name = "商城销售管理"

    def ready(self):
        from . import checks  # noqa: F401
