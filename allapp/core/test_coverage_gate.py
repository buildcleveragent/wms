"""Coverage gates reject missing entries and values just below policy."""

from copy import deepcopy

import pytest

from scripts.check_coverage_gate import check


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
    report["files"].pop("allapp/tasking/views_relocation.py")
    with pytest.raises(ValueError, match="missing or ambiguous"):
        check(report, policy)
