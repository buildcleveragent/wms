"""Safety-focused service for purging volatile WMS business data.

The manifest in this module is intentionally explicit.  Adding a managed model
without classifying it as preserved or purged makes the preflight fail closed.
"""

from __future__ import annotations

import hashlib
from contextlib import contextmanager
from dataclasses import dataclass

from django.apps import apps as django_apps
from django.db import connections, transaction

from allapp.accounts.audit import record_audit_event

PURGE_MANIFEST_VERSION = "2026-08-08.1"


PRESERVED_MODEL_LABELS = frozenset(
    {
        # Django schema, authorization, and audit state.
        "accounts.auditevent",
        "accounts.systemlog",
        "accounts.user",
        "accounts.user_groups",
        "accounts.user_user_permissions",
        "accounts.userrolescope",
        "admin.logentry",
        "auth.group",
        "auth.group_permissions",
        "auth.permission",
        "contenttypes.contenttype",
        # Core configuration and document numbering.
        "core.docsequence",
        "core.printconfig",
        "core.systemsetting",
        # Stable tenant and master data.
        "baseinfo.carriercompany",
        "baseinfo.customer",
        "baseinfo.dictcategory",
        "baseinfo.dictitem",
        "baseinfo.driver",
        "baseinfo.employee",
        "baseinfo.owner",
        "baseinfo.ownerwarehousebinding",
        "baseinfo.route",
        "baseinfo.supplier",
        "baseinfo.vehicle",
        "locations.container",
        "locations.location",
        "locations.location_product_categories",
        "locations.subwarehouse",
        "locations.warehouse",
        # Reusable product dictionaries; product records themselves are purged.
        "products.brand",
        "products.productcategory",
        "products.productuom",
        "products.temperaturezone",
        # Reusable billing and strategy definitions.
        "billing.billingrule",
        "billing.billingruletier",
        "billing.billingservicecontract",
        "strategies.strategy",
        "strategies.strategycategory",
        "strategies.strategylog",
        "strategies.strategyparameter",
        "strategies.strategytemplate",
        # Stable POS, sales, device, and report configuration.
        "pos.poscustomer",
        "pos.posreceiptwarehouseinfo",
        "salesapp.bizorg",
        "salesapp.channel",
        "salesapp.creditpolicy",
        "salesapp.customerchannel",
        "salesapp.minicustomeraddress",
        "salesapp.miniprogramuser",
        "salesapp.phototype",
        "salesapp.pricegroup",
        "salesapp.pricelist",
        "salesapp.saleminibanner",
        "salesapp.saleminicoupontemplate",
        "salesapp.salesperson",
        "driverapp.driverdevice",
        "driverapp.trackingdevice",
        "reports.datedim",
        "reports.operatingtarget",
        "reports.reasondim",
        "reports.tempzonedim",
    }
)


PURGED_MODEL_LABELS = frozenset(
    {
        # Login state.  JWTs remain valid until their configured expiry.
        "accounts.loginthrottlecacheentry",
        "authtoken.token",
        "sessions.session",
        "token_blacklist.blacklistedtoken",
        "token_blacklist.outstandingtoken",
        # Product master and packaging.
        "products.product",
        "products.productbarcode",
        "products.productexternalidentifier",
        "products.productidentifierregistry",
        "products.productpackage",
        # Inbound.
        "inbound.inboundorder",
        "inbound.inboundorderline",
        "inbound.inboundorderreturninfo",
        "inbound.inboundreceipt",
        "inbound.inboundreceiptline",
        "inbound.lot",
        "inbound.lotwarehouse",
        "inbound.noorderreceiverequest",
        "inbound.returninspection",
        # Inventory.
        "inventory.inventorydetail",
        "inventory.inventorycostadjustment",
        "inventory.inventorycostlayer",
        "inventory.inventorylayermovement",
        "inventory.inventorylayerposition",
        "inventory.inventorysnapshotdaily",
        "inventory.inventorysummary",
        "inventory.inventorytransaction",
        "inventory.postingjournal",
        "inventory.reviewdifference",
        "inventory.reviewdifferenceline",
        # Outbound.
        "outbound.orderlinesourcelink",
        "outbound.outboundorder",
        "outbound.outboundorderextra",
        "outbound.outboundorderline",
        # Task execution and scans.
        "tasking.adjustlineextra",
        "tasking.adjusttaskextra",
        "tasking.containerusage",
        "tasking.countlineextra",
        "tasking.countscopelock",
        "tasking.counttaskextra",
        "tasking.dispatchlineextra",
        "tasking.dispatchtaskextra",
        "tasking.loadlineextra",
        "tasking.loadtaskextra",
        "tasking.packlineextra",
        "tasking.packtaskextra",
        "tasking.picklineextra",
        "tasking.picktaskextra",
        "tasking.putawaylineextra",
        "tasking.putawaytaskextra",
        "tasking.qclineextra",
        "tasking.qctaskextra",
        "tasking.receivelineextra",
        "tasking.receivetaskextra",
        "tasking.reloclineextra",
        "tasking.relocationrequest",
        "tasking.relocationrequestline",
        "tasking.relocationreservation",
        "tasking.reloctaskextra",
        "tasking.replenishmentpolicy",
        "tasking.replenishmentrequest",
        "tasking.replenishlineextra",
        "tasking.replenishtaskextra",
        "tasking.reviewlineextra",
        "tasking.reviewtaskextra",
        "tasking.taskassignment",
        "tasking.taskscanlog",
        "tasking.taskstatuslog",
        "tasking.wmstask",
        "tasking.wmstaskline",
        # Billing operations and settlement.
        "billing.bill",
        "billing.billingaccrual",
        "billing.billingevent",
        "billing.billingjobrun",
        "billing.billingmetricdaily",
        "billing.billingperiod",
        "billing.billline",
        "billing.collectionactivity",
        "billing.paymentallocation",
        "billing.paymentreceipt",
        "billing.receivablecollectioncase",
        # POS operations.
        "pos.posauditlog",
        "pos.poscustomerrepayment",
        "pos.pospayment",
        "pos.pospaymentline",
        "pos.posprintlog",
        "pos.posrefund",
        "pos.posreturn",
        "pos.posreturnline",
        "pos.possale",
        "pos.possaleline",
        "pos.possaleorder",
        "pos.posshift",
        "pos.posshiftpaymentsummary",
        # Sales catalog links and business history.
        "salesapp.arledger",
        "salesapp.attendancerecord",
        "salesapp.channelproductpolicy",
        "salesapp.customerproductpolicy",
        "salesapp.customerspecialprice",
        "salesapp.expenseadvance",
        "salesapp.expensewriteoff",
        "salesapp.gpstrackpoint",
        "salesapp.merchandisingagreement",
        "salesapp.merchandisingaudit",
        "salesapp.merchandisingplan",
        "salesapp.priceitem",
        "salesapp.pricememory",
        "salesapp.promotion",
        "salesapp.promotiondiscountstep",
        "salesapp.promotiongiftitem",
        "salesapp.promotionspecialprice",
        "salesapp.rebatepayout",
        "salesapp.saleminiaftersalerequest",
        "salesapp.saleminicart",
        "salesapp.saleminicartitem",
        "salesapp.saleminicoupon",
        "salesapp.saleminidistributionrecord",
        "salesapp.saleminiorderadjustment",
        "salesapp.saleminiordermapping",
        "salesapp.saleminipayment",
        "salesapp.saleminipaymentevent",
        "salesapp.saleminipointledger",
        "salesapp.saleminiproductreview",
        "salesapp.saleminiproductreviewimage",
        "salesapp.saleminirefund",
        "salesapp.saleproductconfig",
        "salesapp.salesorder",
        "salesapp.salesorderline",
        "salesapp.visitphoto",
        "salesapp.visitplan",
        "salesapp.visitrecord",
        # Delivery operations; device masters are preserved.
        "driverapp.deliverypresign",
        "driverapp.deliverytask",
        "driverapp.drivershift",
        "driverapp.exceptionreport",
        "driverapp.taskstop",
        "driverapp.trackingpoint",
        # Reporting warehouse and ETL state.
        "reports.aggbillingdaily",
        "reports.agginventoryaging",
        "reports.aggotifdaily",
        "reports.aggthroughputdaily",
        "reports.carrierdim",
        "reports.customerdim",
        "reports.dedupledger",
        "reports.etljobrun",
        "reports.etlwatermark",
        "reports.alertcase",
        "reports.alertcasehistory",
        "reports.businessreviewsnapshot",
        "reports.factbilling",
        "reports.factinboundline",
        "reports.factinventorysnapshotdaily",
        "reports.factinventorytxn",
        "reports.factoutboundline",
        "reports.factoutboundordersla",
        "reports.ownerdim",
        "reports.productdim",
        "reports.reportsnapshot",
        "reports.supplierdim",
        "reports.taskstatesnapshotdaily",
        "reports.warehousedim",
        # Assignments can contain logical IDs for records purged above.
        "strategies.strategyassignment",
    }
)


EXTRA_PRESERVED_TABLES = frozenset({"django_migrations"})


class PurgeConfigurationError(RuntimeError):
    """The checked-in manifest does not classify the current model registry."""


class PurgeBlockedError(RuntimeError):
    """Database preflight found state that makes the purge unsafe."""


class PurgeLockError(RuntimeError):
    """Another purge command already owns the database advisory lock."""


@dataclass(frozen=True)
class ForeignKeyBlocker:
    child_table: str
    parent_table: str
    constraint_name: str


@dataclass(frozen=True)
class ResolvedManifest:
    preserved_tables: frozenset[str]
    purged_tables: frozenset[str]
    label_to_table: dict[str, str]

    @property
    def classified_tables(self) -> frozenset[str]:
        return self.preserved_tables | self.purged_tables


@dataclass(frozen=True)
class PurgePreflightReport:
    database_alias: str
    target: str
    manifest: ResolvedManifest
    actual_tables: frozenset[str]
    present_preserved_tables: frozenset[str]
    present_purged_tables: frozenset[str]
    missing_preserved_tables: frozenset[str]
    missing_purged_tables: frozenset[str]
    unknown_tables: frozenset[str]
    foreign_key_blockers: tuple[ForeignKeyBlocker, ...]
    estimated_rows: dict[str, int | None]

    @property
    def blocking_messages(self) -> tuple[str, ...]:
        messages = [f"未分类数据库表: {table}" for table in sorted(self.unknown_tables)]
        messages.extend(
            (
                f"外键阻塞: {item.child_table} -> {item.parent_table} "
                f"({item.constraint_name})"
            )
            for item in self.foreign_key_blockers
        )
        return tuple(messages)


def canonical_target(connection) -> str:
    """Return the exact confirmation string for a database connection."""

    config = connection.settings_dict
    host = str(config.get("HOST") or "localhost")
    port = str(config.get("PORT") or "3306")
    name = str(config.get("NAME") or "")
    return f"{host}:{port}/{name}"


def resolve_manifest(apps_registry=django_apps) -> ResolvedManifest:
    """Resolve model labels to tables and fail when classification drifts."""

    overlap = PRESERVED_MODEL_LABELS & PURGED_MODEL_LABELS
    if overlap:
        raise PurgeConfigurationError(
            "模型同时出现在保留和清理清单: " + "、".join(sorted(overlap))
        )

    models_by_label = {
        model._meta.label_lower: model
        for model in apps_registry.get_models(include_auto_created=True)
        if model._meta.managed and not model._meta.proxy
    }
    registered = frozenset(models_by_label)
    declared = PRESERVED_MODEL_LABELS | PURGED_MODEL_LABELS
    unclassified = registered - declared
    stale = declared - registered
    errors = []
    if unclassified:
        errors.append("未分类模型: " + "、".join(sorted(unclassified)))
    if stale:
        errors.append("清单包含不存在模型: " + "、".join(sorted(stale)))
    if errors:
        raise PurgeConfigurationError("；".join(errors))

    label_to_table = {
        label: models_by_label[label]._meta.db_table for label in sorted(registered)
    }
    preserved_tables = frozenset(
        {label_to_table[label] for label in PRESERVED_MODEL_LABELS}
        | set(EXTRA_PRESERVED_TABLES)
    )
    purged_tables = frozenset(label_to_table[label] for label in PURGED_MODEL_LABELS)
    table_overlap = preserved_tables & purged_tables
    if table_overlap:
        raise PurgeConfigurationError(
            "数据库表同时出现在保留和清理清单: " + "、".join(sorted(table_overlap))
        )
    return ResolvedManifest(
        preserved_tables=preserved_tables,
        purged_tables=purged_tables,
        label_to_table=label_to_table,
    )


def _actual_tables(connection) -> frozenset[str]:
    with connection.cursor() as cursor:
        table_info = connection.introspection.get_table_list(cursor)
    return frozenset(item.name for item in table_info if item.type in {"t", "p"})


def _foreign_key_blockers(
    connection,
    *,
    actual_tables: frozenset[str],
    purged_tables: frozenset[str],
) -> tuple[ForeignKeyBlocker, ...]:
    query = (
        "SELECT TABLE_NAME, REFERENCED_TABLE_NAME, CONSTRAINT_NAME "
        "FROM information_schema.KEY_COLUMN_USAGE "
        "WHERE CONSTRAINT_SCHEMA = DATABASE() "
        "AND REFERENCED_TABLE_NAME IS NOT NULL"
    )
    with connection.cursor() as cursor:
        cursor.execute(query)
        foreign_keys = cursor.fetchall()
    return tuple(
        ForeignKeyBlocker(
            child_table=str(child_table),
            parent_table=str(parent_table),
            constraint_name=str(constraint_name),
        )
        for child_table, parent_table, constraint_name in foreign_keys
        if child_table in actual_tables
        and parent_table in purged_tables
        and child_table not in purged_tables
    )


def _estimated_rows(connection) -> dict[str, int | None]:
    query = (
        "SELECT TABLE_NAME, TABLE_ROWS FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA = DATABASE()"
    )
    with connection.cursor() as cursor:
        cursor.execute(query)
        return {
            str(table): (int(rows) if rows is not None else None)
            for table, rows in cursor.fetchall()
        }


def prepare_purge(database_alias: str = "default") -> PurgePreflightReport:
    """Inspect the selected database without mutating it."""

    connection = connections[database_alias]
    if connection.vendor != "mysql":
        raise PurgeConfigurationError("purge_business_data 仅支持 MySQL。")

    manifest = resolve_manifest()
    actual_tables = _actual_tables(connection)
    present_preserved = manifest.preserved_tables & actual_tables
    present_purged = manifest.purged_tables & actual_tables
    unknown_tables = actual_tables - manifest.classified_tables
    blockers = _foreign_key_blockers(
        connection,
        actual_tables=actual_tables,
        purged_tables=present_purged,
    )
    return PurgePreflightReport(
        database_alias=database_alias,
        target=canonical_target(connection),
        manifest=manifest,
        actual_tables=actual_tables,
        present_preserved_tables=frozenset(present_preserved),
        present_purged_tables=frozenset(present_purged),
        missing_preserved_tables=frozenset(manifest.preserved_tables - actual_tables),
        missing_purged_tables=frozenset(manifest.purged_tables - actual_tables),
        unknown_tables=frozenset(unknown_tables),
        foreign_key_blockers=blockers,
        estimated_rows=_estimated_rows(connection),
    )


def ensure_preflight_allows_execution(report: PurgePreflightReport) -> None:
    if report.blocking_messages:
        raise PurgeBlockedError("；".join(report.blocking_messages))


def _lock_name(target: str) -> str:
    target_hash = hashlib.sha256(target.encode("utf-8")).hexdigest()[:24]
    return f"wms:purge-business-data:{target_hash}"


@contextmanager
def acquire_database_maintenance_lock(database_alias: str, target: str):
    """Serialize destructive or snapshot-style maintenance for one database."""

    connection = connections[database_alias]
    lock_name = _lock_name(target)
    with connection.cursor() as cursor:
        cursor.execute("SELECT GET_LOCK(%s, 0)", [lock_name])
        acquired = cursor.fetchone()[0]
    if acquired != 1:
        raise PurgeLockError("已有数据库维护任务正在运行，未取得数据库命名锁。")
    try:
        yield lock_name
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SELECT RELEASE_LOCK(%s)", [lock_name])


@contextmanager
def acquire_purge_lock(database_alias: str, target: str):
    """Backward-compatible purge wrapper around the shared maintenance lock."""

    try:
        with acquire_database_maintenance_lock(database_alias, target) as lock_name:
            yield lock_name
    except PurgeLockError as exc:
        raise PurgeLockError("已有清理任务正在运行，未取得数据库命名锁。") from exc


def _delete_table(cursor, quoted_table: str) -> int:
    cursor.execute(f"DELETE FROM {quoted_table}")
    return max(cursor.rowcount, 0)


def execute_purge(
    report: PurgePreflightReport,
    *,
    operator,
    backup_reference: str,
) -> dict[str, int]:
    """Delete all present purge tables atomically and write the success audit."""

    ensure_preflight_allows_execution(report)
    alias = report.database_alias
    connection = connections[alias]
    deleted_counts: dict[str, int] = {}

    with transaction.atomic(using=alias):
        with connection.cursor() as cursor:
            cursor.execute("SELECT @@SESSION.FOREIGN_KEY_CHECKS")
            original_fk_checks = int(cursor.fetchone()[0])
            try:
                cursor.execute("SET SESSION FOREIGN_KEY_CHECKS = 0")
                for table in sorted(report.present_purged_tables):
                    quoted_table = connection.ops.quote_name(table)
                    deleted_counts[table] = _delete_table(cursor, quoted_table)

                nonempty_tables = []
                for table in sorted(report.present_purged_tables):
                    quoted_table = connection.ops.quote_name(table)
                    cursor.execute(f"SELECT COUNT(*) FROM {quoted_table}")
                    if int(cursor.fetchone()[0]):
                        nonempty_tables.append(table)
                if nonempty_tables:
                    raise PurgeBlockedError(
                        "清理后仍有数据: " + "、".join(nonempty_tables)
                    )
            finally:
                cursor.execute(f"SET SESSION FOREIGN_KEY_CHECKS = {original_fk_checks}")

        record_audit_event(
            action="BUSINESS_DATA_PURGE",
            module="core.operations",
            user=operator,
            succeeded=True,
            before={
                "estimated_rows": {
                    table: report.estimated_rows.get(table)
                    for table in sorted(report.present_purged_tables)
                }
            },
            after={"deleted_rows": deleted_counts},
            metadata={
                "source": "purge_business_data",
                "database_alias": alias,
                "target": report.target,
                "backup_reference": backup_reference,
                "manifest_version": PURGE_MANIFEST_VERSION,
            },
            using=alias,
        )

    return deleted_counts
