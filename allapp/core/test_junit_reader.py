import pytest

from scripts.review_historical_test_evidence import parse_junit

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "doctype",
    [
        '<!DOCTYPE testsuite [<!ENTITY attack "expanded">]>',
        '<!DOCTYPE testsuite SYSTEM "file:///private.xml">',
    ],
)
def test_junit_reader_rejects_internal_and_external_dtd(tmp_path, doctype):
    source = tmp_path / "report.xml"
    source.write_text(doctype + '<testsuite><testcase name="safe"/></testsuite>')
    with pytest.raises(ValueError, match="document type"):
        parse_junit(source)


def test_junit_reader_preserves_ordinary_testcase_identity(tmp_path):
    source = tmp_path / "report.xml"
    source.write_text('<testsuite><testcase classname="module.Test" name="case"/></testsuite>')
    assert parse_junit(source).find("testcase").attrib == {
        "classname": "module.Test",
        "name": "case",
    }
