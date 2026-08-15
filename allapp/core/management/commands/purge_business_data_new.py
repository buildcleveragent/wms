"""Purge volatile WMS data together with product master records."""

from django.core.management.base import CommandError
from django.utils.connection import ConnectionDoesNotExist

from allapp.core.business_data_purge import (
    PURGE_BUSINESS_DATA_NEW_MANIFEST_VERSION,
    PurgeConfigurationError,
    prepare_new_purge,
)

from .purge_business_data import Command as StandardPurgeCommand


class Command(StandardPurgeCommand):
    help = (
        "清空易变业务数据和商品档案，将货主 SKU 序号重置为 1，"
        "保留用户权限、基础字典、配置、表结构和迁移记录。"
    )
    command_name = "purge_business_data_new"
    manifest_version = PURGE_BUSINESS_DATA_NEW_MANIFEST_VERSION
    audit_action = "BUSINESS_DATA_PURGE_NEW"
    audit_source = "purge_business_data_new"
    reset_owner_sku_sequences = True

    def _prepare(self, alias):
        try:
            return prepare_new_purge(alias)
        except (PurgeConfigurationError, ConnectionDoesNotExist) as exc:
            raise CommandError(str(exc)) from exc
