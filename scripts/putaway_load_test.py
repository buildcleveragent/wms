#!/usr/bin/env python3
"""Pre-production HTTP load check for the putaway PDA workflow.

The tool is read-only unless both --execute-writes and --confirm-preproduction
are supplied.  Credentials and task assignments are read from a local JSON file
and are never printed in the report.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import math
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class Sample:
    operation: str
    latency_ms: float
    status: int
    ok: bool
    error: str = ""


def percentile(values: list[float], percentile_value: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(percentile_value * len(ordered)) - 1)
    return ordered[index]


class JsonClient:
    def __init__(self, base_url: str, timeout: float):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.token = ""

    def request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        operation: str,
        expected: tuple[int, ...] = (200,),
    ) -> tuple[Sample, Any]:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=body,
            headers=headers,
            method=method,
        )
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
                status = response.status
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            status = exc.code
        except (urllib.error.URLError, TimeoutError) as exc:
            latency = (time.perf_counter() - started) * 1000
            return Sample(operation, latency, 0, False, str(exc)), None
        latency = (time.perf_counter() - started) * 1000
        try:
            data = json.loads(raw.decode("utf-8")) if raw else None
        except (UnicodeDecodeError, json.JSONDecodeError):
            data = None
        ok = status in expected
        error = "" if ok else f"unexpected HTTP {status}"
        return Sample(operation, latency, status, ok, error), data

    def login(self, username: str, password: str) -> Sample:
        sample, data = self.request(
            "POST",
            "/api/token/",
            payload={"username": username, "password": password},
            operation="login",
        )
        if sample.ok:
            token = (data or {}).get("access") or (data or {}).get("token")
            if not token:
                sample.ok = False
                sample.error = "login response contains no token"
            else:
                self.token = token
        return sample


def run_actor(
    actor: dict[str, Any],
    *,
    base_url: str,
    timeout: float,
    execute_writes: bool,
) -> list[Sample]:
    client = JsonClient(base_url, timeout)
    samples = [client.login(actor["username"], actor["password"])]
    if not samples[-1].ok:
        return samples

    query = urllib.parse.urlencode(
        {"task_type": "PUTAWAY", "search": actor.get("search", "")}
    )
    list_sample, _ = client.request(
        "GET",
        f"/api/inbound/pda/tasks/?{query}",
        operation="task_list",
    )
    samples.append(list_sample)
    task_id = actor.get("task_id")
    if task_id:
        detail_sample, _ = client.request(
            "GET",
            f"/api/inbound/pda/tasks/{task_id}/",
            operation="task_detail",
        )
        samples.append(detail_sample)
    if not execute_writes or not task_id:
        return samples

    required = ("line_id", "to_location_id", "qty")
    missing = [key for key in required if actor.get(key) in (None, "")]
    if missing:
        samples.append(
            Sample("scenario_config", 0, 0, False, f"missing fields: {', '.join(missing)}")
        )
        return samples
    for action in ("claim", "start"):
        sample, _ = client.request(
            "POST",
            f"/api/inbound/pda/tasks/{task_id}/{action}/",
            payload={},
            operation=action,
        )
        samples.append(sample)
        if not sample.ok:
            return samples
    submit_sample, _ = client.request(
        "POST",
        f"/api/inbound/pda/tasks/{task_id}/record-putaway/",
        payload={
            "request_id": f"load-{uuid.uuid4().hex}",
            "line_id": actor["line_id"],
            "to_location_id": actor["to_location_id"],
            "qty": str(actor["qty"]),
        },
        operation="record_putaway",
    )
    samples.append(submit_sample)
    return samples


def summarize(samples: list[Sample]) -> dict[str, Any]:
    operations: dict[str, list[Sample]] = {}
    for sample in samples:
        operations.setdefault(sample.operation, []).append(sample)
    summary = {}
    for operation, rows in sorted(operations.items()):
        latencies = [row.latency_ms for row in rows]
        summary[operation] = {
            "requests": len(rows),
            "errors": sum(not row.ok for row in rows),
            "error_rate": round(sum(not row.ok for row in rows) / len(rows), 4),
            "mean_ms": round(statistics.fmean(latencies), 2),
            "p95_ms": round(percentile(latencies, 0.95), 2),
            "max_ms": round(max(latencies), 2),
        }
    all_errors = [sample for sample in samples if not sample.ok]
    return {
        "operations": summary,
        "total_requests": len(samples),
        "total_errors": len(all_errors),
        "error_rate": round(len(all_errors) / len(samples), 4) if samples else 0,
        "errors": [
            {"operation": row.operation, "status": row.status, "error": row.error}
            for row in all_errors[:50]
        ],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--actors", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=30)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--execute-writes", action="store_true")
    parser.add_argument("--confirm-preproduction", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.execute_writes and not args.confirm_preproduction:
        print(
            "Refusing write scenario without --confirm-preproduction.",
            file=sys.stderr,
        )
        return 2
    actors = json.loads(args.actors.read_text(encoding="utf-8"))
    if not isinstance(actors, list) or not actors:
        raise ValueError("actors file must contain a non-empty JSON array")
    required_credentials = [
        index
        for index, actor in enumerate(actors)
        if not actor.get("username") or not actor.get("password")
    ]
    if required_credentials:
        raise ValueError(f"actors missing credentials at indexes {required_credentials}")

    samples: list[Sample] = []
    started = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = [
            executor.submit(
                run_actor,
                actor,
                base_url=args.base_url,
                timeout=args.timeout,
                execute_writes=args.execute_writes,
            )
            for actor in actors
        ]
        for future in concurrent.futures.as_completed(futures):
            samples.extend(future.result())
    report = summarize(samples)
    report.update(
        {
            "base_url": args.base_url,
            "actors": len(actors),
            "workers": args.workers,
            "write_scenario": args.execute_writes,
            "elapsed_seconds": round(time.time() - started, 2),
            "thresholds": {
                "read_p95_ms": 1000,
                "write_p95_ms": 2000,
                "error_rate_lt": 0.01,
            },
        }
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)

    read_operations = ("task_list", "task_detail")
    write_operations = ("claim", "start", "record_putaway")
    read_ok = all(
        report["operations"].get(name, {}).get("p95_ms", 0) <= 1000
        for name in read_operations
        if name in report["operations"]
    )
    write_ok = all(
        report["operations"].get(name, {}).get("p95_ms", 0) <= 2000
        for name in write_operations
        if name in report["operations"]
    )
    return 0 if read_ok and write_ok and report["error_rate"] < 0.01 else 1


if __name__ == "__main__":
    raise SystemExit(main())
