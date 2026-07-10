import importlib
import sys

import pytest
from django.conf import settings
from django.test import SimpleTestCase

pytestmark = pytest.mark.unit


class LabelingAppRegistrationTests(SimpleTestCase):
    def test_labeling_models_are_not_accidentally_importable_until_app_is_registered(
        self,
    ):
        self.assertNotIn("allapp.labeling", settings.INSTALLED_APPS)

        sys.modules.pop("allapp.labeling.models", None)
        with self.assertRaises(RuntimeError) as exc:
            importlib.import_module("allapp.labeling.models")

        self.assertIn("isn't in an application in INSTALLED_APPS", str(exc.exception))
