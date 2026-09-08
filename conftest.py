"""Repository-wide database safety; business concurrency stays inside each test."""

import os

import pytest

from wmsmaster.test_evidence import TestEvidence

_evidence = None
_database_lease = None


def pytest_addoption(parser):
    parser.addoption("--wms-group", choices=("all", "functional", "migrations"), default="all")
    parser.addoption(
        "--fast-password-hashers",
        action="store_true",
        help=(
            "Use a fast password hasher only in the validated test process, "
            "not for performance evidence."
        ),
    )


def pytest_collection_finish(session):
    _evidence.collection(item.nodeid for item in session.items)


def pytest_collection_modifyitems(config, items):
    """Partition the actual collection; new tests cannot fall through a static app list."""
    groups = {"functional": [], "migrations": []}
    for item in items:
        group = "migrations" if item.get_closest_marker("migrations") else "functional"
        groups[group].append(item)
    nodes = [item.nodeid for item in items]
    if len(nodes) != len(set(nodes)):
        raise pytest.UsageError("Duplicate test identities in the collected suite")
    _evidence.append(
        "partition", groups={key: [item.nodeid for item in group] for key, group in groups.items()}
    )
    selected = config.getoption("wms_group")
    if selected != "all":
        config.hook.pytest_deselected(
            items=[item for key, group in groups.items() if key != selected for item in group]
        )
        items[:] = groups[selected]


def pytest_runtest_logreport(report):
    _evidence.report(report)


def pytest_runtest_setup(item):
    if _database_lease is not None and _database_lease.lost.is_set():
        pytest.exit("Database coordination was lost; this execution cannot continue.", returncode=2)


def pytest_sessionfinish(session, exitstatus):
    if _evidence is None:
        return
    from django.conf import settings

    result = _evidence.finish(
        int(exitstatus),
        collect_only=session.config.option.collectonly,
        password_hasher=settings.PASSWORD_HASHERS[0],
    )
    if exitstatus == 0 and result["status"] not in {"PASS", "COLLECTED"}:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


def pytest_configure(config):
    global _evidence
    from django.conf import settings

    from wmsmaster.test_database import validate_test_process

    try:
        validate_test_process(settings.DATABASES["default"])
        if config.getoption("numprocesses", default=None) not in (None, 0, "0"):
            raise RuntimeError("xdist creates noncanonical databases; run pytest serially.")
        if config.getoption("nomigrations", default=False):
            raise RuntimeError("Tests must exercise real migrations.")
    except RuntimeError as exc:
        raise pytest.UsageError(str(exc)) from None
    try:
        _evidence = TestEvidence(os.environ.get("WMS_TEST_MANIFEST"))
        os.environ.setdefault("WMS_TEST_RUN_ID", _evidence.run_id)
    except (OSError, RuntimeError) as exc:
        raise pytest.UsageError(str(exc)) from None
    if config.getoption("fast_password_hashers"):
        from django.test.utils import override_settings

        config._wms_password_override = override_settings(
            PASSWORD_HASHERS=[
                "django.contrib.auth.hashers.MD5PasswordHasher",
                *settings.PASSWORD_HASHERS,
            ]
        )
        config._wms_password_override.enable()


def pytest_unconfigure(config):
    override = getattr(config, "_wms_password_override", None)
    if override is not None:
        override.disable()


@pytest.fixture(scope="session")
def django_db_modify_db_settings(django_db_modify_db_settings):
    """Verify the database before fixtures can create, flush, or migrate it."""
    global _database_lease
    from django.conf import settings
    from django.db import connections

    from wmsmaster.test_coordination import DatabaseLease
    from wmsmaster.test_database import verify_test_database

    with DatabaseLease(settings.DATABASES["default"]) as lease:
        _database_lease = lease
        try:
            verify_test_database(settings.DATABASES["default"])
            yield
        finally:
            connections.close_all()
            _database_lease = None
