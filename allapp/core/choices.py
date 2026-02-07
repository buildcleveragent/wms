# allapp/core/choices.py
from django.db import models
from django.utils.translation import gettext_lazy as _

ZONE_TYPE_CHOICES = [("RECEIVING","收货区"), ("STORAGE", "存货区"), ("PICKING", "拣货区"),
    ("RETURN", "退货区"),("SHIPPING", "发运区"),("FULLCASE", "整件区"), ("SPLIT", "拆零区"), ("OTHER", "其他")]


class ZoneType(models.IntegerChoices):
    STORAGE = 1, _("拣选区")
    PICK = 2, _("存储区")
    RECEIVING = 3, _("收货区")
    SHIPPING = 4, _("发运区")
    RETURN = 5, _("退货区")
    FULLCASE = 6, _("整件区")
    SPLIT = 7, _("拆零区")
    damaged = 8, _("破损区")
    OTHER = 9, _("其他")

class SubmitStatus(models.TextChoices):
    DRAFT = "draft", "未提交"
    SUBMITTED = "submitted", "已提交"

class ApproveStatus(models.TextChoices):
    PENDING = "pending", "待审核"
    APPROVED = "approved", "已审核"
    REJECTED = "rejected", "已驳回"

class TransferStatus(models.TextChoices):
    NEW = "new", "新建"
    OUTBOUND_APPROVED = "outbound_approved", "调拨出库已审批"
    OUTBOUND_CONFIRMED = "outbound_confirmed", "调拨出库已确认"
    INBOUND_APPROVED = "inbound_approved", "调拨入库已审批"
    INBOUND_CONFIRMED = "inbound_confirmed", "调拨入库已确认"
    COMPLETED = "completed", "已完成"
    CANCELLED = "cancelled", "已作废"

class AdjustmentOpType(models.TextChoices):
    DAMAGE = "damage", "报损"
    GAIN = "gain", "升溢"

class CountScope(models.TextChoices):
    BY_LOCATION = "by_location", "按仓位盘点"
    BY_PRODUCT = "by_product", "按商品盘点"
    BY_ZONE_TYPE = "by_zone_type", "按库区盘点"

class CountStatus(models.TextChoices):
    NEW = "new", "新建"
    COUNTING = "counting", "盘点录入中"
    RECONCILING = "reconciling", "复核/对账中"
    APPROVED = "approved", "已审核"
    COMPLETED = "completed", "已完成"
    CANCELLED = "cancelled", "已作废"

class LockDocType(models.TextChoices):
    LOCK = "lock", "锁定"
    RELEASE = "release", "释放"

# allapp/core/choices.py 追加
class TxType(models.TextChoices):
    RECEIVE = "RECEIVE", "收货"
    PUTAWAY = "PUTAWAY", "上架"
    ALLOCATE= "ALLOCATE", "分配/锁定"
    PICK = "PICK", "拣货"
    PACK = "PACK", "打包"
    SHIP = "SHIP", "发运"
    DELIVER = "DELIVER", "签收"
    MOVE = "MOVE", "移库"
    ADJ_GAIN = "ADJ_GAIN", "升溢"
    ADJ_LOSS = "ADJ_LOSS", "报损"
    LOCK = "LOCK", "锁定"
    UNLOCK = "UNLOCK", "释放"


class InvTxType(models.TextChoices):
        MOVE_IN = "MOVE_IN", "移入"
        MOVE_OUT = "MOVE_OUT", "移出"
        RECEIVE = "RECEIVE", "入"
        ISSUE = "ISSUE", "出"
        ADJ_GAIN = "ADJ_GAIN", "调整增"
        ADJ_LOSS = "ADJ_LOSS", "调整减"

class ShipmentStatus(models.TextChoices):
    NOT_SHIPPED = "NOT_SHIPPED", "未发运"
    SHIPPED = "SHIPPED", "已发运"
    DELIVERED = "DELIVERED", "已送达"
    CANCELLED = "CANCELLED", "已取消"

class PickingStatus(models.TextChoices):
    PENDING = "PENDING", "待拣货"
    WORKING = "WORKING", "拣货中"
    DONE = "DONE", "已完成"

class DeliveryMethod(models.TextChoices):
    CIF = "CIF", "到岸"
    DISPATCH = "DISPATCH", "派车"
    SELF_PICKUP = "SELF_PICKUP", "自提"

class CreateMode(models.TextChoices):
    MANUAL = "MANUAL", "手工创建"
    IMPORT = "IMPORT", "导入"
    API = "API", "接口创建"
    SYSTEM = "SYSTEM", "系统创建"

