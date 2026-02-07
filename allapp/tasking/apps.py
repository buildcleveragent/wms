from django.apps import AppConfig

class TaskingConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'allapp.tasking'
    verbose_name = "任务管理"           # 左侧一级菜单中文
