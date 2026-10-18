"""Bound renderer concurrency, memory, CPU, output and wall time per container."""

import os
import subprocess
import sys
import tempfile
from contextlib import contextmanager, nullcontext
from pathlib import Path

from django.conf import settings


class PdfUnavailable(RuntimeError):
    """Rendering capacity is exhausted or the renderer exceeded its budget."""


@contextmanager
def rendering_slot():
    import fcntl

    # Never unlink a flock file: new and existing workers must share its inode.
    path = Path(tempfile.gettempdir()) / f"wms-pdf-render-{os.getuid()}.lock"
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise PdfUnavailable("PDF renderer is busy") from None
        yield descriptor
    finally:
        os.close(descriptor)


def render_pdf(html, *, slot=None):
    """Render without letting slow or oversized documents occupy every web worker."""
    admission = rendering_slot() if slot is None else nullcontext(slot)
    with admission as descriptor, tempfile.TemporaryDirectory(prefix="wms-pdf-") as temporary:
        data = html.encode("utf-8")
        if len(data) > settings.PDF_MAX_HTML_BYTES:
            raise PdfUnavailable("PDF input exceeds the configured budget")
        output = Path(temporary) / "report.pdf"
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).with_name("pdf_worker.py")),
                    str(output),
                    str(settings.PDF_MEMORY_BYTES),
                    str(settings.PDF_CPU_SECONDS),
                    str(settings.PDF_MAX_OUTPUT_BYTES),
                ],
                input=data,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                # A surviving child retains the slot if its web worker exits.
                pass_fds=(descriptor,),
                # The renderer does not need application secrets or database access.
                env={"PATH": os.defpath, "LANG": "C.UTF-8"},
                timeout=settings.PDF_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired:
            # subprocess.run kills and waits for its child before raising.
            raise PdfUnavailable("PDF rendering timed out") from None
        if result.returncode != 0 or not output.is_file():
            raise PdfUnavailable("PDF renderer failed within its resource limits")
        if output.stat().st_size > settings.PDF_MAX_OUTPUT_BYTES:
            raise PdfUnavailable("PDF output exceeds the configured budget")
        pdf = output.read_bytes()
        if not pdf.startswith(b"%PDF-"):
            raise PdfUnavailable("PDF renderer returned invalid output")
        return pdf
