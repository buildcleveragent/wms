# -*- coding: utf-8 -*-
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models

from allapp.core.models import BaseModel


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
    warehouse = models.ForeignKey("locations.Warehouse", verbose_name="所属大仓",null=True, blank=True, on_delete=models.PROTECT)

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


class UserRoleScope(models.Model):
    """A user's business role and the tenant boundary attached to that role.

    Groups and permissions grant capabilities.  This model grants no capability;
    it only records which owner or warehouse rows those capabilities may touch.
    A warehouse boss may have several active rows (one per warehouse).  Every
    other role is deliberately limited to one active scope.
    """

    class Role(models.TextChoices):
        WAREHOUSE_OPERATOR = "warehouse_operator", "仓库操作员"
        WAREHOUSE_MANAGER = "warehouse_manager", "仓库管理员"
        WAREHOUSE_BOSS = "warehouse_boss", "仓库老板"
        OWNER_MANAGER = "owner_manager", "货主管理员"
        OWNER_SALESPERSON = "owner_salesperson", "货主业务员"

    WAREHOUSE_ROLES = frozenset(
        {
            Role.WAREHOUSE_OPERATOR,
            Role.WAREHOUSE_MANAGER,
            Role.WAREHOUSE_BOSS,
        }
    )
    OWNER_ROLES = frozenset({Role.OWNER_MANAGER, Role.OWNER_SALESPERSON})

    user = models.ForeignKey(
        "accounts.User",
        verbose_name="用户",
        on_delete=models.CASCADE,
        related_name="role_scopes",
    )
    role = models.CharField("角色", max_length=32, choices=Role.choices, db_index=True)
    owner = models.ForeignKey(
        "baseinfo.Owner",
        verbose_name="货主范围",
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="user_role_scopes",
    )
    warehouse = models.ForeignKey(
        "locations.Warehouse",
        verbose_name="仓库范围",
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="user_role_scopes",
    )
    is_active = models.BooleanField("启用", default=True, db_index=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        verbose_name = "用户角色范围"
        verbose_name_plural = "用户角色范围"
        ordering = ("user_id", "role", "warehouse_id", "owner_id", "id")
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(
                        role__in=(
                            "warehouse_operator",
                            "warehouse_manager",
                            "warehouse_boss",
                        ),
                        warehouse__isnull=False,
                        owner__isnull=True,
                    )
                    | models.Q(
                        role__in=("owner_manager", "owner_salesperson"),
                        owner__isnull=False,
                        warehouse__isnull=True,
                    )
                ),
                name="ck_user_role_scope_target",
            ),
            # One constraint is effective for warehouse roles and the other for
            # owner roles.  The relevant target is non-null because of the check
            # constraint, so this also works on MySQL where NULLs are not equal.
            models.UniqueConstraint(
                fields=("user", "role", "warehouse"),
                name="uq_user_role_warehouse_scope",
            ),
            models.UniqueConstraint(
                fields=("user", "role", "owner"),
                name="uq_user_role_owner_scope",
            ),
        ]
        indexes = [
            models.Index(
                fields=("user", "is_active"),
                name="idx_user_role_scope_active",
            ),
            models.Index(
                fields=("role", "is_active"),
                name="idx_role_scope_active",
            ),
        ]
        permissions = [
            ("access_warehouse_operations", "可执行仓库现场作业"),
            ("receive_without_order", "可执行无订单收货"),
            ("access_warehouse_management", "可管理仓库作业"),
            ("view_warehouse_reports", "可查看仓库运营报表"),
            ("view_warehouse_boss_dashboard", "可查看仓库老板经营看板"),
            ("view_warehouse_financials", "可查看仓库经营财务数据"),
            ("access_owner_management", "可管理货主订单"),
            ("access_owner_sales", "可执行货主业务"),
            ("view_owner_reports", "可查看货主运营报表"),
            ("view_owner_financials", "可查看货主财务数据"),
            ("export_operational_reports", "可导出运营报表"),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.role in self.WAREHOUSE_ROLES:
            if not self.warehouse_id:
                errors["warehouse"] = "仓库角色必须指定仓库范围。"
            if self.owner_id:
                errors["owner"] = "仓库角色不能指定货主范围。"
        elif self.role in self.OWNER_ROLES:
            if not self.owner_id:
                errors["owner"] = "货主角色必须指定货主范围。"
            if self.warehouse_id:
                errors["warehouse"] = "货主角色不能指定仓库范围。"
        else:
            errors["role"] = "未知角色。"

        if self.is_active and self.user_id:
            other_active = type(self).objects.filter(
                user_id=self.user_id,
                is_active=True,
            ).exclude(pk=self.pk)
            conflicting_roles = other_active.exclude(role=self.role)
            if conflicting_roles.exists():
                errors["role"] = "同一用户不能同时启用多个业务角色。"
            elif self.role != self.Role.WAREHOUSE_BOSS and other_active.exists():
                errors["role"] = "除仓库老板外，每个用户只能有一个活动范围。"

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        target = self.warehouse if self.warehouse_id else self.owner
        return f"{self.user} / {self.get_role_display()} / {target}"

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


class AuditEventQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError("AuditEvent is append-only and cannot be updated.")

    def delete(self):
        raise ValidationError("AuditEvent is append-only and cannot be deleted.")


class AuditEvent(models.Model):
    """Append-only security and business audit event.

    ``SystemLog`` is retained for compatibility.  New sensitive operations use
    this structured, tamper-evident record instead.
    """

    occurred_at = models.DateTimeField(auto_now_add=True, db_index=True)
    actor = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_events",
    )
    username = models.CharField(max_length=150, blank=True, default="", db_index=True)
    action = models.CharField(max_length=40, db_index=True)
    module = models.CharField(max_length=80, db_index=True)
    object_type = models.CharField(max_length=100, blank=True, default="")
    object_id = models.CharField(max_length=80, blank=True, default="")
    owner = models.ForeignKey(
        "baseinfo.Owner", on_delete=models.PROTECT, null=True, blank=True
    )
    warehouse = models.ForeignKey(
        "locations.Warehouse", on_delete=models.PROTECT, null=True, blank=True
    )
    request_id = models.CharField(max_length=64, blank=True, default="", db_index=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    method = models.CharField(max_length=12, blank=True, default="")
    path = models.CharField(max_length=500, blank=True, default="")
    succeeded = models.BooleanField(default=True, db_index=True)
    before = models.JSONField(default=dict, blank=True)
    after = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    event_hash = models.CharField(max_length=64, unique=True, editable=False)

    objects = AuditEventQuerySet.as_manager()

    class Meta:
        verbose_name = "不可变审计事件"
        verbose_name_plural = "不可变审计事件"
        ordering = ("-occurred_at", "-id")
        indexes = [
            models.Index(fields=("module", "action", "occurred_at"), name="idx_audit_mod_action_time"),
            models.Index(fields=("owner", "occurred_at"), name="idx_audit_owner_time"),
            models.Index(fields=("warehouse", "occurred_at"), name="idx_audit_wh_time"),
            models.Index(fields=("object_type", "object_id"), name="idx_audit_object"),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("AuditEvent is append-only and cannot be updated.")
        if not self.event_hash:
            raise ValidationError("AuditEvent requires a signed event_hash.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("AuditEvent is append-only and cannot be deleted.")
