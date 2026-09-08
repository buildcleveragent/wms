"""A new evidence file must not invalidate an unchanged candidate."""

import subprocess

import pytest

from scripts.test_candidate import candidate_identity, verify_candidate


@pytest.mark.unit
def test_evidence_is_separate_but_source_and_test_changes_change_candidate(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "app.py").write_text("value = 1\n")
    (tmp_path / "test_app.py").write_text("assert value == 1\n")
    before = candidate_identity(tmp_path)
    evidence = tmp_path / "var" / "system-tests"
    evidence.mkdir(parents=True)
    (evidence / "result.xml").write_text("new JUnit")
    (evidence / "scan.json").write_text("new vulnerability database results")
    (tmp_path / ".env.test.local").write_text("PASSWORD=not-for-the-candidate\n")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/remediation-execution-2026-09-06.md").write_text("new evidence summary")
    assert candidate_identity(tmp_path) == before
    assert verify_candidate(tmp_path, before) == before
    (tmp_path / "test_app.py").write_text("assert value >= 1\n")
    assert candidate_identity(tmp_path)["source_digest"] != before["source_digest"]
    with pytest.raises(ValueError, match="source mismatch"):
        verify_candidate(tmp_path, before)
    (tmp_path / "test_app.py").write_text("assert value == 1\n")
    (tmp_path / "app.py").write_text("value = 2\n")
    assert candidate_identity(tmp_path)["source_digest"] != before["source_digest"]
