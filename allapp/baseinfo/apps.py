from django.apps import AppConfig


class BaseinfoConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'allapp.baseinfo'
    verbose_name = "基础信息"           # 左侧一级菜单中文
