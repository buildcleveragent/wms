# -*- coding: utf-8 -*-
from django.contrib.auth.models import AbstractUser
from django.core.validators import RegexValidator
from django.db import models

from allapp.core.models import BaseModel
from wmsmaster import settings


class User(AbstractUser):
    phone = models.CharField(
        "手机号", max_length=20, blank=True, null=True, db_index=True,
        validators=[RegexValidator(r'^\+?\d{7,20}$', '手机号格式不正确')],
        # help_text="可选，支持+前缀，7-20位数字。注意：当 owner 为 NULL 时，允许相同手机号重复。"
    )
    # 建议：明确语义，考虑改名为 display_name；或仅使用 first_name/last_name
    # username = models.CharField("用户名", max_length=50, blank=True, null=True)
    name = models.CharField("姓名", max_length=50, blank=True, null=True)
    email= models.EmailField("电子邮件", max_length=100, blank=True, null=True)
    owner = models.ForeignKey("baseinfo.Owner", verbose_name="所属货主", null=True, blank=True, on_delete=models.PROTECT)
    warehouse = models.ForeignKey("locations.Warehouse", verbose_name="所属大仓",null=True, blank=True, on_delete=models.PROTECT,default=settings.DEFAULT_WAREHOUSE_ID,editable=False)

    remark = models.CharField("备注", max_length=200, blank=True, null=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    def clean(self):
        # 统一规范化（表单/Admin/序列化器都会走 clean）
        if self.phone is not None:
            p = self.phone.strip()
            self.phone = p or None

    def save(self, *args, **kwargs):
        # 兜底（避免绕过 clean 的路径）
        if self.phone is not None:
            self.phone = self.phone.strip() or None
        super().save(*args, **kwargs)

    class Meta:
        db_table = "accounts_user"
        verbose_name = "用户"
        verbose_name_plural = "用户管理"

        indexes = [
            # 2) 常见查询：按货主列用户 / 登录名
            models.Index(fields=["owner", "username"], name="idx_user_owner_username"),
            # 3) 常见查询：按仓库筛用户
            models.Index(fields=["warehouse"], name="idx_user_warehouse"),
            # 4) 若会按创建时间分页/清理
            models.Index(fields=["created_at"], name="idx_user_created_at"),
            # 5) 若会按小写邮箱检索（仅当你实际用 email 登录/查找时再开）
            # models.Index(Lower("email"), name="idx_user_email_lower"),
        ]

    def __str__(self):
        # 返回一个你希望的用户显示格式，例如：用户名和邮箱
        return f"{self.username} ({self.email})"

# ========= 上机/操作日志 =========
class SystemLog(BaseModel):
    LOG_TYPE_CHOICES = [
        ("LOGIN", "登录"),
        ("LOGOUT", "登出"),
        ("CREATE", "新增"),
        ("UPDATE", "编辑"),
        ("DELETE", "删除"),
        ("IMPORT", "导入"),
        ("EXPORT", "导出"),
        ("OTHER", "其他"),
    ]

    occurred_at = models.DateTimeField("操作日期", db_index=True)
    username = models.CharField("登陆用户名", max_length=150, db_index=True)  # 与 AbstractUser.username 对齐
    real_name = models.CharField("姓名", max_length=60, blank=True, null=True)
    log_type = models.CharField(
        "日志类型", max_length=10,
        choices=LOG_TYPE_CHOICES, default="OTHER", db_index=True
    )
    module = models.CharField("系统模块", max_length=80, blank=True, null=True)
    content = models.TextField("操作内容", blank=True, null=True)
    computer_name = models.CharField("计算机名", max_length=80, blank=True, null=True)
    ip_address = models.GenericIPAddressField("IP", blank=True, null=True)
    owner = models.ForeignKey("baseinfo.Owner", verbose_name="货主", on_delete=models.PROTECT, blank=True, null=True,
                              related_name="system_logs")
    motherboard_sn = models.CharField("主板序列号", max_length=80, blank=True, null=True)
    hdd_sn = models.CharField("硬盘序列号", max_length=80, blank=True, null=True)

    class Meta:
        verbose_name = "上机日志"
        verbose_name_plural = "上机日志"
        indexes = [
            models.Index(fields=["occurred_at", "username", "log_type"], name="idx_log_time_user_type"),
            models.Index(fields=["module"], name="idx_log_module"),
            models.Index(fields=["owner", "occurred_at"], name="idx_log_owner_time"),  # 多租户+时间检索
        ]
        ordering = ["-occurred_at", "-id"]

    def clean(self):
        """自定义清洗数据，确保字段符合预期格式"""
        # 检查 IP 地址格式
        # if self.ip_address and not re.match(r'^\d+\.\d+\.\d+\.\d+$', self.ip_address):
        #     raise ValidationError("IP 地址格式不正确")

        # # 检查主板和硬盘序列号格式（可以用正则进行限制）
        # if self.motherboard_sn and len(self.motherboard_sn) != 80:
        #     raise ValidationError("主板序列号长度不正确")
        # if self.hdd_sn and len(self.hdd_sn) != 80:
        #     raise ValidationError("硬盘序列号长度不正确")

        # 清理操作内容（可选：可以清除不必要的换行或空格）
        if self.content:
            self.content = self.content.strip()

        # 如果需要其他字段验证，可以在这里添加逻辑

    def __str__(self):
        # 这里展示操作时间、用户名和日志类型，方便开发者查看
        content_snippet = self.content[:30] if self.content else ''
        return f"{self.occurred_at:%Y-%m-%d %H:%M:%S} {self.username} {self.log_type} {content_snippet}"

    def save(self, *args, **kwargs):
        """保存前清洗数据"""
        self.clean()  # 在保存前调用 `clean` 方法
        super().save(*args, **kwargs)  # 调用父类的 save 方法进行保存

