## 服务：生成并保存快照  # **文件：`allapp/reports/services.py`**

import hashlib, json
from django.template.loader import render_to_string
from .dispatch_note_builder import build_dispatch_note
from .models import ReportSnapshot

def snapshot_dispatch_note(task, by_user, save_html=True, finalize=False):
    note = build_dispatch_note(task.id)
    payload = {
        "header": note.header.__dict__,
        "items": [it.__dict__ for it in note.items],
        "total_amount": str(note.total_amount),
        "total_amount_upper": note.total_amount_upper,
    }
    html = render_to_string("reports/dispatch_note.html", {"note": note}) if save_html else ""
    fp_src = json.dumps(payload, ensure_ascii=False, sort_keys=True) + "|" + (note.header.title or "") + "|v1"
    fp = hashlib.sha256(fp_src.encode("utf-8")).hexdigest()

    snap, _ = ReportSnapshot.objects.get_or_create(
        fp=fp,
        defaults=dict(
            owner=task.owner, warehouse=task.warehouse,
            src_model="WmsTask", src_id=task.id,
            doc_type="DISPATCH_NOTE", doc_no=note.header.note_no,
            template="dispatch_note", tpl_ver="v1",
            payload=payload, html=html,
            amount_total=note.total_amount,
            amount_upper=note.total_amount_upper,
            is_final=finalize, created_by=by_user,
        ),
    )
    return snap
