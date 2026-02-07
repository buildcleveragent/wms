# tms/models.py
from django.db import models
from allapp.core.models import BaseModel  # 含 created_at/created_by/updated_at/updated_by 等
from allapp.baseinfo.models import Owner,Driver,Vehicle,CarrierCompany
from allapp.locations.models import Warehouse
from wmsmaster import settings


class TrackingDevice(BaseModel):
    """
    纯硬件 GPS 设备（“GPS 硬件另计”），绑定车辆或司机均可。
    手机定位不需要这条记录（见 DriverDevice）。
    """
    class SourceType(models.TextChoices):
        GPS_HARDWARE = "gps_hw", "GPS硬件"
        OBD = "obd", "OBD"
        OTHER = "other", "其它"

    imei = models.CharField(max_length=64, unique=True)  # 设备唯一识别
    source_type = models.CharField(max_length=16, choices=SourceType.choices, default=SourceType.GPS_HARDWARE)
    CarrierCompany = models.ForeignKey(CarrierCompany, on_delete=models.PROTECT, null=True, blank=True)
    vehicle = models.ForeignKey(Vehicle, on_delete=models.PROTECT, null=True, blank=True, related_name="tracking_devices")
    remark = models.CharField(max_length=255, blank=True, default="")

    def __str__(self):
        return self.imei

class DriverDevice(BaseModel):
    """
    司机手机端设备（App 安装后生成 device_id）；用于区分多设备/幂等。
    """
    driver = models.ForeignKey(Driver, on_delete=models.PROTECT, related_name="devices")
    device_id = models.CharField(max_length=128)  # 由移动端生成/上报
    brand = models.CharField(max_length=64, blank=True, default="")
    model = models.CharField(max_length=64, blank=True, default="")
    os = models.CharField(max_length=64, blank=True, default="")
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = [("driver", "device_id")]


class DriverShift(BaseModel):
    """
    上/下班登记；移动端“打卡”。幂等：同一 request_id 只生效一次。
    """
    class Action(models.TextChoices):
        CLOCK_IN = "in", "上班"
        CLOCK_OUT = "out", "下班"

    driver = models.ForeignKey(Driver, on_delete=models.PROTECT, related_name="shifts")
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="driver_shifts", null=True, blank=True,editable=False,default=settings.DEFAULT_WAREHOUSE_ID)
    action = models.CharField(max_length=8, choices=Action.choices)
    at = models.DateTimeField()  # 客户端时间戳
    geo_lat = models.DecimalField(max_digits=10, decimal_places=6, null=True, blank=True)
    geo_lng = models.DecimalField(max_digits=10, decimal_places=6, null=True, blank=True)
    device = models.ForeignKey(DriverDevice, on_delete=models.PROTECT, null=True, blank=True)
    request_id = models.CharField(max_length=64, db_index=True)  # 幂等键（移动端生成）

    class Meta:
        indexes = [models.Index(fields=["driver", "at"]), models.Index(fields=["action", "at"])]
        unique_together = [("driver", "request_id")]  # 幂等


class DeliveryTask(BaseModel):
    """
    派单/任务：排单 -> 司机受理 -> 在途/完成/取消。
    可关联出库单/运单等外部对象：使用 ref_* 字段解耦。
    """
    class TaskStatus(models.TextChoices):
        NEW = "new", "新建"
        ASSIGNED = "assigned", "已指派"
        ACCEPTED = "accepted", "已受理"
        IN_TRANSIT = "in_transit", "在途"
        ARRIVED = "arrived", "已到达"
        COMPLETED = "completed", "已完成"
        CANCELLED = "cancelled", "已取消"

    owner = models.ForeignKey(Owner, on_delete=models.PROTECT, related_name="delivery_tasks")
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="delivery_tasks",editable=False,default=settings.DEFAULT_WAREHOUSE_ID)
    CarrierCompany = models.ForeignKey(CarrierCompany, on_delete=models.PROTECT, related_name="delivery_tasks", null=True, blank=True)
    driver = models.ForeignKey(Driver, on_delete=models.PROTECT, related_name="delivery_tasks", null=True, blank=True)
    vehicle = models.ForeignKey(Vehicle, on_delete=models.PROTECT, related_name="delivery_tasks", null=True, blank=True)

    # 外部业务引用（如出库单/运单/波次等）
    ref_type = models.CharField(max_length=64, blank=True, default="")   # e.g. "OutboundOrder"
    ref_id = models.CharField(max_length=64, blank=True, default="")
    ref_no = models.CharField(max_length=64, db_index=True, blank=True, default="")

    status = models.CharField(max_length=16, choices=TaskStatus.choices, default=TaskStatus.NEW)
    planned_depart_at = models.DateTimeField(null=True, blank=True)
    planned_arrive_at = models.DateTimeField(null=True, blank=True)
    departed_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    priority = models.PositiveSmallIntegerField(default=5)  # 1 高 - 9 低
    remark = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        indexes = [
            models.Index(fields=["owner", "status"]),
            models.Index(fields=["status", "planned_depart_at"]),
            models.Index(fields=["driver", "status"]),
        ]


class TaskStop(BaseModel):
    """
    线路/站点（多段配送）：每单可有多个送达点（门店/仓/客户）。
    """
    class StopStatus(models.TextChoices):
        NEW = "new", "待执行"
        ENROUTE = "enroute", "在途"
        ARRIVED = "arrived", "已到达"
        SERVICING = "servicing", "服务中"
        DONE = "done", "完成"
        EXCEPTION = "exception", "异常"

    task = models.ForeignKey(DeliveryTask, on_delete=models.PROTECT, related_name="stops")
    seq = models.PositiveSmallIntegerField()  # 线路顺序
    consignee_name = models.CharField(max_length=128)
    consignee_phone = models.CharField(max_length=32, blank=True, default="")
    address = models.CharField(max_length=255)
    geo_lat = models.DecimalField(max_digits=10, decimal_places=6, null=True, blank=True)
    geo_lng = models.DecimalField(max_digits=10, decimal_places=6, null=True, blank=True)

    status = models.CharField(max_length=16, choices=StopStatus.choices, default=StopStatus.NEW)
    planned_arrive_at = models.DateTimeField(null=True, blank=True)
    actual_arrive_at = models.DateTimeField(null=True, blank=True)
    actual_leave_at = models.DateTimeField(null=True, blank=True)

    # 映射外部明细（如出库单号/行号等）
    ref_type = models.CharField(max_length=64, blank=True, default="")
    ref_id = models.CharField(max_length=64, blank=True, default="")
    ref_no = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        unique_together = [("task", "seq")]
        indexes = [
            models.Index(fields=["task", "seq"]),
            models.Index(fields=["status"]),
        ]


class TrackingPoint(BaseModel):
    """
    在途跟踪：手机定位（driver_device）或 GPS 硬件（tracking_device）上报。
    幂等：针对同一 device + request_id 保证唯一。
    """
    class Source(models.TextChoices):
        PHONE = "phone", "手机定位"
        GPS_HW = "gps_hw", "GPS硬件"

    task = models.ForeignKey(DeliveryTask, on_delete=models.PROTECT, related_name="tracking_points")
    stop = models.ForeignKey(TaskStop, on_delete=models.PROTECT, null=True, blank=True, related_name="tracking_points")
    source = models.CharField(max_length=16, choices=Source.choices, default=Source.PHONE)

    driver_device = models.ForeignKey(DriverDevice, on_delete=models.PROTECT, null=True, blank=True, related_name="tracking_points")
    tracking_device = models.ForeignKey(TrackingDevice, on_delete=models.PROTECT, null=True, blank=True, related_name="tracking_points")

    lat = models.DecimalField(max_digits=10, decimal_places=6)
    lng = models.DecimalField(max_digits=10, decimal_places=6)
    speed = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)  # km/h
    heading = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)  # 航向角
    located_at = models.DateTimeField()  # 位置对应的时间（客户端/GPS）
    request_id = models.CharField(max_length=64)  # 幂等键（按设备生成）

    class Meta:
        indexes = [models.Index(fields=["task", "located_at"]), models.Index(fields=["source", "located_at"])]
        unique_together = [
            ("driver_device", "request_id"),
            ("tracking_device", "request_id"),
        ]


class DeliveryPreSign(BaseModel):
    """
    送货预签收：到站前或现场先行确认/预确认收货信息（不等于最终签收）。
    """
    class Status(models.TextChoices):
        PENDING = "pending", "待确认"
        CONFIRMED = "confirmed", "已确认"
        REJECTED = "rejected", "已驳回"

    task = models.ForeignKey(DeliveryTask, on_delete=models.PROTECT, related_name="pre_signs")
    stop = models.ForeignKey(TaskStop, on_delete=models.PROTECT, related_name="pre_signs")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)

    consignee_contact = models.CharField(max_length=64, blank=True, default="")
    consignee_note = models.CharField(max_length=255, blank=True, default="")
    photo_urls = models.JSONField(default=list, blank=True)   # 预签收凭证照片（OSS/MinIO 链接）
    files = models.JSONField(default=list, blank=True)        # 其它附件

    submitted_at = models.DateTimeField(auto_now_add=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    request_id = models.CharField(max_length=64, db_index=True)

    class Meta:
        indexes = [models.Index(fields=["task", "stop", "status"])]
        unique_together = [("stop", "request_id")]  # 幂等


class ExceptionReport(BaseModel):
    """
    异常反馈：破损、少货、客户不在、道路拥堵、拒收等。
    """
    class Level(models.TextChoices):
        INFO = "info", "提示"
        MINOR = "minor", "一般"
        MAJOR = "major", "严重"
        CRITICAL = "critical", "致命"

    class Category(models.TextChoices):
        DAMAGE = "damage", "破损"
        SHORTAGE = "shortage", "少货"
        DELAY = "delay", "延误"
        REFUSAL = "refusal", "拒收"
        TRAFFIC = "traffic", "交通/封路"
        OTHER = "other", "其它"

    task = models.ForeignKey(DeliveryTask, on_delete=models.PROTECT, related_name="exceptions")
    stop = models.ForeignKey(TaskStop, on_delete=models.PROTECT, null=True, blank=True, related_name="exceptions")
    category = models.CharField(max_length=16, choices=Category.choices, default=Category.OTHER)
    level = models.CharField(max_length=16, choices=Level.choices, default=Level.MINOR)
    description = models.TextField(blank=True, default="")
    photo_urls = models.JSONField(default=list, blank=True)
    files = models.JSONField(default=list, blank=True)
    occurred_at = models.DateTimeField()
    request_id = models.CharField(max_length=64, db_index=True)  # 幂等（移动端提交）

    class Meta:
        indexes = [models.Index(fields=["task", "occurred_at"]), models.Index(fields=["category", "level"])]
        unique_together = [("task", "request_id")]
