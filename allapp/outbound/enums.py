from django.db import models


class PricingStatus(models.TextChoices):
    PENDING = "PENDING", "待定价"
    CONFIRMED = "CONFIRMED", "已确认"