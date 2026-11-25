"""Fail before stopping writers if the runtime user cannot use release volumes."""

import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

RELEASE_VOLUMES = (
    "/app/staticfiles",
    "/app/media",
    "/app/private_arrivals",
    "/app/private_returns",
    "/app/private_exports",
)


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


def check_release_volumes():
    """Run the probe against the same mounts and account as the production app."""
    if (os.geteuid(), os.getegid()) != (10001, 10001):
        raise SystemExit("Run the release volume probe as UID:GID 10001:10001.")
    mounts = {line.split()[4] for line in Path("/proc/self/mountinfo").read_text().splitlines()}
    for directory in RELEASE_VOLUMES:
        if directory not in mounts:
            raise SystemExit(f"Required release volume is not mounted: {directory}")
        check_volume(directory)

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "wmsmaster.settings")
    from django.conf import settings

    check_attachment_root(settings.PRIVATE_ARRIVAL_ROOT, "/app/private_arrivals")
    check_attachment_root(settings.PRIVATE_RETURN_ROOT, "/app/private_returns")
    check_attachment_root(settings.OWNER_ARCHIVE_ROOT, "/app/private_exports")


def check_attachment_root(configured, mount):
    """A private storage override must keep new and existing files on its backed-up volume."""
    target = Path(configured).resolve()
    if not target.is_relative_to(mount):
        raise RuntimeError(f"Configured private attachment root {target} is outside {mount}")
    # FileSystemStorage creates missing subdirectories on first use.
    while target != Path(mount) and not target.exists():
        target = target.parent
    if target != Path(mount):
        check_volume(target)


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    check_release_volumes()
    print("Verified read/write access to staticfiles, media, private_arrivals, private_returns and private_exports.")
