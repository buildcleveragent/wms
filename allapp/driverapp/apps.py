# allapp/driverapp/apps.py
from django.apps import AppConfig

class DriverappConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'allapp.driverapp'   # ← 必须是自己的包路径
