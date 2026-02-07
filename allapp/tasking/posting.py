# tasking/posting.py
from django.db import transaction
from decimal import Decimal
from .models import WmsTask, WmsTaskLine

def _fp_task(task: WmsTask) -> str:
    return f"TASK-POST-{task.pk}-{task.approved_at and task.approved_at.isoformat()}"

@transaction.atomic
def post_receive_for_task(*, task: WmsTask, by_user=None):
    """
    将任务内所有行的收货结果汇总为一张过账单（GOOD/DMG/REJ 拆行）。
    幂等指纹：任务ID + 审核时间；再次过账不会重复。
    """
    fp = _fp_task(task)
    if InvPosting.objects.filter(fp=fp).exists():
        return

    posting = InvPosting.objects.create(
        posting_type="RECEIVE_TASK",
        warehouse=getattr(task, "warehouse", None),
        task=task,
        fp=fp,
        by_user=by_user,
        remark="任务审核通过后统一过账",
    )

    # 汇总每行（可直接按行入账；若你要按商品/批次聚合，也可在这里做 group）
    lines = (WmsTaskLine.objects
             .select_related("product","to_location","from_location")
             .filter(task=task))

    for ln in lines:
        extra = getattr(ln, "receivelineextra", None)  # 如果 related_name 不同，改名
        if not extra:
            continue
        def add(status_code, qty):
            q = Decimal(qty or 0)
            if q > 0:
                InvPostingLine.objects.create(
                    posting=posting,
                    product=ln.product,
                    location=ln.to_location or ln.from_location,
                    lot_no=getattr(extra, "lot_no", None),
                    exp_date=getattr(extra, "exp_date", None),
                    status_code=status_code,
                    qty=q,
                    uom="EA",
                )
        add("GOOD", extra.qty_done)
        add("DMG",  extra.qty_damage)
        add("REJ",  extra.qty_reject)

    # 如果需要，同时更新库存现存量/移动台账，在这里调用你的库存结转逻辑
