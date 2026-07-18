
# apps/accounts/apps.py

from django.apps import AppConfig

class AccountsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'allapp.accounts'
    verbose_name = "账号管理"

    def ready(self):
        # Import only after Django has populated all app models.
        from . import signals  # noqa: F401
