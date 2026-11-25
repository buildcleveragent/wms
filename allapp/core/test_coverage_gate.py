"""Coverage gates reject missing entries and values just below policy."""

import json
from copy import deepcopy

import pytest
from coverage import CoverageData
from coverage.exceptions import DataError

from scripts.check_coverage_gate import check
from scripts.combine_ci_coverage import combine


@pytest.mark.unit
def test_coverage_thresholds_do_not_round_up_or_accept_missing_business_entries():
    policy = {
        "minimum_coverage_percent": {"allapp": 72.38, "relocation_api": 80, "replenishment_api": 80}
    }
    report = {
        "totals": {"percent_covered": 72.38},
        "files": {
            f"allapp/tasking/views_{name}.py": {"summary": {"percent_covered": 80}}
            for name in ("relocation", "replenishment")
        },
    }
    assert check(report, policy)["status"] == "PASS"
    for value in (72.37999, float("nan")):
        changed = deepcopy(report)
        changed["totals"]["percent_covered"] = value
        assert check(changed, policy)["status"] == "FAIL"
    for filename in report["files"]:
        changed = deepcopy(report)
        changed["files"][filename]["summary"]["percent_covered"] = 79.999
        assert check(changed, policy)["status"] == "FAIL"
    report["files"].pop("allapp/tasking/views_relocation.py")
    with pytest.raises(ValueError, match="missing or ambiguous"):
        check(report, policy)


def _write_shard(root, name, module, lines):
    path = root / f"pytest-{name}" / f".coverage.{name}"
    path.parent.mkdir()
    shard = CoverageData(basename=path)
    shard.add_lines({str(module): lines})
    shard.write()
    return path


def test_combine_reports_coverage_from_both_named_artifacts(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    module = tmp_path / "sample.py"
    module.write_text("a = 1\nb = 2\nc = 3\n")
    _write_shard(tmp_path, "functional", module, [1])
    _write_shard(tmp_path, "migrations", module, [2])
    _write_shard(tmp_path, "unrelated", module, [3])
    combine(tmp_path, tmp_path)
    report = json.loads((tmp_path / "coverage.json").read_text())
    assert report["files"]["sample.py"]["executed_lines"] == [1, 2]
    assert (tmp_path / "coverage.xml").is_file()


@pytest.mark.parametrize("name", ["functional", "migrations"])
@pytest.mark.parametrize("reason", ["missing", "corrupt", "empty"])
def test_combine_fails_on_invalid_required_shard(tmp_path, name, reason):
    for source in ("functional", "migrations"):
        path = _write_shard(tmp_path, source, tmp_path / "sample.py", [1])
        if source == name:
            if reason == "missing":
                path.unlink()
            else:
                path.write_bytes(b"" if reason == "empty" else b"invalid coverage file")
    with pytest.raises((ValueError, DataError), match=f"coverage.{name}"):
        combine(tmp_path, tmp_path)
    assert not (tmp_path / "coverage.json").exists()
