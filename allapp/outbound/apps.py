from django.apps import AppConfig


class OutboundConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'allapp.outbound'
    verbose_name = "出库管理"           # 左侧一级菜单中文


