# models.py for baseinfo app
from allapp.core.models import BaseModel, AddressMixin
from django.conf import settings
from django.core.validators import MinValueValidator, RegexValidator
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

phone_validator = RegexValidator(
    regex=r'^[0-9+\-\s()]{5,20}$',
    message="电话仅允许数字、+、-、空格、()，长度5-20。"
)

mobile_validator = RegexValidator(
    regex=r'^\+?[1-9]\d{1,14}$',  # E.164 international phone number format
    message="请输入有效的手机号码，支持国际格式。"
)

class Owner(BaseModel, AddressMixin):
    # —— 基本档案 ——
    name = models.CharField("货主名称", max_length=30, unique=True)
    code = models.CharField("货主代码", max_length=10, unique=True)
    contact_person = models.CharField("联系人", max_length=10, blank=True, null=True)
    phone = models.CharField("联系电话", max_length=20, blank=True, null=True)
    sms_mobile = models.CharField("短信接收号码", max_length=20, blank=True, null=True)
    bank_account = models.CharField("银行账号", max_length=50, blank=True, null=True)
    wx = models.CharField("微信", max_length=20, blank=True, null=True)
    qq = models.CharField("QQ", max_length=20, blank=True, null=True)
    email = models.EmailField("Email", blank=True, null=True)

    # 证照信息ImageField(upload_to='owner_pictures/', blank=True, null=True)
    business_license = models.CharField("营业执照", max_length=100, blank=True, null=True)
    tax_registration = models.CharField("税务登记证", max_length=100, blank=True, null=True)
    organization_code = models.CharField("组织机构代码证", max_length=100, blank=True, null=True)

    class Meta:
        verbose_name = "货主"
        verbose_name_plural = "货主管理"
        ordering = ["name"]

        permissions = [
            ("asownermanager",  _("货主管理员权")),
        ]

        indexes = [
            models.Index(fields=['name', 'code']),
        ]
    def __str__(self): return self.name
#-----------------------------------------------
# 业务 models（示例放在各自 app 的 models.py）
# ========= 客户（终端）=========

class Customer(BaseModel, AddressMixin):
    """
    终端客户。多货主隔离：同一Owner下code、external_code唯一。
    说明：
    - 不采用全局唯一code，而是 (owner, code) 唯一：便于3PL场景不同货主可复用相同编码体系。
    - 通过 save() 统一将空字符串标准化为 NULL，避免 MySQL 下 unique + '' 产生“看似有值实为空”的问题。
    """

    code = models.CharField("客户编号", max_length=30, db_index=True)
    name = models.CharField("客户名称", max_length=50, db_index=True)

    owner = models.ForeignKey(
        'Owner',
        on_delete=models.PROTECT,            # 避免误删 Owner 级联清空
        related_name="customers",            # 规范：复数
        verbose_name="货主",
        db_index=True,
    )

    salesperson = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="业务员",
        on_delete=models.PROTECT,
        related_name="customers",
    )

    # 联系方式
    contact_person = models.CharField("联系人", max_length=80, blank=True, null=True)
    phone  = models.CharField("联系电话", max_length=20, blank=True, null=True)
    mobile = models.CharField("移动电话", max_length=20, blank=True, null=True)
    qq     = models.CharField("QQ", max_length=20, blank=True, null=True)
    email  = models.EmailField("电子邮箱", blank=True, null=True)

    # 配送与分级
    area = models.CharField("客户区域", max_length=60, blank=True, null=True, db_index=True)
    delivery_route = models.CharField("配送线路", max_length=60, blank=True, null=True)
    delivery_seq = models.PositiveIntegerField("配送序号", blank=True, null=True, validators=[MinValueValidator(1)])
    level = models.PositiveSmallIntegerField("客户等级", blank=True, null=True, validators=[MinValueValidator(1)])

    # 结算/对账
    bank_name    = models.CharField("银行", max_length=60, blank=True, null=True)
    bank_account = models.CharField("银行账号", max_length=50, blank=True, null=True)

    # 业务属性
    delivery_distance_km = models.DecimalField(
        "配送距离(公里)", max_digits=6, decimal_places=2, blank=True, null=True,
        validators=[MinValueValidator(0)]
    )
    promised_days = models.PositiveSmallIntegerField("应达天数", blank=True, null=True, validators=[MinValueValidator(0)])

    # 外部对照
    external_code = models.CharField("客户接口对照编号", max_length=60, blank=True, null=True)

    class Meta:
        # db_table = "customer"
        verbose_name = "终端客户"
        verbose_name_plural = "终端客户管理"

        # 典型列表页排序：按owner再按code
        ordering = ["owner_id", "code"]

        # ---- 索引（INDEX）与约束（CONSTRAINTS） ----
        # 注意：MySQL 8 不支持“条件唯一索引”；以下均为可在 MySQL 8.0.43 上直接生效的定义
        constraints = [
            # 1) (owner, code) 唯一：多货主隔离，允许不同货主用相同code
            models.UniqueConstraint(
                fields=["owner", "code"],
                name="uq_customer_owner_code",
            ),
            # 2) (owner, external_code) 唯一：外部系统对照码在同一货主内唯一
            #    允许 NULL 重复（MySQL 对 NULL 不参与唯一性），我们会在 save() 将空字符串标准化为 NULL
            models.UniqueConstraint(
                fields=["owner", "external_code"],
                name="uq_cust_owner_ext_code",
            ),
            # 3) 非负/非空检查（MySQL 8.0.16+生效；8.0.43可强制执行）
            models.CheckConstraint(
                check=Q(delivery_distance_km__gte=0) | Q(delivery_distance_km__isnull=True),
                name="chk_cust_delivery_dist_pos",
            ),
            models.CheckConstraint(
                check=Q(promised_days__gte=0) | Q(promised_days__isnull=True),
                name="chk_cust_pro_days_pos",
            ),


        ]

        indexes = [
            # 4) 常用查询：按 (owner, name) 前缀匹配/排序
            models.Index(fields=["owner", "name"], name="idx_customer_owner_name"),
            # 5) 常用查询：按 (owner, code) 精确查
            models.Index(fields=["owner", "code"], name="idx_customer_owner_code"),
            # 6) 业务员+货主维度的过滤
            models.Index(fields=["owner", "salesperson"], name="idx_customer_owner_sales"),
            # 7) 配送线路内按序号排序/查询
            models.Index(fields=["owner", "delivery_route", "delivery_seq"], name="idx_customer_route_seq"),
            # 8) 邮箱/手机在同一货主下检索（如导入去重、客户查找）
            # models.Index(fields=["owner", "email"],  name="idx_customer_owner_email"),
            models.Index(fields=["owner", "mobile"], name="idx_customer_owner_mobile"),
 
        ]

    def __str__(self):
        return f"{self.code} - {self.name}"

    # --- 规范化：将空字符串统一转为 None，避免 unique + '' 的边界问题 ---
    def _normalize_blanks_to_nulls(self):
        str_fields = [
            "area", "delivery_route", "contact_person",
            "phone", "mobile", "qq", "email",
            "bank_name", "bank_account", "external_code",
        ]
        for f in str_fields:
            v = getattr(self, f, None)
            if isinstance(v, str):
                v2 = v.strip()
                setattr(self, f, v2 or None)

        # code/name 也去首尾空白
        if isinstance(self.code, str):
            self.code = self.code.strip()
        if isinstance(self.name, str):
            self.name = self.name.strip()

    def save(self, *args, **kwargs):
        self._normalize_blanks_to_nulls()
        super().save(*args, **kwargs)

class Employee(BaseModel):
    code = models.CharField("职员代码", max_length=30, unique=True, db_index=True)
    name = models.CharField("姓名", max_length=50, db_index=True)
    gender = models.CharField("性别", max_length=1, choices=[("M", "男"), ("F", "女"), ("O", "其他")], blank=True, null=True)
    department = models.CharField("部门", max_length=60, blank=True, null=True)
    position = models.CharField("职务", max_length=60, blank=True, null=True)
    phone = models.CharField("联系电话", max_length=20, blank=True, null=True, validators=[phone_validator])
    mobile = models.CharField("移动电话", max_length=20, blank=True, null=True, validators=[phone_validator])
    birthday = models.DateField("出生年月日", blank=True, null=True)
    education = models.CharField("文化程度", max_length=30, blank=True, null=True)
    id_number = models.CharField("身份证号", max_length=30, blank=True, null=True)
    bank_name = models.CharField("银行", max_length=60, blank=True, null=True)
    bank_account = models.CharField("银行账号", max_length=50, blank=True, null=True)
    address = models.CharField("住址", max_length=200, blank=True, null=True)
    email = models.EmailField("Email", blank=True, null=True)
    hire_date = models.DateField("入职日期", blank=True, null=True)
    leave_date = models.DateField("离职日期", blank=True, null=True)
    warehouse = models.ForeignKey(
        'locations.Warehouse',
        verbose_name="所属大仓",
        on_delete=models.PROTECT,  # 也可以使用 models.PROTECT, 根据业务需求来选择
        blank=True, null=True,
        related_name="employees",  # 如果需要通过 Warehouse 模型反向查找关联员工
    )
    owner = models.ForeignKey(
        'Owner',
        on_delete=models.PROTECT,            # 避免误删 Owner 级联清空
        related_name="employee",            # 规范：复数
        verbose_name="所属货主",
         blank = True, null = True,
        db_index=True,
    )
    class Meta:
        verbose_name = "公司职员"
        verbose_name_plural = "公司职员管理"
        ordering = ["code"]

        # ---- 索引与约束 ----
        indexes = [
            models.Index(fields=["department"], name="idx_employee_department"),
            models.Index(fields=["mobile"], name="idx_employee_mobile"),
        ]
        constraints = [
            models.UniqueConstraint(fields=["id_number"], name="uq_employee_id_number"),
        ]

    def __str__(self):
        return f"{self.code} - {self.name}"

class CarrierCompany(BaseModel, AddressMixin):
    name = models.CharField(
        "承运公司名称",
        max_length=30,
        unique=True,
        db_index=True
    )

    manager = models.CharField("负责人", max_length=50, db_index=True)

    mobile = models.CharField(
        "手机号码",
        max_length=20,
        blank=True,
        null=True,
        # validators=[mobile_validator]
    )

    phone = models.CharField(
        "电话号码",
        max_length=20,
        blank=True,
        null=True
    )

    warehouse = models.ForeignKey(
        'locations.Warehouse',
        verbose_name="所属大仓",
        on_delete=models.PROTECT,  # 也可以使用 models.CASCADE, 根据业务需求来选择
        blank=True, null=True,
        related_name="carrier_company",  # 如果需要通过 Warehouse 模型反向查找关联员工
    )
    owner = models.ForeignKey(
        'Owner',
        on_delete=models.PROTECT,  # 避免误删 Owner 级联清空
        related_name="carrier_company",  # 规范：复数
        verbose_name="所属货主",
        blank=True, null=True,
        db_index=True,
    )

    class Meta:
        verbose_name = "承运公司"
        verbose_name_plural = "承运公司档案"
        ordering = ["name"]
        indexes = [
            models.Index(fields=["phone"], name="idx_carrier_phone"),
            models.Index(fields=["mobile"], name="idx_carrier_mobile"),
            models.Index(fields=["owner"], name="idx_carrier_owner")  # Add index on owner field
        ]
        # constraints = [
        #     models.UniqueConstraint(fields=["mobile"], name="uq_carrier_mobile"),  # Ensure mobile is unique
        #     models.CheckConstraint(
        #         check=models.Q(mobile__regex=r'^\+?[1-9]\d{1,14}$'),
        #         name="chk_carrier_mobile_format",
        #     )  # Validate mobile number format
        # ]

    def __str__(self):
        return self.name

class Supplier(BaseModel, AddressMixin):
    owner = models.ForeignKey(Owner, on_delete=models.PROTECT, related_name="suppliers", verbose_name="货主", db_index=True,)
    code = models.CharField("供应商代码", max_length=100,  blank=True, null=True)
    name = models.CharField("供应商名称", max_length=30)
    contact_person = models.CharField("联系人", max_length=10, blank=True, null=True)
    phone = models.CharField("联系电话", max_length=20, blank=True, null=True, validators=[phone_validator])
    bank_account = models.CharField("银行账号", max_length=50, blank=True, null=True)
    qq = models.CharField("QQ", max_length=20, blank=True, null=True)
    email = models.EmailField("Email", blank=True, null=True)
    yb = models.CharField("邮编", max_length=20, blank=True, null=True)

    class Meta:
        verbose_name = "供应商"
        verbose_name_plural = "供应商管理"
        ordering = ["name"]
        # Composite unique constraint to ensure unique supplier names per owner
        constraints = [
            models.UniqueConstraint(fields=["owner", "name"], name="uq_supplier_owner_name")
        ]

    def __str__(self):
        return self.name

class Driver(BaseModel, AddressMixin):
    carrier_company = models.ForeignKey("CarrierCompany", verbose_name="归属承运公司", on_delete=models.PROTECT, related_name="drivers",blank=True, null=True, )
    name = models.CharField("姓名", max_length=10)
    gender = models.CharField("性别", max_length=1, choices=[("M", "男"), ("F", "女")], blank=True, null=True)
    mobile = models.CharField("手机号码", max_length=20, blank=True, null=True, validators=[phone_validator])
    phone = models.CharField("电话号码", max_length=20, blank=True, null=True)
    id_number = models.CharField("身份证号码", max_length=30, blank=True, null=True)
    driver_license_no = models.CharField("驾驶证号码", max_length=30, blank=True, null=True)
    driver_license_expiry = models.DateField("驾驶证有效期", blank=True, null=True)

    class Meta:
        verbose_name = "司机"
        verbose_name_plural = "司机档案"
        ordering = ["name"]

    def __str__(self):
        return f"{self.name}（{self.mobile or '-'}）"

class Vehicle(BaseModel):
    carrier_company = models.ForeignKey("CarrierCompany", verbose_name="归属承运公司", on_delete=models.PROTECT, related_name="vehicles", blank=True, null=True)
    license_no = models.CharField("行驶证号", max_length=40, blank=True, null=True)
    plate_no = models.CharField("车牌号", max_length=20, unique=True, db_index=True)
    use_type = models.CharField("用车类型", max_length=30, blank=True, null=True)
    model_name = models.CharField("车型", max_length=50, blank=True, null=True)
    category = models.CharField("车辆分类", max_length=50, blank=True, null=True)
    vin = models.CharField("车架号码", max_length=40, blank=True, null=True)
    engine_no = models.CharField("发动机号", max_length=40, blank=True, null=True)
    trailer_no = models.CharField("挂车号码", max_length=20, blank=True, null=True)
    insurance_no = models.CharField("保险单号", max_length=40, blank=True, null=True)
    operation_permit_no = models.CharField("营运证号", max_length=40, blank=True, null=True)
    payload_kg = models.DecimalField("可载重量(kg)", max_digits=10, decimal_places=2, blank=True, null=True)
    surcharge_cert_no = models.CharField("附加费证号", max_length=40, blank=True, null=True)
    length_m = models.DecimalField("车长(米)", max_digits=6, decimal_places=2, blank=True, null=True)
    volume_m3 = models.DecimalField("可载体积(m³)", max_digits=10, decimal_places=3, blank=True, null=True)
    maintenance_km = models.PositiveIntegerField("保养公里数", blank=True, null=True)
    status = models.CharField("车辆状态", max_length=20, choices=[("IDLE", "空闲"), ("ON_DUTY", "在岗"), ("MAINT", "保养"), ("DISABLED", "停用")], default="IDLE")
    warranty_expiry = models.DateField("保修单到期日", blank=True, null=True)
    maintenance_due = models.DateField("保养到期日", blank=True, null=True)
    operation_permit_annual_due = models.DateField("营运证年审到期日", blank=True, null=True)
    annual_inspection_due = models.DateField("年检到期日", blank=True, null=True)
    driver = models.ForeignKey("Driver", verbose_name="司机", on_delete=models.PROTECT, blank=True, null=True, related_name="vehicles")
    remark = models.CharField("备注", max_length=200, blank=True, null=True)

    class Meta:
        verbose_name = "车辆"
        verbose_name_plural = "车辆档案"
        ordering = ["plate_no"]

    def __str__(self):
        return self.plate_no

class Route(BaseModel):
    code = models.CharField("线路编号", max_length=30, unique=True, db_index=True)
    name = models.CharField("线路名称", max_length=50)
    remark = models.CharField("备注", max_length=100, blank=True, null=True)

    class Meta:
        verbose_name = "线路"
        verbose_name_plural = "线路管理"
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} - {self.name}"

class DictCategory(BaseModel):
    code = models.CharField("分类编码", max_length=50, unique=True, db_index=True)
    name = models.CharField("分类名称", max_length=100)
    is_locked = models.BooleanField("锁定（禁止删除）", default=False)

    class Meta:
        verbose_name = "数据字典分类"
        verbose_name_plural = "数据字典分类"
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} - {self.name}"

class DictItem(BaseModel):
    category = models.ForeignKey("DictCategory", verbose_name="所属分类", on_delete=models.PROTECT, related_name="items")
    code = models.CharField("条目编码", max_length=50, db_index=True)
    name = models.CharField("条目名称", max_length=120)
    value = models.CharField("条目值", max_length=200, blank=True, null=True)
    extra = models.JSONField("扩展数据(JSON)", blank=True, null=True)
    sort_order = models.PositiveIntegerField("排序号", default=0)

    class Meta:
        verbose_name = "数据字典条目"
        verbose_name_plural = "数据字典条目"
        unique_together = (("category", "code"),)
        ordering = ["category_id", "sort_order", "code"]

    def __str__(self):
        return f"{self.category.code}:{self.code} - {self.name}"
