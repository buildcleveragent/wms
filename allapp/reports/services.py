"""Services for immutable report snapshots."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict

from django.template.loader import render_to_string

from .dispatch_note_builder import build_dispatch_note
from .models import ReportSnapshot


def _dispatch_payload(note):
    """Return a stable, JSON-native v2 payload."""

    header = asdict(note.header)
    items = []
    for item in note.items:
        value = asdict(item)
        for field in ("qty", "price", "amount", "piece_qty"):
            if value[field] is not None:
                value[field] = str(value[field])
        items.append(value)
    return {
        "snapshot_version": "v2",
        "is_preview": note.is_preview,
        "header": header,
        "items": items,
        "total_amount": str(note.total_amount),
        "total_amount_upper": note.total_amount_upper,
    }


def snapshot_dispatch_note(task, by_user, save_html=True, finalize=False):
    """Create a v2 snapshot without mutating historical v1 documents."""

    note = build_dispatch_note(task.id)
    if finalize and note.is_preview:
        raise ValueError("执行中的配送任务只能生成预览，不能定稿。")

    payload = _dispatch_payload(note)
    html = render_to_string("reports/dispatch_note.html", {"note": note}) if save_html else ""
    fingerprint_source = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    fingerprint = hashlib.sha256(f"{fingerprint_source}|dispatch_note|v2".encode()).hexdigest()

    snapshot, created = ReportSnapshot.objects.get_or_create(
        fp=fingerprint,
        defaults={
            "owner": task.owner,
            "warehouse": task.warehouse,
            "src_model": "WmsTask",
            "src_id": task.id,
            "doc_type": "DISPATCH_NOTE",
            "doc_no": note.header.note_no,
            "template": "dispatch_note",
            "tpl_ver": "v2",
            "payload": payload,
            "html": html,
            "amount_total": note.total_amount,
            "amount_upper": note.total_amount_upper,
            "is_final": finalize,
            "created_by": by_user,
        },
    )
    if not created and finalize and not snapshot.is_final:
        snapshot.is_final = True
        snapshot.save(update_fields=["is_final"])
    return snapshot
