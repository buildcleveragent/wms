from django.apps import AppConfig

class ProductsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'allapp.products'
    verbose_name = "商品管理"           # 左侧一级菜单中文
