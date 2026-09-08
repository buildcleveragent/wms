"""Append-only pytest evidence, with summaries scoped to one execution."""

import json
import os
import tempfile
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


def atomic_json(path, value):
    """Publish a complete JSON document without exposing a partial write."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class TestEvidence:
    """Retain every report; later events cannot erase an earlier failure."""

    __test__ = False

    def __init__(self, destination=None, *, run_id=None):
        self.run_id = run_id or uuid.uuid4().hex
        self.destination = Path(destination) if destination else None
        self.events = []
        self.nodes = []
        self.attempts = Counter()
        self.subtests = Counter()
        self.events_path = None
        if self.destination:
            self.destination.parent.mkdir(parents=True, exist_ok=True)
            if self.destination.exists():
                raise RuntimeError("Evidence destination exists; choose a new run directory.")
            self.events_path = self.destination.with_suffix(".events.jsonl")
            # Exclusive creation also protects against accidental run reuse.
            with self.events_path.open("x"):
                pass
        self.append("started")

    def append(self, kind, **fields):
        event = {
            "schema_version": 1,
            "run_id": self.run_id,
            "sequence": len(self.events) + 1,
            "time": datetime.now(timezone.utc).isoformat(),
            "kind": kind,
            **fields,
        }
        if self.events_path:
            with self.events_path.open("a") as stream:
                stream.write(json.dumps(event, ensure_ascii=False) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
        self.events.append(event)

    def collection(self, nodes):
        self.nodes = list(nodes)
        self.append("collection", nodes=self.nodes)

    def report(self, report):
        node = report.nodeid
        context = getattr(report, "context", None)
        if report.when == "setup" and context is None:
            self.attempts[node] += 1
        attempt = max(self.attempts[node], 1)
        subtest_id = None
        if context is not None:
            self.subtests[(node, attempt)] += 1
            subtest_id = f"{attempt}:{self.subtests[(node, attempt)]}"
        self.append(
            "report",
            nodeid=node,
            phase=report.when,
            outcome=report.outcome,
            attempt=attempt,
            subtest_id=subtest_id,
            # Parameter values and report text may contain credentials.
            parameter_names=sorted(str(key) for key in getattr(context, "kwargs", {})),
        )

    def summary(self, exit_code, *, collect_only=False, password_hasher=None):
        reports = defaultdict(list)
        for event in self.events:
            if event["kind"] == "report":
                reports[event["nodeid"]].append(event)
        tests = {}
        for node in self.nodes:
            events = reports.get(node, [])
            phases = defaultdict(list)
            for event in events:
                phases[event["phase"]].append(event["outcome"])
            if any(event["outcome"] in {"failed", "rerun"} for event in events):
                status = "FAIL"
            elif any(event["outcome"] == "skipped" for event in events):
                status = "SKIPPED"
            elif all(
                any(
                    e["phase"] == phase and e["outcome"] == "passed" and e["subtest_id"] is None
                    for e in events
                )
                for phase in ("setup", "call", "teardown")
            ):
                status = "PASS"
            else:
                status = "NOT RUN"
            tests[node] = {"status": status, "phases": dict(phases)}
        complete = bool(tests) and all(row["status"] == "PASS" for row in tests.values())
        unexpected = sorted(set(reports) - set(self.nodes))
        if collect_only and exit_code == 0 and self.nodes and not reports:
            status = "COLLECTED"
        elif exit_code == 2:
            status = "ABORTED"
        else:
            status = "PASS" if exit_code == 0 and complete and not unexpected else "FAIL"
        return {
            "schema_version": 1,
            "run_id": self.run_id,
            "status": status,
            "exit_code": int(exit_code),
            "collected": len(self.nodes),
            "password_hasher": password_hasher,
            "events_file": str(self.events_path) if self.events_path else None,
            "unexpected_nodes": unexpected,
            "main_counts": dict(Counter(row["status"] for row in tests.values())),
            "subtest_counts": dict(
                Counter(e["outcome"] for e in self.events if e.get("subtest_id") is not None)
            ),
            "tests": tests,
        }

    def finish(self, exit_code, **kwargs):
        result = self.summary(exit_code, **kwargs)
        self.append("finished", status=result["status"], exit_code=int(exit_code))
        if self.destination:
            atomic_json(self.destination, result)
        return result
