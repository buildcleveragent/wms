"""Validate GS1 schema and secret configuration without calling the provider."""

from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.db.migrations.recorder import MigrationRecorder

from allapp.core.models import SecretSettingError, SystemSetting


class Command(BaseCommand):
    help = "只读检查 GS1 数据库迁移、数据表和加密 API Key。"

    def handle(self, *args, **options):
        errors = []
        migration_applied = MigrationRecorder.Migration.objects.filter(
            app="products",
            name="0012_gs1_lookup_cache_and_sku_format",
        ).exists()
        self.stdout.write(f"products.0012 migration={'ok' if migration_applied else 'missing'}")
        if not migration_applied:
            errors.append("products.0012 数据库迁移未应用")

        tables = set(connection.introspection.table_names())
        required_tables = (
            "products_gs1lookupcache",
            "products_gs1providerratelimit",
        )
        missing_tables = [name for name in required_tables if name not in tables]
        self.stdout.write(
            "gs1_tables=" + ("ok" if not missing_tables else ",".join(missing_tables))
        )
        if missing_tables:
            errors.append("GS1 数据表缺失")

        try:
            api_key = SystemSetting.get_secret_value(
                SystemSetting.INTEGRATION_NAMESPACE,
                SystemSetting.APIZERO_GS1_API_KEY,
                "",
            )
        except SecretSettingError as exc:
            api_key = ""
            errors.append(str(exc))
        self.stdout.write(f"api_key={'configured' if api_key else 'missing'}")
        if not api_key and not any("密钥" in error for error in errors):
            errors.append("ApiZero GS1 API Key 尚未配置")

        if errors:
            raise CommandError("；".join(errors))
        self.stdout.write(self.style.SUCCESS("GS1 配置检查通过。"))
