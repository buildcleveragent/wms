"""Offline tests for the HTTP workload, counter reduction, and formal evidence assembly."""

import json
import sys
from collections import Counter
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from scripts import validate_external_acceptance as gate
from scripts import wms_capacity as w
from scripts import wms_capacity_reconciliation as recon
from scripts.putaway_load_test import Sample


def actors(cross):
    result = []
    for role, per_fifty in w.load(w.PROFILE)["capacity"]["roles_per_50"].items():
        for i in range(per_fifty * 2):
            number = len(result) + 1
            # Alternate which warehouse receives each odd-sized group's extra person.
            warehouse = 1 + (i + (role in ("mall", "reports"))) % 2 if cross else 1
            result.append(
                dict(
                    username=f"sim{number}",
                    password="unit-fixture",
                    role=role,
                    warehouse_id=warehouse,
                    **{k: number for k in w.FIELDS[role]},
                )
            )
    return result


def test_roster_requires_unique_accounts_and_balances_every_phase():
    rules = w.load(w.PROFILE)["capacity"]
    config = {"actors": actors(True)}
    for users in (50, 100):
        group = w.roster(config, "cross-warehouse", users, rules)
        assert Counter(a["warehouse_id"] for a in group) == {1: users // 2, 2: users // 2}
    config["actors"][1]["username"] = config["actors"][0]["username"].upper()
    with pytest.raises(ValueError, match="distinct"):
        w.roster(config, "cross-warehouse", 50, rules)


@pytest.mark.parametrize(
    ("role", "write", "path", "response"),
    [
        (
            "pda",
            False,
            "/api/inbound/pda/tasks/2/",
            dict(
                id=2,
                warehouse_id=1,
                task_type="PUTAWAY",
                can_record=True,
                arrival=None,
                lines=[dict(id=2, qty_pending="240.000", from_location_id=1, to_location_id=2)],
            ),
        ),
        (
            "pda",
            True,
            "/api/inbound/pda/tasks/2/record-putaway/",
            dict(idempotent=False, task=dict(id=2, warehouse_id=1)),
        ),
        (
            "pos",
            True,
            "/api/pos/checkout/",
            dict(src_bill_no="sample-key", sale=dict(id=5, warehouse_id=1)),
        ),
        ("pos", False, "/api/pos/shifts/current/", dict(shift=dict(id=1, warehouse_id=1))),
        ("owner", False, "/api/inbound/orders/2/", dict(id=2, warehouse_id=1, owner_id=2)),
        (
            "mall",
            False,
            "/api/sale-mini/cart/?owner_id=2",
            dict(owner_id=2, warehouse={"id": 1}, ok=True, line_count=1),
        ),
        (
            "reports",
            False,
            "/api/reports/boss/inventory/?warehouse=1",
            dict(
                scope={"warehouse": 1}, summary={"sku_count": 3}, meta={"data_status": "COMPLETE"}
            ),
        ),
    ],
)
def test_business_calls_require_a_real_scoped_response(role, write, path, response):
    actor = {"role": role, "warehouse_id": 1, **{k: 2 for k in w.FIELDS[role]}}

    def request(method, url, payload, expected, operation):
        assert url == path
        assert method == ("POST" if write else "GET")
        if write:
            assert (
                payload.get("request_id") == "sample-key"
                or payload.get("src_bill_no") == "sample-key"
            )
        return Sample(operation, 3, expected[0], True), response

    client = SimpleNamespace(request=request)
    assert w.business_call(client, actor, write, "sample-key")[0].ok
    # A normal HTTP status on an unrelated warehouse does not activate the actor.
    if role != "reports":
        actor["warehouse_id"] = 8
        assert not w.business_call(client, actor, write, "sample-key")[0].ok


def test_counters_separate_timeouts_failed_and_unsent():
    events = [
        {"outcome": outcome, "role": "pda", "actor": 1, "method": "POST", "latency_ms": 100}
        for outcome in ("ok", "failed", "timeout", "unsent")
    ]
    result = w.summarize(events, 10)
    assert result["planned_requests"] == 4 and result["started_requests"] == 3
    assert result["completed_requests"] == 2 and result["unexpected_failures"] == 1
    assert result["timeout_requests"] == result["unsent_requests"] == 1


@pytest.mark.parametrize(
    "remaining,arrival,source,destination,units,valid",
    [
        ("240.000", None, 3, None, 240, True),
        ("30.000", None, 3, 5, 30, True),
        ("239.999", None, 3, None, 240, False),
        ("240", {"receipt_id": 55}, 3, None, 240, False),
        ("240", None, 5, None, 240, False),
        ("240", None, 3, 4, 240, False),
    ],
)
def test_pda_preflight_checks_the_planned_writes(
    remaining, arrival, source, destination, units, valid
):
    actor = dict(
        username="unit", role="pda", warehouse_id=1, task_id=10, line_id=2, to_location_id=5
    )
    session = w.Session(actor, "https://example.invalid", 1)
    session.authenticate = lambda: None
    task = dict(
        id=10,
        warehouse_id=1,
        task_type="PUTAWAY",
        can_record=True,
        arrival=arrival,
        lines=[
            dict(id=2, qty_pending=remaining, from_location_id=source, to_location_id=destination)
        ],
    )
    profile = {
        "user": {
            "username": "unit",
            "roles": ["warehouse_operator"],
            "scopes": {"is_global": False, "warehouse_ids": [1]},
        }
    }
    session.client.request = lambda method, url, **kwargs: (
        Sample("task", 1, 200, True),
        profile if url == "/api/auth/profile/" else task,
    )
    if valid:
        session.preflight(units)
    else:
        with pytest.raises(ValueError, match="fixture/scope"):
            session.preflight(units)


@pytest.mark.parametrize("sku_count,status", [(0, "COMPLETE"), (2, "UNAVAILABLE")])
def test_report_requires_visible_inventory(sku_count, status):
    data = dict(
        scope={"warehouse": 1}, summary={"sku_count": sku_count}, meta={"data_status": status}
    )
    client = SimpleNamespace(request=lambda *args, **kwargs: (Sample("report", 1, 200, True), data))
    sample, _ = w.business_call(client, dict(role="reports", warehouse_id=1), False, "")
    assert not sample.ok


START, END = "2026-01-01T10:00:00+00:00", "2026-01-01T10:00:10+00:00"


def timeseries(start=START, end=END):
    samples = [
        dict(
            at=ts,
            cpu_percent=22,
            rss_bytes=512,
            database_connections=8,
            backlog=0,
            lock_waits=v,
            slow_queries=2,
            queue_wait_ms=1,
            payment_external_ms=15,
        )
        for ts, v in [(start, 3), (end, 7)]
    ]
    return dict(started_at=start, finished_at=end, samples=samples)


def test_telemetry_fails_closed_on_missing_provider_or_counter_reset():
    row = dict(started_at=START, finished_at=END)
    export = dict(run_id="unit", **timeseries())
    assert w.server_metrics(export, row, "unit")["lock_waits"] == 4
    export["samples"][-1]["lock_waits"] = 0
    with pytest.raises(ValueError, match="reset"):
        w.server_metrics(export, row, "unit")
    for point in export["samples"]:
        point.pop("payment_external_ms")
    export["samples"][-1]["lock_waits"] = 8
    with pytest.raises(ValueError, match="payment_external_ms"):
        w.server_metrics(export, row, "unit")


def test_four_window_assembly_uses_raw_slots_server_samples_and_native_reconciliation(
    tmp_path, monkeypatch, capsys
):
    profile = w.load(w.PROFILE)
    rules = profile["capacity"]
    rules.update(warmup_seconds=10, phases=[dict(users=n, seconds=10) for n in (50, 100)])
    identity = dict(
        dataset=profile["dataset"], resource_limits={"web": "2x1cpu"}, deployment_identity="fixture"
    )
    observations = dict(identity, runs=[])
    dirs = []
    for scenario in rules["scenarios"]:
        folder = tmp_path / scenario
        folder.mkdir()
        dirs.append(folder)
        ds = profile["dataset"]
        w.save(
            folder / "baseline.json",
            dict(
                source="wms_capacity_dataset_v1",
                server_uuid="offline-unit",
                snapshot_id="baseline",
                database="snapshot",
                captured_at=START,
                dataset=ds,
                deployment_identity="fixture",
                order_counts={"inbound": ds["orders"], "outbound": 0},
                accounting_month_starts=[
                    f"2025-{m+1:02d}-01" for m in range(ds["accounting_months"])
                ],
            ),
        )
        group = actors(scenario == "cross-warehouse")
        client = dict(
            identity,
            baseline_id="baseline",
            origin="https://example.invalid",
            scenario=scenario,
            run_id=scenario,
            runs=[],
        )
        for index, (label, n) in enumerate([("warmup", 50), ("50", 50), ("100", 100)]):
            base = datetime.fromisoformat(START) + timedelta(seconds=10 * index)
            start, end = base.isoformat(), (base + timedelta(seconds=10)).isoformat()
            rows = w.roster(dict(actors=group), scenario, n, rules)
            events = [
                dict(
                    role=a["role"],
                    actor=a["username"],
                    warehouse_id=a["warehouse_id"],
                    slot=i,
                    scheduled_offset=5 * i,
                    at=(base + timedelta(seconds=5 * i)).isoformat(),
                    request_id=(
                        f"cap-{scenario}-{label}-{a['username']}-{i}"
                        if i and a["role"] in ("pos", "pda")
                        else ""
                    ),
                    outcome="ok",
                    method="POST" if i and a["role"] in ("pda", "pos") else "GET",
                    latency_ms=15,
                )
                for a in rows
                for i in range(2)
            ]
            raw = folder / f"{label}.jsonl"
            raw.write_text("".join(json.dumps(e) + "\n" for e in events))
            phase = dict(
                users=n,
                raw_file=raw.name,
                measured_seconds=10,
                started_at=start,
                finished_at=end,
                **w.summarize(events, 10),
            )
            if label == "warmup":
                client["warmup"] = phase
                continue
            client["runs"].append(dict(phase, scenario=scenario))
            prefix = f"{scenario}-{n}"
            telemetry = tmp_path / f"{prefix}-telemetry"
            w.save(telemetry, dict(run_id=scenario, **timeseries(start, end)))
            native = {
                section: dict(
                    issue_count=0,
                    checks=[
                        dict(name=name, ok=True, skipped=False, issue_count=0) for name in names
                    ],
                )
                for section, names in recon.CHECKS.items()
            }
            native["scope"] = dict(
                owner_id=None, warehouse_id=None, service_date=None, period_id=None
            )
            reports = [
                dict(
                    scope=dict(warehouse_id=wh),
                    checked_at=(datetime.fromisoformat(end) + timedelta(hours=8))
                    .replace(tzinfo=None)
                    .isoformat(),
                    period=dict(start_date="2026-01-01", end_date="2026-01-01"),
                    checks=[dict(code=c, error_count=0) for c in recon.POS_CHECKS],
                    summary=dict(sale_count=50, check_count=len(recon.POS_CHECKS), error_count=0),
                )
                for wh in {a["warehouse_id"] for a in rows}
            ]
            checkpoint = tmp_path / f"{prefix}-checkpoint"
            committed = [
                {k: e[k] for k in ("role", "warehouse_id", "request_id")}
                for e in events
                if e["method"] == "POST"
            ]
            w.save(
                checkpoint,
                dict(
                    snapshot_id=prefix,
                    as_of=end,
                    users=n,
                    run_id=scenario,
                    deployment_identity="fixture",
                    committed=committed,
                    reconciliation=native,
                    pos=reports,
                ),
            )
            observations["runs"].append(
                dict(
                    scenario=scenario,
                    users=n,
                    run_id=scenario,
                    started_at=start,
                    finished_at=end,
                    telemetry_file=telemetry.name,
                    checkpoint_file=checkpoint.name,
                )
            )
        w.save(folder / "client.json", client)
    manifest = tmp_path / "observations.json"
    w.save(manifest, observations)
    args = SimpleNamespace(
        observations=manifest, client_dirs=dirs, output=tmp_path / "assembled.json"
    )
    w.assemble(args, profile)
    assert len(w.load(args.output)["runs"]) == 4
    profile_path = tmp_path / "profile.json"
    w.save(profile_path, profile)
    monkeypatch.setattr(
        sys, "argv", ["validate", "capacity", str(args.output), "--profile", str(profile_path)]
    )
    assert gate.main() == 0
    assert json.loads(capsys.readouterr().out)["status"] == "PASS"
    unbacked = w.load(args.output)
    del unbacked["sources"]
    for invalid in (unbacked, {"runs": [None]}, {"dataset": None}):
        args.output.write_text(json.dumps(invalid))
        assert gate.main() == 2 and json.loads(capsys.readouterr().out)["status"] == "BLOCKED"
    for field, invalid in (
        ("read_p95_ms", False),
        ("unexpected_failures", None),
        ("completed_requests", "100"),
        ("financial_errors", False),
    ):
        previous = unbacked["runs"][0][field]
        unbacked["runs"][0][field] = invalid
        assert gate.capacity_errors(unbacked, profile)
        unbacked["runs"][0][field] = previous
    original = checkpoint.read_text()
    for field in ("stale_checked_at", "wrong_request_id", "wrong_scope", "missing_check"):
        data = json.loads(original)
        if field == "stale_checked_at":
            data["pos"][0]["checked_at"] = START
        elif field == "wrong_request_id":
            data["committed"][0]["request_id"] = "archive-from-unrelated-run"
        elif field == "wrong_scope":
            data["reconciliation"]["scope"]["owner_id"] = 99
        else:
            data["reconciliation"]["billing"]["checks"].pop()
        checkpoint.write_text(json.dumps(data))
        with pytest.raises(ValueError):
            w.assemble(args, profile)
    checkpoint.write_text(original)
    ledger = dirs[0] / "50.jsonl"
    ledger.write_text(ledger.read_text().split("\n", 1)[1])
    with pytest.raises(ValueError, match="missing slots"):
        w.assemble(args, profile)


def test_global_or_out_of_scope_account_cannot_fill_an_owner_slot():
    actor = dict(role="owner", username="unit", owner_id=3)
    user = dict(
        username="unit", roles=["owner_manager"], scopes=dict(is_global=True, owner_ids=[3])
    )
    assert not w.matches_identity(actor, {"user": user})
    user["scopes"]["is_global"] = False
    assert w.matches_identity(actor, {"user": user})
    user["scopes"]["owner_ids"] = [4]
    assert not w.matches_identity(actor, {"user": user})
