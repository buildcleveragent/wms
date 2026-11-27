"""Run the fixed WMS five-role workload against an explicitly confirmed staging origin."""

import argparse
import json
import math
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed, wait
from datetime import datetime, timezone
from pathlib import Path
from threading import Event, Lock

if __package__:
    from .putaway_load_test import JsonClient, percentile, validate_base_url
    from .validate_external_acceptance import capacity_errors
    from .wms_capacity_reconciliation import verify as verify_checkpoint
else:
    from putaway_load_test import JsonClient, percentile, validate_base_url
    from validate_external_acceptance import capacity_errors
    from wms_capacity_reconciliation import verify as verify_checkpoint

PROFILE = Path(__file__).resolve().parents[1] / "docs/external-acceptance-profile.json"
CADENCE = 5  # One scheduled business call per actor every five seconds.
FIELDS = {
    "pda": ("task_id", "line_id", "to_location_id"),
    "owner": ("order_id", "owner_id"),
    "pos": ("product_id",),
    "mall": ("owner_id",),
    "reports": (),
}


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save(path, data):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)


def utc():
    return datetime.now(timezone.utc).isoformat()


def roster(config, scenario, users, rules):
    """Use the first N distinct accounts per role; check warehouse balance in both phases."""
    actors = config["actors"]
    if len(actors) != 100 or len({a["username"].strip().casefold() for a in actors}) != len(actors):
        raise ValueError("100 distinct synthetic usernames required in the scenario roster")
    selected = []
    for role, per_fifty in rules["roles_per_50"].items():
        group = [a for a in actors if a["role"] == role]
        if len(group) != per_fifty * 2:
            raise ValueError(f"wrong actor count for {role}")
        group = group[: per_fifty * users // 50]
        warehouses = Counter()
        for actor in group:
            if not actor["username"] or not actor["password"]:
                raise ValueError("synthetic credentials missing")
            for key in ("warehouse_id", *FIELDS[role]):
                if type(actor.get(key)) is not int or actor[key] < 1:
                    raise ValueError(f"invalid {role} fixture field {key}")
            warehouses[actor["warehouse_id"]] += 1
        if scenario == "cross-warehouse" and (
            len(warehouses) != 2 or max(warehouses.values()) - min(warehouses.values()) > 1
        ):
            raise ValueError(f"{role} must be split across both warehouses")
        selected.extend(group)
    counts = sorted(Counter(a["warehouse_id"] for a in selected).values())
    expected = [users] if scenario == "same-warehouse-hotspot" else [users // 2] * 2
    if counts != expected:
        raise ValueError("actor allocation does not match the warehouse scenario")
    if len({a["task_id"] for a in selected if a["role"] == "pda"}) != sum(
        a["role"] == "pda" for a in selected
    ):
        raise ValueError("each PDA operator requires a dedicated task")
    return selected


def business_call(client, actor, write, key, *, required_units=0):
    role, warehouse = actor["role"], actor["warehouse_id"]
    payload = None
    if role == "pda":
        path = f"/api/inbound/pda/tasks/{actor['task_id']}/"
        if write:
            path += "record-putaway/"
            payload = dict(
                request_id=key,
                line_id=actor["line_id"],
                to_location_id=actor["to_location_id"],
                qty="1.000",
            )
    elif role == "owner":
        path = f"/api/inbound/orders/{actor['order_id']}/"
    elif role == "pos":
        path = "/api/pos/shifts/current/"
        if write:
            path = "/api/pos/checkout/"
            payload = {
                "src_bill_no": key,
                "idempotency_key": key,
                "items": [{"product_id": actor["product_id"], "qty": "1.000", "price": "10.0000"}],
                "payment": {"method": "CASH", "amount_received": "10.00"},
            }
    elif role == "mall":
        path = f"/api/sale-mini/cart/?owner_id={actor['owner_id']}"
    else:
        path = f"/api/reports/boss/inventory/?warehouse={warehouse}"
    op = path.split("?")[0]
    method = "POST" if payload else "GET"
    sample, data = client.request(
        method,
        path,
        operation=op,
        payload=payload,
        expected=(201,) if role == "pos" and payload else (200,),
    )
    data = data if isinstance(data, dict) else {}
    # Check that a successful HTTP response actually observed the assigned business fixture.
    if role == "pda":
        task = (data.get("task") if payload else data) or {}
        valid = task.get("id") == actor["task_id"] and task.get("warehouse_id") == warehouse
        if payload:
            valid = valid and data.get("idempotent") is False
        else:
            line = next(
                (item for item in task.get("lines", ()) if item.get("id") == actor["line_id"]),
                {},
            )
            try:
                pending = float(line.get("qty_pending"))
            except (ValueError, TypeError):
                pending = -1
            valid = (
                valid
                and task.get("task_type") == "PUTAWAY"
                and task.get("can_record") is True
                and task.get("arrival") is None
                and math.isfinite(pending)
                and pending >= required_units
                and line.get("from_location_id") != actor["to_location_id"]
                and line.get("to_location_id") in (None, actor["to_location_id"])
            )
    elif role == "pos":
        obj = (data.get("sale") if payload else data.get("shift")) or {}
        valid = obj.get("warehouse_id") == warehouse and bool(obj.get("id"))
        if payload:
            valid = valid and data.get("src_bill_no") == key
    elif role == "owner":
        valid = (
            data.get("id") == actor["order_id"]
            and data.get("warehouse_id") == warehouse
            and data.get("owner_id") == actor["owner_id"]
        )
    elif role == "mall":
        valid = (
            data.get("owner_id") == actor["owner_id"]
            and (data.get("warehouse") or {}).get("id") == warehouse
            and data.get("ok") is True
            and bool(data.get("line_count"))
        )
    else:
        sku_count = (data.get("summary") or {}).get("sku_count")
        valid = (
            (data.get("scope") or {}).get("warehouse") == warehouse
            and type(sku_count) is int
            and sku_count > 0
            and (data.get("meta") or {}).get("data_status") in ("COMPLETE", "WARNING")
        )
    sample.ok = bool(sample.ok and valid)
    return sample, method


def matches_identity(actor, profile):
    user = profile.get("user", {})
    scopes = user.get("scopes", {})
    roles = set(user.get("roles", []))
    if user.get("username") != actor["username"] or scopes.get("is_global") is not False:
        return False
    role = actor["role"]
    if role == "owner":
        return bool(roles & {"owner_manager", "owner_finance", "owner_salesperson"}) and actor[
            "owner_id"
        ] in scopes.get("owner_ids", [])
    if role in ("pda", "reports"):
        expected = "warehouse_operator" if role == "pda" else "warehouse_boss"
        return expected in roles and actor["warehouse_id"] in scopes.get("warehouse_ids", [])
    if role == "pos":
        return "pos.add_possale" in profile.get("perms", [])
    # The storefront verifies the MiniProgramUser buyer binding when loading the assigned cart.
    return role == "mall"


class Session:
    def __init__(self, actor, origin, number):
        self.actor = actor
        self.number = number
        self.client = JsonClient(origin, timeout=10)
        self.refresh = ""
        self.renew_at = 0

    def authenticate(self):
        base = "/api/pda/token/" if self.actor["role"] == "pda" else "/api/token/"
        if self.refresh:
            path, payload = base + "refresh/", {"refresh": self.refresh}
        else:
            path = base
            payload = {k: self.actor[k] for k in ("username", "password")}
        sample, data = self.client.request("POST", path, operation="auth", payload=payload)
        if not sample.ok or not isinstance(data, dict) or not data.get("access"):
            raise ValueError(f"actor {self.number} authentication failed (HTTP {sample.status})")
        self.client.token = data["access"]
        self.refresh = data.get("refresh") or self.refresh
        self.renew_at = time.monotonic() + (23 * 3600 if self.actor["role"] == "pda" else 600)

    def preflight(self, required_units=0):
        self.authenticate()
        sample, profile = self.client.request(
            "GET", "/api/auth/profile/", operation="actor_profile"
        )
        if (
            not sample.ok
            or not isinstance(profile, dict)
            or not matches_identity(self.actor, profile)
        ):
            raise ValueError(f"actor {self.number} does not match the assigned business role")
        sample, _ = business_call(self.client, self.actor, False, "", required_units=required_units)
        if not sample.ok:
            raise ValueError(
                f"actor {self.number} fixture/scope check failed (HTTP {sample.status})"
            )


def summarize(events, duration):
    started = [e for e in events if e["outcome"] != "unsent"]
    by_role = {}
    for event in started:
        if event["outcome"] == "ok":
            by_role.setdefault(event["role"], set()).add(event["actor"])
    times = [e["latency_ms"] for e in started]
    reads = [e["latency_ms"] for e in started if e["method"] == "GET"]
    writes = [e["latency_ms"] for e in started if e["method"] == "POST"]
    counts = Counter(e["outcome"] for e in events)
    completed = len(started) - counts["timeout"]
    return {
        "planned_requests": len(events),
        "started_requests": len(started),
        "completed_requests": completed,
        "unsent_requests": counts["unsent"],
        "timeout_requests": counts["timeout"],
        "unexpected_failures": counts["failed"],
        "read_samples": len(reads),
        "write_samples": len(writes),
        "read_p95_ms": percentile(reads, 0.95),
        "write_p95_ms": percentile(writes, 0.95),
        "active_roles": {k: len(v) for k, v in by_role.items()},
        "metrics": {
            **{f"p{p}_ms": percentile(times, p / 100) for p in (50, 95, 99)},
            "throughput_per_second": completed / duration,
        },
    }


def phase(pool, sessions, duration, folder, label, run_id):
    events = []
    lock = Lock()
    stop = Event()
    users = len(sessions)
    slots = duration // CADENCE
    start = time.monotonic()
    started_at = utc()
    with (folder / f"{label}.jsonl").open("x", encoding="utf-8", buffering=1) as stream:

        def worker(item):
            lane, session = item
            actor = session.actor
            for slot in range(slots):
                due = start + CADENCE * (slot + lane / users)
                if stop.wait(max(0, due - time.monotonic())):
                    return
                if (
                    time.monotonic() < min(due + CADENCE, start + duration)
                    and time.monotonic() >= session.renew_at
                ):
                    session.authenticate()
                event = dict(
                    actor=session.number,
                    role=actor["role"],
                    warehouse_id=actor["warehouse_id"],
                    slot=slot,
                    scheduled_offset=due - start,
                    at=utc(),
                    outcome="unsent",
                )
                if time.monotonic() < min(due + CADENCE, start + duration):
                    write = actor["role"] in ("pda", "pos") and slot % 2 == 1
                    key = f"cap-{run_id}-{label}-{session.number}-{slot}" if write else ""
                    sample, method = business_call(session.client, actor, write, key)
                    timeout = sample.status == 0 and "timed out" in sample.error.lower()
                    outcome = "ok" if sample.ok else "timeout" if timeout else "failed"
                    event.update(
                        operation=sample.operation,
                        method=method,
                        status=sample.status,
                        latency_ms=sample.latency_ms,
                        outcome=outcome,
                        request_id=key,
                    )
                with lock:
                    stream.write(json.dumps(event, separators=(",", ":")) + "\n")
                    events.append(event)

        futures = [pool.submit(worker, item) for item in enumerate(sessions)]
        try:
            for future in as_completed(futures):
                future.result()
        finally:
            stop.set()
            wait(futures)  # Finish in-flight calls before closing their shared raw ledger.
    time.sleep(max(0, start + duration - time.monotonic()))
    return dict(
        users=users,
        measured_seconds=duration,
        started_at=started_at,
        finished_at=utc(),
        raw_file=f"{label}.jsonl",
        **summarize(events, duration),
    )


def verify_baseline(baseline, deployment, profile):
    counts = baseline["dataset"]
    if (
        baseline["source"] != "wms_capacity_dataset_v1"
        or not baseline["snapshot_id"]
        or not baseline["database"]
        or not baseline["server_uuid"]
        or baseline["deployment_identity"] != deployment
        or datetime.fromisoformat(baseline["captured_at"]).tzinfo is None
        or any(
            type(counts.get(k)) is not int or counts[k] < v for k, v in profile["dataset"].items()
        )
        or set(baseline["order_counts"]) != {"inbound", "outbound"}
        or sum(baseline["order_counts"].values()) != counts["orders"]
        or len(set(baseline["accounting_month_starts"])) != counts["accounting_months"]
    ):
        raise ValueError("incomplete or insufficient measured dataset baseline")


def run(args, profile):
    rules = profile["capacity"]
    config = load(args.config)
    origin = validate_base_url(config["origin"])
    if (
        not origin.startswith("https://")
        or args.confirm_preproduction != origin
        or config["scenario"] != args.scenario
    ):
        raise ValueError(
            "confirm the exact isolated HTTPS origin and scenario in the private roster"
        )
    if (
        not config.get("deployment_identity")
        or not config.get("resource_limits")
        or config.get("synthetic_only") is not True
    ):
        raise ValueError(
            "isolated synthetic dataset, deployment identity and resource limits required"
        )
    baseline = load(args.baseline)
    verify_baseline(baseline, config["deployment_identity"], profile)
    groups = {p["users"]: roster(config, args.scenario, p["users"], rules) for p in rules["phases"]}
    sessions = {a["username"]: Session(a, origin, n) for n, a in enumerate(groups[100], 1)}
    args.output.mkdir(parents=True, exist_ok=False)
    save(args.output / "baseline.json", baseline)
    run_id = uuid.uuid4().hex[:16]
    with ThreadPoolExecutor(max_workers=100) as pool:
        # Stagger initial logins to avoid a 100-login burst unrelated to warehouse throughput.
        for s in sessions.values():
            time.sleep(2.1)  # Default single-source IP login throttle is 30 per minute.
            scheduled_windows = [
                p["seconds"] for p in rules["phases"] if s.actor in groups[p["users"]]
            ]
            if s.actor in groups[50]:
                scheduled_windows.append(rules["warmup_seconds"])
            s.preflight(sum(seconds // (2 * CADENCE) for seconds in scheduled_windows))

        def measure(users, duration, label):
            return phase(
                pool,
                [sessions[a["username"]] for a in groups[users]],
                duration,
                args.output,
                label,
                run_id,
            )

        warmup = measure(50, rules["warmup_seconds"], "warmup")
        runs = [
            dict(scenario=args.scenario, **measure(p["users"], p["seconds"], str(p["users"])))
            for p in rules["phases"]
        ]
    save(
        args.output / "client.json",
        dict(
            run_id=run_id,
            origin=origin,
            scenario=args.scenario,
            deployment_identity=config["deployment_identity"],
            resource_limits=config["resource_limits"],
            dataset=baseline["dataset"],
            baseline_id=baseline["snapshot_id"],
            cadence_seconds=CADENCE,
            warmup=warmup,
            runs=runs,
        ),
    )


def raw_summary(folder, row, label, users, duration, scenario, run_id):
    """Verify the archived slot ledger before using client measurements for acceptance."""
    if (
        row["raw_file"] != f"{label}.jsonl"
        or row["users"] != users
        or row["measured_seconds"] != duration
    ):
        raise ValueError("window does not match the scheduled phase")
    with (folder / row["raw_file"]).open(encoding="utf-8") as stream:
        events = [json.loads(line) for line in stream]
    start, end = (datetime.fromisoformat(row[k]) for k in ("started_at", "finished_at"))
    if (
        start.tzinfo is None
        or end.tzinfo is None
        or not duration - 0.01 <= (end - start).total_seconds() <= duration + 15
    ):
        raise ValueError("recorded UTC window does not match the measurement duration")
    writes = {}
    slots = duration // CADENCE
    actors = {}
    occupied = set()
    for event in events:
        actor, slot = event["actor"], event["slot"]
        identity = (event["role"], event["warehouse_id"])
        if (
            not (0 <= slot < slots)
            or (actor, slot) in occupied
            or actors.setdefault(actor, identity) != identity
        ):
            raise ValueError("duplicate slot or unexpected actor or slot in raw evidence")
        observed = datetime.fromisoformat(event["at"])
        offset = event["scheduled_offset"]
        if not slot * CADENCE <= offset < (slot + 1) * CADENCE:
            raise ValueError("scheduled offset outside its time slot")
        lag = (observed - start).total_seconds() - offset
        if lag < -0.01 or (event["outcome"] != "unsent" and lag > CADENCE + 0.01):
            raise ValueError("raw request was not dispatched in the recorded slot")
        if not start <= observed <= end or event["outcome"] not in (
            "ok",
            "failed",
            "timeout",
            "unsent",
        ):
            raise ValueError("invalid raw request time or outcome")
        is_write = event["role"] in ("pos", "pda") and slot % 2 == 1
        if event["outcome"] != "unsent":
            if event["method"] != ("POST" if is_write else "GET"):
                raise ValueError("request method does not match scheduled role operation")
            if is_write:
                key = f"cap-{run_id}-{label}-{actor}-{slot}"
                if event["request_id"] != key:
                    raise ValueError("unexpected business write key")
                writes[event["role"], event["warehouse_id"], key] = event["outcome"]
        occupied.add((actor, slot))
    counts = sorted(Counter(warehouse for _, warehouse in actors.values()).values())
    expected = [users] if scenario == "same-warehouse-hotspot" else [users // 2] * 2
    if counts != expected or len(occupied) != users * slots:
        raise ValueError("raw evidence has missing slots or unexpected warehouse allocation")
    summary = summarize(events, duration)
    if any(row[k] != value for k, value in summary.items()):
        raise ValueError("client summary disagrees with raw evidence")
    return writes


def server_metrics(export, row, run_id):
    """Reduce point samples, cumulative counters and traced latency samples for one UTC window."""
    if export["run_id"] != run_id or any(
        export[k] != row[k] for k in ("started_at", "finished_at")
    ):
        raise ValueError("telemetry export does not match the client run and window")
    start, end = (datetime.fromisoformat(row[k]) for k in ("started_at", "finished_at"))
    series = {}
    for point in export["samples"]:
        at = datetime.fromisoformat(point["at"])
        if not start <= at <= end:
            raise ValueError("telemetry sample outside the measured window")
        for key, value in point.items():
            if key != "at":
                if type(value) not in (int, float) or value < 0 or not math.isfinite(value):
                    raise ValueError(f"invalid telemetry sample: {key}")
                series.setdefault(key, []).append((at, value))
    metrics = {}
    for key in (
        "cpu_percent",
        "rss_bytes",
        "database_connections",
        "backlog",
        "lock_waits",
        "slow_queries",
        "queue_wait_ms",
        "payment_external_ms",
    ):
        points = sorted(series.get(key, []))
        if not points:
            raise ValueError(f"missing telemetry samples: {key}")
        values = [value for _, value in points]
        if key in ("queue_wait_ms", "payment_external_ms"):
            metrics["queue_wait_p95_ms" if key == "queue_wait_ms" else key] = percentile(
                values, 0.95
            )
            continue
        times = [start, *(at for at, _ in points), end]
        if len(points) < 2 or any((b - a).total_seconds() > 30 for a, b in zip(times, times[1:])):
            raise ValueError(f"telemetry gap exceeds 30 seconds: {key}")
        if key in ("lock_waits", "slow_queries"):
            if any(b < a for a, b in zip(values, values[1:])):
                raise ValueError(f"counter reset within the measurement window: {key}")
            metrics[key] = values[-1] - values[0]
        else:
            metrics[key] = max(values)
    return metrics


def local_artifact(parent, reference):
    path = (parent / reference).resolve()
    path.relative_to(parent.resolve())
    return path


def build_report(observations, client_dirs, profile):
    external = load(observations)
    # Only the collector supplies latency/request counts. External telemetry supplies server metrics.
    output = {k: external[k] for k in ("deployment_identity", "resource_limits")}
    output.update(runs=[], warmup_seconds=profile["capacity"]["warmup_seconds"])
    seen = set()
    origins = set()
    rules = profile["capacity"]
    durations = {p["users"]: p["seconds"] for p in rules["phases"]}
    for folder in client_dirs:
        client = load(folder / "client.json")
        baseline = load(folder / "baseline.json")
        verify_baseline(baseline, output["deployment_identity"], profile)
        if (
            client["baseline_id"] != baseline["snapshot_id"]
            or output.setdefault("baseline_id", baseline["snapshot_id"]) != baseline["snapshot_id"]
            or datetime.fromisoformat(baseline["captured_at"])
            > datetime.fromisoformat(client["warmup"]["started_at"])
            or output.setdefault("dataset", baseline["dataset"]) != baseline["dataset"]
        ):
            raise ValueError("stale or mismatched baseline for this candidate run")
        scenario = client["scenario"]
        if (
            any(
                client[k] != output[k]
                for k in ("deployment_identity", "dataset", "resource_limits")
            )
            or scenario in seen
        ):
            raise ValueError(
                "duplicate scenario or mismatch in deployment, resource limits or dataset"
            )
        seen.add(scenario)
        origins.add(client["origin"])
        if len(origins) != 1:
            raise ValueError("scenario HTTP origins differ")
        warmup = client["warmup"]
        raw_summary(
            folder, warmup, "warmup", 50, rules["warmup_seconds"], scenario, client["run_id"]
        )
        previous_end = datetime.fromisoformat(warmup["finished_at"])
        if (
            warmup["active_roles"] != profile["capacity"]["roles_per_50"]
            or warmup["unsent_requests"]
        ):
            raise ValueError("warmup did not exercise the scheduled five-role workload")
        output["warmup_seconds"] = min(output["warmup_seconds"], warmup["measured_seconds"])
        if [r["users"] for r in client["runs"]] != [p["users"] for p in rules["phases"]]:
            raise ValueError("missing or out-of-order measurement phases")
        for row in client["runs"]:
            if datetime.fromisoformat(row["started_at"]) < previous_end:
                raise ValueError("client phases overlap or are out of sequence")
            previous_end = datetime.fromisoformat(row["finished_at"])
            if row["scenario"] != scenario:
                raise ValueError("mismatched scenario")
            writes = raw_summary(
                folder,
                row,
                str(row["users"]),
                row["users"],
                durations[row["users"]],
                scenario,
                client["run_id"],
            )
            matching = [
                obs
                for obs in external["runs"]
                if obs["run_id"] == client["run_id"]
                and obs["scenario"] == scenario
                and obs["users"] == row["users"]
            ]
            if len(matching) != 1:
                raise ValueError("one matching external measurement is required for every run")
            obs = matching[0]
            if any(obs[k] != row[k] for k in ("started_at", "finished_at")):
                raise ValueError("external export must cover the exact client measurement window")
            checkpoint = load(local_artifact(observations.parent, obs["checkpoint_file"]))
            issue_counts = verify_checkpoint(
                checkpoint, row, client["run_id"], client["deployment_identity"], writes
            )
            metrics = server_metrics(
                load(local_artifact(observations.parent, obs["telemetry_file"])),
                row,
                client["run_id"],
            )
            output["runs"].append(
                dict(
                    row,
                    metrics={**row["metrics"], **metrics},
                    run_id=client["run_id"],
                    inventory_errors=issue_counts["inventory"],
                    financial_errors=issue_counts["billing"],
                    **{
                        k: obs[k]
                        for k in (
                            "telemetry_file",
                            "checkpoint_file",
                        )
                    },
                )
            )
    return output


def assemble(args, profile):
    root = args.output.parent.resolve()
    sources = {
        "observations": str(args.observations.resolve().relative_to(root)),
        "client_dirs": [str(p.resolve().relative_to(root)) for p in args.client_dirs],
    }
    output = build_report(args.observations, args.client_dirs, profile)
    errors = capacity_errors(output, profile)
    save(args.output, dict(output, sources=sources))
    if errors:
        raise ValueError(" / ".join(errors))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    profile = load(PROFILE)
    cmd = commands.add_parser("run")
    cmd.add_argument("--config", type=Path, required=True)
    cmd.add_argument("--baseline", type=Path, required=True)
    cmd.add_argument("--scenario", choices=profile["capacity"]["scenarios"], required=True)
    cmd.add_argument("--confirm-preproduction", required=True)
    cmd.add_argument("--output", type=Path, required=True)
    cmd = commands.add_parser("assemble")
    cmd.add_argument("--client-dirs", nargs=2, required=True, type=Path)
    cmd.add_argument("--observations", type=Path, required=True)
    cmd.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "run":
            run(args, profile)
        else:
            assemble(args, profile)
    except (OSError, KeyError, TypeError, ValueError) as exc:
        parser.exit(2, f"BLOCKED: {exc}\n")


if __name__ == "__main__":
    main()
