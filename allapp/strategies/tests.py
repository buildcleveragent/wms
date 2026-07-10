import datetime

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from allapp.strategies import models as strategy_models

pytestmark = pytest.mark.unit


class StrategyModelTests(TestCase):
    def setUp(self):
        self.category = strategy_models.StrategyCategory.objects.create(
            name="Inventory",
            description="Inventory strategy category",
        )
        self.template = strategy_models.StrategyTemplate.objects.create(
            category=self.category,
            name="Rotation",
            description="Stock rotation template",
        )
        self.strategy = strategy_models.Strategy.objects.create(
            category=self.category,
            template=self.template,
            name="FEFO Retail",
            inventory_management_type="FEFO",
            parameters={"expiry_days": 30},
        )

    def test_strategy_tree_defaults_and_string_representations(self):
        self.assertEqual(str(self.category), "Inventory")
        self.assertEqual(str(self.template), "Rotation")
        self.assertEqual(str(self.strategy), "FEFO Retail")
        self.assertTrue(self.strategy.is_active)
        self.assertEqual(self.strategy.parameters["expiry_days"], 30)
        self.assertEqual(self.category.templates.get(), self.template)
        self.assertEqual(self.template.strategies.get(), self.strategy)

    def test_strategy_assignment_is_unique_per_target(self):
        start = timezone.now()
        strategy_models.StrategyAssignment.objects.create(
            strategy=self.strategy,
            target="outbound_order",
            target_id=1001,
            start_date=start,
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                strategy_models.StrategyAssignment.objects.create(
                    strategy=self.strategy,
                    target="outbound_order",
                    target_id=1001,
                    start_date=start + datetime.timedelta(hours=1),
                )

    def test_strategy_log_and_parameter_keep_auditable_context(self):
        user = get_user_model().objects.create_user(
            username="strategy-user", password="x"
        )
        log = strategy_models.StrategyLog.objects.create(
            strategy=self.strategy,
            action="update",
            description="Changed FEFO window",
            changed_by=user,
        )
        parameter = strategy_models.StrategyParameter.objects.create(
            strategy=self.strategy,
            name="expiry_days",
            value="30",
            description="Minimum shelf life",
        )

        self.assertIn("FEFO Retail - update by strategy-user", str(log))
        self.assertEqual(str(parameter), "expiry_days: 30")
