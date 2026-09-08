"""Single-host coordination for the canonical test database, never production."""

import fcntl
import json
import os
import shutil
import signal
import socket
import stat
import subprocess
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

import MySQLdb

from wmsmaster.test_database import TEST_DATABASE, validate_test_process
from wmsmaster.test_evidence import atomic_json

LOCK_NAME = "wms_db_test.execution"


class CoordinationBlocked(RuntimeError):
    """The resource cannot safely be acquired or handed over."""


def secure_directory(path=None):
    """Require one real, private directory shared by all users of an instance."""
    # The checks below reject foreign owners, non-private modes and symlinks.
    path = Path(
        path or os.environ.get("WMS_TEST_COORDINATION_DIR", "/tmp/wms-test-coordination")
    )  # nosec B108
    if not path.is_absolute():
        raise CoordinationBlocked("Coordination directory must be absolute.")
    path.mkdir(mode=0o700, parents=False, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise CoordinationBlocked("Coordination directory has unsafe type, owner or permissions.")
    if path.resolve() != path:
        raise CoordinationBlocked("Coordination directory must not traverse symlinks.")
    return path


@contextmanager
def secure_file_lock(path):
    """Open a stable lock without following links or blocking on a FIFO."""
    path = Path(path)
    secure_directory(path.parent)
    flags = os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
    descriptor = os.open(path, flags, 0o600)
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_nlink != 1
            or info.st_mode & 0o077
        ):
            raise CoordinationBlocked("Unsafe coordination lock file.")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise CoordinationBlocked("Another process owns the coordination lock.") from None
        yield descriptor
    finally:
        os.close(descriptor)


def process_identity(pid=None):
    """Identify a process using boot and start time as well as its PID."""
    pid = pid or os.getpid()
    try:
        tail = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    except (FileNotFoundError, ProcessLookupError):
        return None
    return {
        "pid": pid,
        "pid_namespace": os.readlink(f"/proc/{pid}/ns/pid"),
        "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        "start_time": tail[19],
        "state": tail[0],
    }


def process_is_alive(identity):
    if identity.get("pid_namespace") != os.readlink("/proc/self/ns/pid"):
        raise CoordinationBlocked(
            "Process belongs to an unobservable PID namespace; recovery required."
        )
    current = process_identity(identity["pid"])
    return bool(
        current
        and current["state"] != "Z"
        and all(current[key] == identity[key] for key in ("pid", "boot_id", "start_time"))
    )


def owned_processes(run_id):
    """Find inherited run markers without exporting any process environment."""
    if not run_id:
        return []
    marker = f"WMS_TEST_RUN_ID={run_id}".encode()
    ancestors = {os.getpid()}
    parent = os.getppid()
    while parent > 0 and parent not in ancestors:
        ancestors.add(parent)
        try:
            parent = int(Path(f"/proc/{parent}/stat").read_text().rsplit(")", 1)[1].split()[1])
        except (FileNotFoundError, ProcessLookupError):
            break
    found = []
    for path in Path("/proc").iterdir():
        if not path.name.isdigit() or int(path.name) in ancestors:
            continue
        try:
            if path.stat().st_uid != os.getuid():
                continue
            if marker not in (path / "environ").read_bytes().split(b"\0"):
                continue
            identity = process_identity(int(path.name))
            if identity and identity["state"] != "Z":
                found.append(identity)
        except (FileNotFoundError, ProcessLookupError):
            continue
    return found


def _connect(config):
    # Do not select a database: Django may legitimately recreate wms_db_test.
    return MySQLdb.connect(
        host=config["HOST"],
        port=int(config["PORT"] or 3306),
        user=config["USER"],
        passwd=config["PASSWORD"],
        connect_timeout=5,
        read_timeout=5,
        write_timeout=5,
        autocommit=True,
    )


class DatabaseLease:
    """Hold a server-wide lock plus a durable record of unfinished cleanup.

    Every cooperating entrypoint must share WMS_TEST_COORDINATION_DIR. CI jobs
    without a shared directory must use separate MySQL instances. This is not
    a distributed lease or protection from noncooperating SQL clients.
    """

    def __init__(self, config, *, directory=None, wait_seconds=60, on_lost=None):
        validate_test_process(config)
        self.config = config
        self.directory = secure_directory(directory)
        self.wait_seconds = min(max(float(wait_seconds), 0), 60)
        self.on_lost = on_lost or (lambda: os.kill(os.getpid(), signal.SIGINT))
        self.connection = None
        self.record = None
        self.path = None
        self.lost = threading.Event()
        self.stop = threading.Event()
        self.io = threading.RLock()
        self.monitor = None

    def query(self, sql, params=()):
        with self.io, self.connection.cursor() as cursor:
            cursor.execute(sql, params)
            return cursor.fetchall()

    def _read_record(self):
        try:
            info = self.path.lstat()
        except FileNotFoundError:
            return None
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
            raise CoordinationBlocked("Unsafe database run record.")
        try:
            record = json.loads(self.path.read_text())
            if record["schema_version"] != 1 or uuid.UUID(record["run_id"]).hex != record["run_id"]:
                raise ValueError("invalid run identity")
            if record["server_uuid"] != self.server_uuid or record["database"] != TEST_DATABASE:
                raise ValueError("identity mismatch")
            if record["state"] not in {"ACTIVE", "CLEAN", "ABORTED", "CLEANUP_REQUIRED"}:
                raise ValueError("invalid state")
            owner = record["owner"]
            if not isinstance(owner["pid"], int) or owner["pid"] <= 0:
                raise ValueError("invalid process identity")
            if not owner["boot_id"] or not owner["start_time"]:
                raise ValueError("incomplete process identity")
            if not isinstance(record["processes"], list) or not isinstance(
                record["containers"], list
            ):
                raise ValueError("incomplete resource registry")
            return record
        except (ValueError, KeyError, TypeError):
            raise CoordinationBlocked(
                "Corrupt database run record; recovery is required."
            ) from None

    def _database_idle(self):
        # PROCESS privilege is required: inability to observe other transactions
        # must not be interpreted as an empty or safe database.
        transactions = self.query("SELECT trx_mysql_thread_id FROM information_schema.innodb_trx")
        sessions = self.query(
            "SELECT ID FROM information_schema.processlist WHERE DB=%s AND ID<>CONNECTION_ID()",
            (TEST_DATABASE,),
        )
        return not transactions and not sessions

    def _previous_finished(self, record):
        # A clean SQL lease can precede the outer runner's exit. A new runner
        # must also wait for that previous container to have been removed.
        if record and self._containers(record):
            return False
        if record and record["state"] != "CLEAN":
            if process_is_alive(record["owner"]):
                return False
            if any(process_is_alive(item) for item in record.get("processes", [])):
                return False
            if owned_processes(record.get("resource_run_id")):
                return False
            for container in record.get("containers", []):
                result = subprocess.run(
                    ["docker", "ps", "-aq", "--no-trunc", "--filter", f"id={container}"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=True,
                )
                if result.stdout.strip():
                    return False
        return self._database_idle()

    def _containers(self, record):
        run_id = record.get("resource_run_id")
        if not run_id or not record.get("docker_capable"):
            return []
        result = subprocess.run(
            ["docker", "ps", "-aq", "--no-trunc", "--filter", f"label=com.wms.test-run={run_id}"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        # An isolated migration's target database is owned by its outer caller;
        # it must stay alive until the lease has checked transactions and closed.
        # A container running this pytest process is likewise reaped by its
        # outer host supervisor, after this process closes its database lease.
        # A different contender has a different hostname and still sees it.
        current_hostname = socket.gethostname()
        current_runner = None
        containers = result.stdout.split()
        if containers and current_hostname == f"wms-runner-{run_id}":
            identities = json.loads(
                subprocess.check_output(["docker", "inspect", *containers], text=True, timeout=10)
            )
            matches = [
                item["Id"]
                for item in identities
                if item["Config"]["Hostname"] == current_hostname
                and item["Config"].get("Labels", {}).get("com.wms.test-run") == run_id
            ]
            if len(matches) > 1:
                raise CoordinationBlocked("Ambiguous current test runner container identity.")
            current_runner = matches[0] if matches else None
        return [
            item
            for item in containers
            if not item.startswith(self.server_hostname)
            and item != current_runner
            and not (len(current_hostname) >= 12 and item.startswith(current_hostname))
        ]

    def __enter__(self):
        deadline = time.monotonic() + self.wait_seconds
        try:
            self.connection = _connect(self.config)
            self.server_uuid, self.connection_id, version, self.server_hostname = self.query(
                "SELECT @@server_uuid, CONNECTION_ID(), VERSION(), @@hostname"
            )[0]
            if not version.startswith("8.") or not self.server_uuid:
                raise CoordinationBlocked("Coordination requires an identified MySQL 8 instance.")
            self.path = self.directory / f"{self.server_uuid}-{TEST_DATABASE}.json"
            while self.query("SELECT GET_LOCK(%s, 0)", (LOCK_NAME,))[0][0] != 1:
                if time.monotonic() >= deadline:
                    raise CoordinationBlocked(
                        "Database execution lock unavailable within deadline."
                    )
                time.sleep(0.1)
            while True:
                previous = self._read_record()
                if self._previous_finished(previous):
                    break
                if time.monotonic() >= deadline:
                    raise CoordinationBlocked(
                        "Previous run has not completed cleanup; database untouched."
                    )
                time.sleep(0.1)
            self.record = {
                "schema_version": 1,
                "run_id": uuid.uuid4().hex,
                "server_uuid": self.server_uuid,
                "database": TEST_DATABASE,
                "state": "ACTIVE",
                "owner": process_identity(),
                "connection_id": self.connection_id,
                "processes": [],
                "containers": [],
                "previous_run": previous.get("run_id") if previous else None,
                "resource_run_id": os.environ.get("WMS_TEST_RUN_ID"),
                "docker_capable": bool(shutil.which("docker")),
            }
            atomic_json(self.path, self.record)
            self.monitor = threading.Thread(target=self._watch, daemon=True)
            self.monitor.start()
            return self
        except BaseException:
            if self.connection:
                self.connection.close()
            raise

    def assert_owned(self):
        if self.lost.is_set():
            raise CoordinationBlocked("Database coordination session was lost; abort this run.")
        owner = self.query("SELECT IS_USED_LOCK(%s)", (LOCK_NAME,))[0][0]
        if owner != self.connection_id:
            raise CoordinationBlocked("Database execution lock changed owner.")

    def _watch(self):
        while not self.stop.wait(0.5):
            try:
                self.assert_owned()
            except Exception:
                self.lost.set()
                self.on_lost()
                return

    def __exit__(self, exc_type, exc, traceback):
        self.stop.set()
        self.monitor.join(timeout=6)
        try:
            self.record["processes"] = owned_processes(self.record.get("resource_run_id"))
            self.record["containers"] = self._containers(self.record)
            if self.lost.is_set():
                self.record["state"] = "ABORTED"
            else:
                self.assert_owned()
                clean = (
                    self._database_idle()
                    and not self.record["processes"]
                    and not self.record["containers"]
                )
                self.record["state"] = "CLEAN" if clean else "CLEANUP_REQUIRED"
            atomic_json(self.path, self.record)
        except Exception:
            # The durable ACTIVE record remains a handover barrier if saving fails.
            self.record["state"] = "CLEANUP_REQUIRED"
            raise
        finally:
            self.connection.close()
        if exc_type is None and self.record["state"] != "CLEAN":
            raise CoordinationBlocked("Run did not finish with a clean database handover.")
