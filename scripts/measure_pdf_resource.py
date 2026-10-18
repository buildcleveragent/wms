#!/usr/bin/env python3
"""Measure synthetic dispatch PDFs through the bounded production renderer."""

import argparse
import json
import os
import platform
import resource
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def worker(rows, output):
    os.environ.setdefault("APP_ENV", "test")
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "wmsmaster.settings")
    import django

    django.setup()
    import weasyprint
    from django.conf import settings
    from django.template.loader import render_to_string

    from allapp.reports.pdf_rendering import render_pdf
    from allapp.reports.test_pdf_acceptance import sample_note

    started = time.monotonic()
    html = render_to_string("reports/dispatch_note.html", {"note": sample_note(rows)})
    pdf = render_pdf(html)
    output.write_bytes(pdf)
    print(
        json.dumps(
            {
                "rows": rows,
                "input_html_bytes": len(html.encode()),
                "pdf_bytes": len(pdf),
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "peak_rss_kib": resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
                "web_process_peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                "renderer_memory_bytes": settings.PDF_MEMORY_BYTES,
                "renderer_cpu_seconds": settings.PDF_CPU_SECONDS,
                "renderer_timeout_seconds": settings.PDF_TIMEOUT_SECONDS,
                "renderer_concurrency_per_container": 1,
                "weasyprint": weasyprint.__version__,
                "python": platform.python_version(),
                "status": "MEASURED",
            }
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--worker", type=int)
    args = parser.parse_args()
    if args.worker is not None:
        worker(args.worker, args.output)
        return 0
    args.output.mkdir(parents=True, exist_ok=False)
    report = {
        "risk": "R-PDF-RESOURCE",
        "risk_status": "REVIEW_REQUIRED",
        "production_limits_implemented": True,
        "scope": "synthetic local measurement; not a production capacity or DoS guarantee",
        "samples": [],
    }
    for rows in (1, 100, 1000):
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--worker",
                    str(rows),
                    "--output",
                    str(args.output / f"dispatch-{rows}.pdf"),
                ],
                capture_output=True,
                text=True,
                timeout=180,
            )
            (args.output / f"dispatch-{rows}.log").write_text(result.stderr)
            sample = (
                json.loads(result.stdout)
                if result.returncode == 0
                else {
                    "rows": rows,
                    "status": "FAIL",
                    "exit_code": result.returncode,
                }
            )
        except subprocess.TimeoutExpired:
            sample = {"rows": rows, "status": "TEST_TIMEOUT", "limit_seconds": 180}
        report["samples"].append(sample)
        (args.output / "measurement.json").write_text(json.dumps(report, indent=2) + "\n")
    return 0 if all(row["status"] == "MEASURED" for row in report["samples"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
