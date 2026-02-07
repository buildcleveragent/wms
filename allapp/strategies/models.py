from django.conf import settings
from django.db import models


# 策略类别模型
class StrategyCategory(models.Model):
    name = models.CharField(verbose_name="策略类别名称", max_length=100)
    description = models.TextField(verbose_name="策略类别描述", null=True, blank=True)

    class Meta:
        verbose_name = "策略类别"
        verbose_name_plural = "策略类别"

    def __str__(self):
        return self.name


# 策略模板模型
class StrategyTemplate(models.Model):
    name = models.CharField(verbose_name="策略模板名称", max_length=100)
    category = models.ForeignKey(StrategyCategory, related_name="templates", on_delete=models.CASCADE,
                                 verbose_name="策略类别")
    description = models.TextField(verbose_name="模板描述", null=True, blank=True)

    class Meta:
        verbose_name = "策略模板"
        verbose_name_plural = "策略模板"

    def __str__(self):
        return self.name


# 常见的策略选择（通过选择项实现）
class Strategy(models.Model):
    name = models.CharField(verbose_name="策略名称", max_length=100)
    template = models.ForeignKey(StrategyTemplate, related_name="strategies", on_delete=models.CASCADE,
                                 verbose_name="策略模板")
    category = models.ForeignKey(StrategyCategory, related_name="strategies", on_delete=models.CASCADE,
                                 verbose_name="策略类别")
    description = models.TextField(verbose_name="策略描述", null=True, blank=True)
    is_active = models.BooleanField(verbose_name="是否启用", default=True)

    # 根据不同策略类型的选择字段
    inventory_management_type = models.CharField(
        verbose_name="库存管理策略", max_length=20, choices=[
            ('FIFO', '先进先出'),
            ('LIFO', '后进先出'),
            ('FEFO', '保质期优先'),
            ('BATCH', '批次管理'),
        ], null=True, blank=True
    )
    order_processing_type = models.CharField(
        verbose_name="订单处理策略", max_length=20, choices=[
            ('AUTO_ALLOC', '自动订单分配'),
            ('MANUAL_ALLOC', '手动订单分配'),
        ], null=True, blank=True
    )
    shipping_type = models.CharField(
        verbose_name="配送策略", max_length=20, choices=[
            ('PRIORITY', '优先配送'),
            ('STANDARD', '标准配送'),
        ], null=True, blank=True
    )
    billing_type = models.CharField(
        verbose_name="计费策略", max_length=20, choices=[
            ('FLAT', '固定费用'),
            ('TIERED', '阶梯费用'),
        ], null=True, blank=True
    )

    parameters = models.JSONField(verbose_name="策略参数", default=dict)
    created_at = models.DateTimeField(verbose_name="创建时间", auto_now_add=True)
    updated_at = models.DateTimeField(verbose_name="更新时间", auto_now=True)

    class Meta:
        verbose_name = "策略"
        verbose_name_plural = "策略"
        indexes = [models.Index(fields=['name', 'template'])]

    def __str__(self):
        return self.name


# 策略应用模型
class StrategyAssignment(models.Model):
    strategy = models.ForeignKey(Strategy, related_name="assignments", on_delete=models.CASCADE, verbose_name="策略")
    target = models.CharField(verbose_name="目标对象（如订单、库存、配送等）", max_length=255)
    target_id = models.PositiveIntegerField(verbose_name="目标对象ID")
    start_date = models.DateTimeField(verbose_name="策略生效时间")
    end_date = models.DateTimeField(verbose_name="策略失效时间", null=True, blank=True)
    is_active = models.BooleanField(verbose_name="是否生效", default=True)

    class Meta:
        verbose_name = "策略应用"
        verbose_name_plural = "策略应用"
        unique_together = ('strategy', 'target', 'target_id')

    def __str__(self):
        return f"{self.strategy.name} - {self.target} ({self.target_id})"


# 策略日志模型
class StrategyLog(models.Model):
    strategy = models.ForeignKey(Strategy, related_name="logs", on_delete=models.CASCADE, verbose_name="策略")
    action = models.CharField(verbose_name="操作类型", max_length=50,
                              choices=[('create', '创建'), ('update', '更新'), ('delete', '删除')])
    description = models.TextField(verbose_name="操作描述")
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, verbose_name="操作用户")
    changed_at = models.DateTimeField(verbose_name="操作时间", auto_now_add=True)

    class Meta:
        verbose_name = "策略日志"
        verbose_name_plural = "策略日志"

    def __str__(self):
        return f"{self.strategy.name} - {self.action} by {self.changed_by.username} at {self.changed_at}"


# 策略参数模型
class StrategyParameter(models.Model):
    strategy = models.ForeignKey(Strategy, related_name="sparameters", on_delete=models.CASCADE, verbose_name="策略")
    name = models.CharField(verbose_name="参数名称", max_length=100)
    value = models.CharField(verbose_name="参数值", max_length=255)
    description = models.TextField(verbose_name="参数描述", null=True, blank=True)

    class Meta:
        verbose_name = "策略参数"
        verbose_name_plural = "策略参数"

    def __str__(self):
        return f"{self.name}: {self.value}"
