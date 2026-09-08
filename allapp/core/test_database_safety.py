from copy import deepcopy

import pytest

from wmsmaster.test_database import validate_test_database


@pytest.mark.unit
@pytest.mark.parametrize("name", ["", "wms_db", "wms_ci_serial", "wms_db_test_gw0", None])
@pytest.mark.parametrize("field", ["NAME", "TEST"])
def test_rejects_noncanonical_resolved_database(name, field):
    config = {
        "ENGINE": "django.db.backends.mysql",
        "NAME": "wms_db_test",
        "TEST": {"NAME": "wms_db_test"},
    }
    unsafe = deepcopy(config)
    if field == "TEST":
        unsafe["TEST"]["NAME"] = name
    else:
        unsafe[field] = name
    with pytest.raises(RuntimeError, match="exactly wms_db_test"):
        validate_test_database(unsafe)


@pytest.mark.unit
def test_rejects_sqlite_even_with_a_test_name():
    with pytest.raises(RuntimeError, match="MySQL 8"):
        validate_test_database({"ENGINE": "django.db.backends.sqlite3", "NAME": "wms_db_test"})
