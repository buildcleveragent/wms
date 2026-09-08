"""Fail-closed identity checks for the canonical automated MySQL database."""

import os

import MySQLdb

TEST_DATABASE = "wms_db_test"


def validate_test_database(config):
    """Reject an unsafe resolved Django database configuration before connecting."""
    if config.get("ENGINE") != "django.db.backends.mysql":
        raise RuntimeError("Automated database tests require MySQL 8.")
    if config.get("NAME") != TEST_DATABASE or config.get("TEST", {}).get("NAME") != TEST_DATABASE:
        raise RuntimeError("Both database names must be exactly wms_db_test.")


def verify_test_database(config):
    """Check actual server/database identity with a bounded, read-only connection."""
    validate_test_database(config)
    connection = MySQLdb.connect(
        host=config["HOST"],
        port=int(config["PORT"] or 3306),
        user=config["USER"],
        passwd=config["PASSWORD"],
        db=TEST_DATABASE,
        connect_timeout=5,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT DATABASE(), VERSION()")
            name, version = cursor.fetchone()
        if name != TEST_DATABASE or not version.startswith("8."):
            raise RuntimeError("Connected database must be wms_db_test on MySQL 8.")
        return {"database": name, "mysql_version": version}
    finally:
        connection.close()


def validate_test_process(config):
    """Check process identity as well as resolved settings, without exposing secrets."""
    if os.environ.get("APP_ENV") != "test":
        raise RuntimeError("APP_ENV must be test for automated database operations.")
    validate_test_database(config)
