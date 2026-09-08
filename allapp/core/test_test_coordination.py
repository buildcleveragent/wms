"""Coordinator fault tests, including real MySQL lock loss without schema writes."""

import json
import os
import subprocess
import sys
import time
from copy import deepcopy
from pathlib import Path

import pytest

from wmsmaster.test_coordination import (
    CoordinationBlocked,
    DatabaseLease,
    process_identity,
    process_is_alive,
    secure_directory,
    secure_file_lock,
)


class PrivateTestConfig(dict):
    def __repr__(self):
        return "<isolated test configuration; credentials redacted>"


@pytest.fixture
def isolated_coordination_config(settings):
    import MySQLdb

    from scripts.isolated_test_mysql import isolated_test_mysql
    from wmsmaster.test_database import verify_test_database

    with isolated_test_mysql() as environment:
        config = deepcopy(settings.DATABASES["default"])
        for field in ("HOST", "PORT", "USER", "PASSWORD"):
            config[field] = environment[f"DB_{field}"]
        verify_test_database(config)
        connection = MySQLdb.connect(
            host=config["HOST"],
            port=int(config["PORT"]),
            user=config["USER"],
            passwd=config["PASSWORD"],
            db="wms_db_test",
            connect_timeout=5,
        )
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT DATABASE()")
                assert cursor.fetchone()[0] == "wms_db_test"
                cursor.execute("CREATE TABLE coordination_probe (id INT PRIMARY KEY) ENGINE=InnoDB")
                cursor.execute("INSERT INTO coordination_probe VALUES (1)")
            connection.commit()
        finally:
            connection.close()
        yield PrivateTestConfig(config), PrivateTestConfig(environment)


@pytest.mark.unit
def test_clean_sql_lease_still_waits_for_previous_runner_container(monkeypatch):
    lease = object.__new__(DatabaseLease)
    monkeypatch.setattr(lease, "_containers", lambda record: ["old-runner"])
    monkeypatch.setattr(lease, "_database_idle", lambda: True)
    assert not lease._previous_finished({"state": "CLEAN"})
    monkeypatch.setattr(lease, "_containers", lambda record: [])
    assert lease._previous_finished({"state": "CLEAN"})


@pytest.mark.unit
def test_stable_lock_is_exclusive_and_not_deleted(tmp_path):
    path = tmp_path / "resource.lock"
    with secure_file_lock(path):
        original = path.stat().st_ino
        with pytest.raises(CoordinationBlocked):
            with secure_file_lock(path):
                pytest.fail("Both callers acquired one resource")
    with secure_file_lock(path):
        assert path.stat().st_ino == original


@pytest.mark.unit
@pytest.mark.parametrize("kind", ["symlink", "dangling", "fifo", "hardlink", "public"])
def test_unsafe_lock_objects_rejected_without_modifying_target(tmp_path, kind):
    target = tmp_path / "target"
    target.write_text("untouched")
    path = tmp_path / "resource.lock"
    if kind == "symlink":
        path.symlink_to(target)
    elif kind == "dangling":
        path.symlink_to(tmp_path / "absent")
    elif kind == "fifo":
        os.mkfifo(path)
    elif kind == "hardlink":
        path.hardlink_to(target)
    else:
        path.touch(mode=0o666)
        path.chmod(0o666)
    with pytest.raises((OSError, CoordinationBlocked)):
        with secure_file_lock(path):
            pytest.fail("Unsafe lock accepted")
    assert target.read_text() == "untouched"


@pytest.mark.unit
def test_coordination_directory_cannot_be_aliased_or_public(tmp_path):
    directory = tmp_path / "real"
    directory.mkdir(mode=0o700)
    alias = tmp_path / "alias"
    alias.symlink_to(directory, target_is_directory=True)
    with pytest.raises(CoordinationBlocked):
        secure_directory(alias)
    directory.chmod(0o777)
    with pytest.raises(CoordinationBlocked):
        secure_directory(directory)


@pytest.mark.unit
def test_pid_alone_cannot_prove_process_identity():
    identity = process_identity()
    assert process_is_alive(identity)
    assert not process_is_alive({**identity, "start_time": "different"})
    assert not process_is_alive({**identity, "boot_id": "different"})
    with pytest.raises(CoordinationBlocked, match="PID namespace"):
        process_is_alive({**identity, "pid_namespace": "unobservable"})


@pytest.mark.unit
@pytest.mark.parametrize("contents", ["{", "{}", '{"state":"CLEAN"}'])
def test_corrupt_record_is_not_interpreted_as_clean(tmp_path, settings, contents):
    lease = DatabaseLease(settings.DATABASES["default"], directory=tmp_path)
    lease.server_uuid = "synthetic"
    lease.path = tmp_path / "state.json"
    lease.path.write_text(contents)
    with pytest.raises(CoordinationBlocked, match="Corrupt"):
        lease._read_record()


@pytest.mark.unit
def test_dangling_run_record_is_rejected(tmp_path, settings):
    lease = DatabaseLease(settings.DATABASES["default"], directory=tmp_path)
    lease.path = tmp_path / "state.json"
    lease.path.symlink_to(tmp_path / "missing")
    with pytest.raises(CoordinationBlocked, match="Unsafe"):
        lease._read_record()


@pytest.mark.integration
def test_lock_loss_never_allows_new_run_to_overtake_live_worker(
    tmp_path, isolated_coordination_config
):
    """A stays alive after its lock session is killed; B must do no work."""
    import MySQLdb

    from wmsmaster.test_database import verify_test_database

    config, environment = isolated_coordination_config
    verify_test_database(config)
    # The fault uses its own MySQL instance, even when the full suite owns the main lease.
    admin = MySQLdb.connect(
        host=config["HOST"],
        port=int(config["PORT"]),
        user=config["USER"],
        passwd=config["PASSWORD"],
        connect_timeout=5,
        autocommit=True,
    )
    directory = tmp_path / "coordination"
    directory.mkdir(mode=0o700)
    script = tmp_path / "owner.py"
    script.write_text(
        "import json, os, time, django\n"
        "from pathlib import Path\n"
        "os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'wmsmaster.settings')\n"
        "django.setup()\n"
        "from django.conf import settings\n"
        "from wmsmaster.test_coordination import DatabaseLease\n"
        "with DatabaseLease(settings.DATABASES['default'], wait_seconds=0, "
        "on_lost=lambda: None) as lease:\n"
        "    import MySQLdb\n"
        "    cfg=settings.DATABASES['default']\n"
        "    worker=MySQLdb.connect(host=cfg['HOST'],port=int(cfg['PORT']),"
        "user=cfg['USER'],passwd=cfg['PASSWORD'],db='wms_db_test',connect_timeout=5)\n"
        "    with worker.cursor() as cursor:\n"
        "        cursor.execute('START TRANSACTION READ ONLY')\n"
        "        cursor.execute('SELECT id FROM coordination_probe LIMIT 1')\n"
        "        cursor.fetchall()\n"
        "    with lease.connection.cursor() as cursor:\n"
        "        cursor.execute('SELECT CONNECTION_ID()')\n"
        "        owner_id=cursor.fetchone()[0]\n"
        "    Path(os.environ['OWNER_READY']).write_text(json.dumps({'connection':owner_id}))\n"
        "    time.sleep(30)\n"
    )
    ready = tmp_path / "ready.json"
    env = {
        **os.environ,
        **environment,
        "PYTHONPATH": str(Path.cwd()),
        "WMS_TEST_COORDINATION_DIR": str(directory),
        "OWNER_READY": str(ready),
    }
    owner = subprocess.Popen(
        [sys.executable, str(script)], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    try:
        deadline = time.monotonic() + 15
        while not ready.exists() and owner.poll() is None and time.monotonic() < deadline:
            time.sleep(0.1)
        assert ready.exists(), owner.communicate(timeout=2)
        connection_id = json.loads(ready.read_text())["connection"]
        # Only the recorded synthetic test coordinator session is terminated.
        with admin.cursor() as cursor:
            cursor.execute("KILL CONNECTION %s", (connection_id,))
        body_executed = False
        started = time.monotonic()
        with pytest.raises(CoordinationBlocked, match="not completed cleanup"):
            with DatabaseLease(config, directory=directory, wait_seconds=0.3):
                body_executed = True
        assert not body_executed
        assert owner.poll() is None
        assert time.monotonic() - started < 5
        owner.terminate()
        owner.communicate(timeout=5)
        with DatabaseLease(config, directory=directory, wait_seconds=1):
            body_executed = True
        assert body_executed
        records = list(directory.glob("*.json"))
        assert len(records) == 1
        assert json.loads(records[0].read_text())["state"] == "CLEAN"
    finally:
        if owner.poll() is None:
            owner.kill()
        owner.communicate(timeout=5)
        admin.close()
