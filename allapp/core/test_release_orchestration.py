"""Exercise real release scripts with isolated, deterministic service commands."""

import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SERVICES = "web scheduler boss-check-worker owner-archive-worker"
DOCKER = r"""#!/bin/sh
echo "docker $*" >> "$TEST_ROOT/events"
case "$*" in
  *'--profile oneshot ps --status running -q')
    if [ "$TEST_SCENARIO" = lingering-writer ] || [ -f "$TEST_ROOT/late-writer" ]; then
      echo leftover-id
    fi ;;
  *'ps --status running -q web scheduler boss-check-worker owner-archive-worker') echo 'old-web old-scheduler old-worker' ;;
  *'ps --status running -q web') echo old-web ;;
  *'ps --status running -q boss-check-worker') echo old-worker ;;
  'inspect '*com.docker.compose.oneoff*)
    if [ "$TEST_SCENARIO" = oneoff ]; then echo True; else echo False; fi
    exit 0 ;;
  'inspect --format {{.State.Running}} old-'*)
    if [ "$TEST_SCENARIO" = resume-failure ]; then echo false; else echo true; fi
    exit 0 ;;
  *check_volume_access.py*) [ "$TEST_SCENARIO" != volumes ] || exit 8 ;;
  *'migrate --noinput'*)
    if [ "$TEST_SCENARIO" = post-failure-writer ]; then touch "$TEST_ROOT/late-writer"; exit 9; fi
    [ "$TEST_SCENARIO" != migration ] || exit 9 ;;
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
    backup_hook = """#!/usr/bin/env python3
import json, os, pathlib
kind = pathlib.Path(__file__).name
scenario = os.environ['TEST_SCENARIO']
with (pathlib.Path(os.environ['TEST_ROOT']) / 'events').open('a') as f:
    print('backup ' + kind, file=f)
if scenario == 'backup-' + kind or scenario in ('resume-failure', 'inactive-timer'):
    raise SystemExit(11)
if scenario == 'missing-receipt' and kind == 'media':
    raise SystemExit(0)
includes = ['database'] if kind == 'database' else ['media', 'private_arrivals', 'private_returns']
if scenario == 'missing-private' and kind == 'media':
    includes.remove('private_returns')
point = os.environ['WMS_BACKUP_SET_ID']
if scenario == 'mixed-snapshots' and kind == 'media':
    point = 'old-set'
pathlib.Path(os.environ['WMS_BACKUP_RECEIPT']).write_text(json.dumps(
    dict(recovery_point=point, snapshot_id='artifact-' + kind, includes=includes)))
"""
    programs = {
        "docker": DOCKER,
        "systemctl": """#!/bin/sh
echo "systemctl $*" >> "$TEST_ROOT/events"
case "$*" in
  'list-units '*) [ "$TEST_SCENARIO" != legacy ] || echo wms-boss-deadlines.timer ;;
  'show '*wms-sale-mini-expire.service)
    if [ "$TEST_SCENARIO" = draining ] && [ ! -f "$TEST_ROOT/drained" ]; then
      echo activating
      touch "$TEST_ROOT/drained"
    else
      echo inactive
    fi ;;
  'show '*wms-sale-mini-reconcile.timer)
    if [ "$TEST_SCENARIO" = inactive-timer ]; then echo inactive; else echo active; fi ;;
  'show '*.timer) echo active ;;
  'show '*.service) echo inactive ;;
  'is-active --quiet '*.service) exit 3 ;;
esac
""",
        "database": backup_hook,
        "media": backup_hook,
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
            "WMS_DB_BACKUP_SCRIPT": str(tmp_path / "database"),
            "WMS_MEDIA_BACKUP_SCRIPT": str(tmp_path / "media"),
            "WMS_BACKUP_EVIDENCE_DIR": str(tmp_path / "evidence"),
            "WMS_RELEASE_LOCK_FILE": str(tmp_path / "release.lock"),
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
        assert find("systemctl stop") < find(f"stop {SERVICES}") < find("backup database")
        assert find("backup database") < find("backup media") < find("migrate --noinput")
        manifest = json.loads(next((tmp_path / "evidence").glob("*/manifest.json")).read_text())
        assert manifest["database"]["recovery_point"] == manifest["media"]["recovery_point"]
        assert manifest["recovery_point"] == manifest["database"]["recovery_point"]
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
    assert any(event.endswith(f"stop {SERVICES}") for event in events)
    assert "remain stopped" in result.stderr
    if scenario == "migration":
        assert result.returncode == 9
        assert not any("up -d" in event for event in events)
        assert not any(event.startswith("systemctl start") for event in events)
    if not stage:
        assert "restore the verified database/media backup" in result.stderr
        assert events[-1].endswith("--profile oneshot ps --status running -q")
        assert any(event.startswith("systemctl stop") for event in events)
    else:
        assert events[-1].endswith(f"stop {SERVICES}")


def test_lingering_writer_blocks_restore_after_migration_failure(tmp_path):
    result, events = release(tmp_path, scenario="post-failure-writer")
    assert result.returncode != 0
    assert events[-1].endswith("--profile oneshot ps --status running -q")
    assert "drain remaining writers before any restore" in result.stderr
    assert "restore the verified database/media backup" not in result.stderr


@pytest.mark.parametrize("stage", [False, True], ids=["production", "staging"])
def test_failed_preflight_preserves_running_services(tmp_path, stage):
    result, events = release(tmp_path, stage=stage, scenario="volumes")
    assert result.returncode == 8
    assert not any("systemctl stop" in event or f"stop {SERVICES}" in event for event in events)
    assert not any("migrate --noinput" in event or event.startswith("backup") for event in events)


@pytest.mark.parametrize(
    "scenario",
    [
        "backup-database",
        "backup-media",
        "missing-receipt",
        "missing-private",
        "mixed-snapshots",
        "lingering-writer",
    ],
)
def test_backup_failure_resumes_only_previous_writers_before_migration(tmp_path, scenario):
    result, events = release(tmp_path, scenario=scenario)
    assert result.returncode != 0
    assert "docker start old-web old-scheduler old-worker" in events
    assert "previous writer state restored" in result.stderr
    assert any(event.startswith("systemctl start wms-sale-mini-expire.timer") for event in events)
    assert not any("migrate --noinput" in event or "up -d" in event for event in events)
    assert not list((tmp_path / "evidence").glob("*/manifest.json"))
    if scenario == "lingering-writer":
        assert not any(event.startswith("backup ") for event in events)


def test_failed_old_container_restart_keeps_timers_off(tmp_path):
    result, events = release(tmp_path, scenario="resume-failure")
    assert result.returncode != 0
    assert not any("systemctl start" in event for event in events)
    assert events[-1].endswith(f"stop {SERVICES}")


def test_backup_abort_preserves_previously_inactive_timer(tmp_path):
    result, events = release(tmp_path, scenario="inactive-timer")
    assert result.returncode != 0
    starts = [event for event in events if event.startswith("systemctl start")]
    assert starts == ["systemctl start wms-sale-mini-expire.timer"]


def test_activating_payment_job_drains_before_backup(tmp_path):
    result, events = release(tmp_path, scenario="draining")
    assert result.returncode == 0, result.stderr
    inspect = "systemctl show --property=ActiveState --value wms-sale-mini-expire.service"
    assert events[: events.index("backup database")].count(inspect) == 2


@pytest.mark.parametrize("scenario", ["legacy", "oneoff"])
def test_active_unscheduled_writer_prevents_freeze(tmp_path, scenario):
    result, events = release(tmp_path, scenario=scenario)
    assert result.returncode != 0
    assert not any(event.startswith(("backup ", "systemctl stop")) for event in events)


def test_ci_calls_the_shared_staging_entrypoint():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    deploy = workflow.split("- name: Deploy to staging", 1)[1]
    assert "sh docker/staging_release.sh" in deploy
    assert "migrate --noinput" not in deploy
