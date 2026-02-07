from django.apps import AppConfig


class LocationsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'allapp.locations'
    verbose_name = "仓位管理"           # 左侧一级菜单中文
