from django.db import models

class ChargeType(models.TextChoices):
    RECEIVE = "RECEIVE", "收货"
    PUTAWAY = "PUTAWAY", "上架/移库入"
    RELOC = "RELOC", "移库"
    PICK = "PICK", "拣货"
    REVIEW = "REVIEW", "复核"
    PACK = "PACK", "打包"
    LOAD = "LOAD", "装车"
    DISPATCH = "DISPATCH", "发运/订单处理"
    COUNT = "COUNT", "盘点"
    ADJUST = "ADJUST", "调整"
    STORAGE = "STORAGE", "仓储/保管"

class CalcMethod(models.TextChoices):
    PER_TASK = "PER_TASK", "按任务"
    PER_LINE = "PER_LINE", "按任务行"
    PER_QTY_BASE = "PER_QTY_BASE", "按基础数量"
    PER_QTY_ABSDEL = "PER_QTY_ABSDEL", "按数量差额绝对值"
    PER_ORDER = "PER_ORDER", "按订单"
    PER_ORDER_LINE = "PER_ORDER_LINE", "按订单行"
    PER_PARCEL = "PER_PARCEL", "按包裹件数"
    PER_DAY_ONHAND_BASE = "PER_DAY_ONHAND_BASE", "按日在库(基础数)"
    PER_PALLET_DAY = "PER_PALLET_DAY", "按托盘位/天"
    PER_CBM_DAY = "PER_CBM_DAY", "按体积CBM/天"
    PER_AREA_MONTH = "PER_AREA_MONTH", "按面积㎡/月"
    PERCENT_OF_ORDER_AMOUNT = "PERCENT_OF_ORDER_AMOUNT", "按订单金额比例"

class AccrualStatus(models.TextChoices):
    OPEN = "OPEN", "未锁定"
    LOCKED = "LOCKED", "已锁定"
    INVOICED = "INVOICED", "已开票"
    VOID = "VOID", "作废"

class PeriodStatus(models.TextChoices):
    OPEN = "OPEN", "开账"
    CLOSED = "CLOSED", "关账"
    INVOICED = "INVOICED", "已开票"

class BillStatus(models.TextChoices):
    DRAFT = "DRAFT", "草稿"
    ISSUED = "ISSUED", "已开票"
    PAID = "PAID", "已收款"
    VOID = "VOID", "作废"

class MetricType(models.TextChoices):
    AREA_M2 = "AREA_M2", "面积(㎡)"
    CBM = "CBM", "体积(CBM)"
    PALLET = "PALLET", "托盘位(个)"
    ORDER_AMT = "ORDER_AMT", "订单金额"

class LadderMode(models.TextChoices):
    WHOLE = "WHOLE", "整档全量价"
    INCREMENTAL = "INCREMENTAL", "分段累进"

# —— 新增：封顶与打包的口径/类型 —— #
class CapMode(models.TextChoices):
    NONE = "NONE", "不封顶"
    PER_DAY = "PER_DAY", "按天封顶"
    PER_PERIOD = "PER_PERIOD", "按账期封顶"

class BundleScope(models.TextChoices):
    NONE = "NONE", "不打包"
    PER_DAY = "PER_DAY", "按天打包"
    PER_PERIOD = "PER_PERIOD", "按账期打包"

class BundleType(models.TextChoices):
    CAP = "CAP", "打包上限"       # 总额不超过打包价
    FIXED = "FIXED", "固定打包价"  # 有发生则总额=打包价
