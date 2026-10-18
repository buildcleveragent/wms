"""Exercise real subprocess termination, resource limits and shared admission."""

import subprocess
import sys
from unittest.mock import patch

import pytest
from django.test import override_settings

from allapp.reports.pdf_rendering import PdfUnavailable, render_pdf, rendering_slot
from allapp.reports.test_pdf_acceptance import pdf_text


def test_real_isolated_pdf_retains_chinese_and_releases_slot():
    pdf = render_pdf("<p>资源验收 中文 19.00</p>")
    assert "19.00" in pdf_text(pdf)
    assert "中文" in pdf_text(pdf)
    with rendering_slot():
        pass


def test_active_render_rejects_another_process_without_queueing():
    with rendering_slot():
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "from allapp.reports.pdf_rendering import rendering_slot, PdfUnavailable\n"
                "try:\n"
                "    with rendering_slot(): pass\n"
                "except PdfUnavailable: raise SystemExit(23)\n",
            ],
            capture_output=True,
            timeout=10,
            check=False,
        )
    assert result.returncode == 23, result.stderr
    with rendering_slot():
        pass


@override_settings(PDF_TIMEOUT_SECONDS=0.001)
def test_timeout_kills_and_reaps_child_before_releasing_slot():
    import allapp.reports.pdf_rendering as rendering

    original = subprocess.Popen
    child = []

    def observed(*args, **kwargs):
        process = original(*args, **kwargs)
        child.append(process)
        return process

    with patch.object(rendering.subprocess, "Popen", side_effect=observed):
        with pytest.raises(PdfUnavailable, match="timed out"):
            render_pdf("<p>timeout</p>")
    assert len(child) == 1
    assert child[0].poll() is not None
    with rendering_slot():
        pass


@override_settings(PDF_MEMORY_BYTES=16 * 1024 * 1024)
def test_memory_limit_fails_safely_and_releases_slot():
    with pytest.raises(PdfUnavailable, match="resource limits"):
        render_pdf("<p>memory budget</p>")
    with rendering_slot():
        pass


@override_settings(PDF_MAX_HTML_BYTES=4)
def test_large_input_is_rejected_before_starting_a_renderer():
    with patch("allapp.reports.pdf_rendering.subprocess.run") as run:
        with pytest.raises(PdfUnavailable, match="input"):
            render_pdf("too large")
        run.assert_not_called()


@override_settings(PDF_MAX_OUTPUT_BYTES=32)
def test_output_file_limit_is_enforced_by_worker():
    with pytest.raises(PdfUnavailable, match="resource limits"):
        render_pdf("<p>output limit</p>")
