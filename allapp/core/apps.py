from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'allapp.core'
    verbose_name = "核心"

    def ready(self):
        # 导入以触发补丁
        from . import admin_order  # noqa
