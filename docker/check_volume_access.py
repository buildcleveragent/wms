"""Fail before stopping writers if the runtime user cannot use release volumes."""

import os
from pathlib import Path
from tempfile import TemporaryDirectory


def check_volume(directory):
    """Exercise directory creation and file IO without touching existing data."""
    root = Path(directory)
    if not root.is_dir():
        raise RuntimeError(f"Required volume directory is missing: {root}")

    def unreadable(error):
        raise error

    for parent, directories, files in os.walk(root, onerror=unreadable, followlinks=False):
        for path in [Path(parent), *(Path(parent) / name for name in directories + files)]:
            if path.is_symlink():
                raise RuntimeError(f"Volume symlink requires explicit review: {path}")
            mode = os.R_OK | os.W_OK | (os.X_OK if path.is_dir() else 0)
            if not os.access(path, mode):
                raise RuntimeError(f"Runtime user cannot access existing volume entry: {path}")
    with TemporaryDirectory(prefix=".wms-permission-check-", dir=root) as temporary:
        probe = Path(temporary) / "probe"
        probe.write_bytes(b"wms-volume-probe")
        if probe.read_bytes() != b"wms-volume-probe":
            raise RuntimeError(f"Volume readback failed: {root}")
        probe.unlink()


if __name__ == "__main__":
    for directory in ("/app/staticfiles", "/app/media"):
        check_volume(directory)
    print("Static and media volume access verified for the runtime user.")
