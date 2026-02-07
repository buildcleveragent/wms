from django.apps import AppConfig


class StrategiesConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'allapp.strategies'
    verbose_name = "策略管理"           # 左侧一级菜单中文
