from django.apps import AppConfig


class ReportsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'allapp.reports'
    verbose_name = "报表管理"           # 左侧一级菜单中文
