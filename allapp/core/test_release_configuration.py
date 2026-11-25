"""Release probes must fail safely without weakening business HTTPS policy."""

import subprocess
from unittest.mock import patch

import pytest
from django.test import Client, override_settings

from docker.check_volume_access import (
    RELEASE_VOLUMES,
    check_attachment_root,
    check_release_volumes,
    check_volume,
)
from scripts.lint_ratchet import tracked_python_files


@override_settings(SECURE_SSL_REDIRECT=True)
def test_health_probe_stays_http_but_business_routes_redirect():
    client = Client()
    assert client.get("/healthz/live").status_code == 200
    with patch("wmsmaster.views.connection.cursor") as cursor:
        assert client.get("/healthz/ready").status_code == 200
        cursor.return_value.__enter__.return_value.execute.assert_called_once_with("SELECT 1")
    with patch("wmsmaster.views.connection.cursor", side_effect=RuntimeError("unavailable")):
        response = client.get("/healthz/ready")
        assert response.status_code == 503
        assert response.json() == {"status": "unavailable"}
    for path in ("/admin/", "/api/", "/healthz/ready/extra", "/healthz/live/"):
        response = client.get(path)
        assert response.status_code == 301
        assert response["Location"] == f"https://testserver{path}"


def test_volume_probe_preserves_existing_files_and_cleans_up(tmp_path):
    existing = tmp_path / "existing-media"
    existing.write_bytes(b"existing data")
    check_volume(tmp_path)
    assert list(tmp_path.iterdir()) == [existing]
    assert existing.read_bytes() == b"existing data"
    with pytest.raises(RuntimeError, match="missing"):
        check_volume(tmp_path / "missing")


def test_private_storage_overrides_must_use_the_backed_up_mount(tmp_path):
    mount = tmp_path / "private_arrivals"
    mount.mkdir()
    check_attachment_root(mount / "future" / "photos", mount)
    assert not list(mount.iterdir())
    with pytest.raises(RuntimeError, match="outside"):
        check_attachment_root(tmp_path / "media", mount)
    bad_parent = mount / "file"
    bad_parent.touch()
    with pytest.raises(RuntimeError, match="directory"):
        check_attachment_root(bad_parent / "photos", mount)


def test_lint_excludes_only_historical_evidence_not_executable_source():
    sources = ["allapp/test_example.py", "scripts/example.py", "docker/example.py"]
    sources += ["var/system-tests-other/tool.py"]
    result = subprocess.CompletedProcess(
        [], 0, stdout="\n".join([*sources, "var/system-tests/old/harness.py"])
    )
    with patch("scripts.lint_ratchet._run", return_value=result):
        assert tracked_python_files() == sorted(sources)


def test_volume_probe_rejects_unwritable_existing_entries(tmp_path):
    nested = tmp_path / "css"
    nested.mkdir()
    with patch(
        "docker.check_volume_access.os.access", side_effect=lambda path, mode: path != nested
    ):
        with pytest.raises(RuntimeError, match="existing volume entry"):
            check_volume(tmp_path)
    assert list(tmp_path.iterdir()) == [nested]


@pytest.mark.parametrize("denied", RELEASE_VOLUMES)
def test_release_probe_rejects_io_failure_on_each_mounted_volume(denied, monkeypatch):
    mounts = "\n".join(f"44 33 8:1 / {mount} rw" for mount in RELEASE_VOLUMES)
    monkeypatch.setattr("docker.check_volume_access.Path.read_text", lambda self: mounts)
    monkeypatch.setattr("docker.check_volume_access.os.geteuid", lambda: 10001)
    monkeypatch.setattr("docker.check_volume_access.os.getegid", lambda: 10001)

    def probe(path):
        if path == denied:
            raise PermissionError(path)

    monkeypatch.setattr("docker.check_volume_access.check_volume", probe)
    with pytest.raises(PermissionError, match=denied):
        check_release_volumes()
