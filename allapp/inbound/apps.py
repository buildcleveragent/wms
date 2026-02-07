from django.apps import AppConfig


class InboundConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'allapp.inbound'
    verbose_name = "入库管理"           # 左侧一级菜单中文
