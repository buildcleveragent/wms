
# apps/accounts/apps.py

from django.apps import AppConfig

class AccountsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'allapp.accounts'
    verbose_name = "账号管理"

