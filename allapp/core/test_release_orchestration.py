"""Exercise real release scripts with isolated, deterministic service commands."""

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SERVICES = "web scheduler boss-check-worker"
DOCKER = r"""#!/bin/sh
echo "docker $*" >> "$TEST_ROOT/events"
case "$*" in
  *check_volume_access.py*) [ "$TEST_SCENARIO" != volumes ] || exit 8 ;;
  *'migrate --noinput'*) [ "$TEST_SCENARIO" != migration ] || exit 9 ;;
  *'up -d scheduler boss-check-worker'*) [ "$TEST_SCENARIO" != startup ] || exit 10 ;;
esac
case "$*" in
  'image inspect '*) echo sha256:candidate ;;
  *'ps --all -q '*)
    for service in "$@"; do :; done
    if [ "$service" = boss-check-worker ]; then
      [ "$TEST_SCENARIO" != missing ] || exit 0
      [ "$TEST_SCENARIO" != duplicate ] || echo boss-check-worker-other-id
    fi
    echo "$service-id" ;;
  'inspect '*StartedAt*) echo '2026-01-01T00:00:00.000000000Z' ;;
  'inspect '*)
    for id in "$@"; do :; done
    if [ "$TEST_SCENARIO:$id" = old-image:boss-check-worker-id ]; then
      echo 'true sha256:previous'
    elif [ "$TEST_SCENARIO:$id" = stopped:scheduler-id ]; then
      echo 'false sha256:candidate'
    else
      echo 'true sha256:candidate'
    fi ;;
  *check_boss_worker*)
    count=0
    if [ -f "$TEST_ROOT/heartbeat-count" ]; then read -r count < "$TEST_ROOT/heartbeat-count"; fi
    count=$((count + 1))
    echo "$count" > "$TEST_ROOT/heartbeat-count"
    case "$TEST_SCENARIO" in
      stale-heartbeat|old-heartbeat) exit 1 ;;
      observation) [ "$count" -le 1 ] || exit 1 ;;
      warming-up) [ "$count" -ne 1 ] || exit 1 ;;
    esac ;;
esac
exit 0
"""


def release(tmp_path, *, stage=False, scenario="success"):
    programs = {
        "docker": DOCKER,
        "systemctl": """#!/bin/sh
echo "systemctl $*" >> "$TEST_ROOT/events"
case "$*" in
  'is-active --quiet '*.service) exit 3 ;;
esac
""",
        "backup": '#!/bin/sh\necho backup >> "$TEST_ROOT/events"\n',
        "sleep": "#!/bin/sh\nexit 0\n",
        "date": """#!/bin/sh
n=940
if [ -f "$TEST_ROOT/clock" ]; then read -r n < "$TEST_ROOT/clock"; fi
n=$((n + 60))
echo "$n" > "$TEST_ROOT/clock"
echo "$n"
""",
    }
    for name, text in programs.items():
        target = tmp_path / name
        target.write_text(text)
        target.chmod(0o700)
    command = ["/bin/sh", f"docker/{'staging' if stage else 'production'}_release.sh"]
    image = "example.invalid/wms@sha256:candidate"
    if not stage:
        command.append(image)
    result = subprocess.run(
        command,
        cwd=ROOT,
        env={
            **os.environ,
            "PATH": f"{tmp_path}:/usr/bin:/bin",
            "TEST_ROOT": str(tmp_path),
            "TEST_SCENARIO": scenario,
            "WMS_IMAGE": image,
            "WMS_DB_BACKUP_SCRIPT": str(tmp_path / "backup"),
            "WMS_MEDIA_BACKUP_SCRIPT": str(tmp_path / "backup"),
            "WMS_OBSERVE_SECONDS": "10" if scenario == "observation" else "0",
        },
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result, (tmp_path / "events").read_text().splitlines()


@pytest.mark.parametrize("stage", [False, True], ids=["production", "staging"])
@pytest.mark.parametrize("scenario", ["success", "warming-up"])
def test_all_writers_stop_before_migration_and_start_the_candidate(tmp_path, stage, scenario):
    result, events = release(tmp_path, stage=stage, scenario=scenario)
    assert result.returncode == 0, result.stderr

    def find(value):
        return next(i for i, event in enumerate(events) if value in event)

    assert find(f"stop {SERVICES}") < find("migrate --noinput")
    assert find("migrate --noinput") < find("up -d web")
    assert find("reconcile_data_accuracy") < find("up -d scheduler boss-check-worker")
    assert find("audit_dispatch_note_snapshots") < find("up -d scheduler boss-check-worker")
    assert any("check_boss_worker --started-after 2026-01-01T00:00:00" in e for e in events)
    for service in SERVICES.split():
        assert any(f"ps --all -q {service}" in e for e in events)
        assert any(f"Running}}}} {{{{.Image}}}} {service}-id" in e for e in events)
    if not stage:
        assert find("systemctl stop") < find("migrate --noinput")
        assert find("check_boss_worker") < find("systemctl start")
        assert "systemctl start wms-sale-mini-expire.service" in events
        assert "systemctl start wms-sale-mini-reconcile.service" in events


@pytest.mark.parametrize("stage", [False, True], ids=["production", "staging"])
@pytest.mark.parametrize(
    "scenario",
    [
        "migration",
        "startup",
        "missing",
        "duplicate",
        "old-image",
        "stopped",
        "stale-heartbeat",
        "old-heartbeat",
        "observation",
    ],
)
def test_release_failure_stops_every_writer(tmp_path, stage, scenario):
    result, events = release(tmp_path, stage=stage, scenario=scenario)
    assert result.returncode != 0
    assert events[-1].endswith(f"stop {SERVICES}")
    assert "remain stopped" in result.stderr
    if scenario == "migration":
        assert result.returncode == 9
        assert not any("up -d" in event for event in events)
        assert not any(event.startswith("systemctl start") for event in events)
    if not stage:
        assert "restore the verified database/media backup" in result.stderr
        assert events[-2].startswith("systemctl stop")


@pytest.mark.parametrize("stage", [False, True], ids=["production", "staging"])
def test_failed_preflight_preserves_running_services(tmp_path, stage):
    result, events = release(tmp_path, stage=stage, scenario="volumes")
    assert result.returncode == 8
    assert not any("systemctl stop" in event or f"stop {SERVICES}" in event for event in events)
    assert not any("migrate --noinput" in event or event == "backup" for event in events)


def test_ci_calls_the_shared_staging_entrypoint():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    deploy = workflow.split("- name: Deploy to staging", 1)[1]
    assert "sh docker/staging_release.sh" in deploy
    assert "migrate --noinput" not in deploy
