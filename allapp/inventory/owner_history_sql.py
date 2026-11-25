"""MySQL 8 read model for owner history; quantities always come from the ledger.

CTEs resolve provenance once, then number complete events *before* applying date,
product, batch or pagination filters. No document status can erase a posted fact.
Only identifiers obtained from Django models are interpolated into SQL.
"""

from django.apps import apps
from django.db import connection


def table(label):
    return connection.ops.quote_name(apps.get_model(label)._meta.db_table)


def facts_sql():
    """Return an owner-parameterized, reusable CTE defining ``facts``."""
    tables = {
        key: table(label)
        for key, label in {
            "tx": "inventory.InventoryTransaction",
            "task": "tasking.WmsTask",
            "inbound": "inbound.InboundOrder",
            "receipt": "inbound.InboundReceipt",
            "receipt_line": "inbound.InboundReceiptLine",
            "outbound": "outbound.OutboundOrder",
            "outbound_line": "outbound.OutboundOrderLine",
            "sale": "pos.PosSale",
            "sale_line": "pos.PosSaleLine",
            "pos_return": "pos.PosReturn",
            "pos_return_line": "pos.PosReturnLine",
            "customer_return": "inbound.CustomerReturn",
            "customer_return_line": "inbound.CustomerReturnLine",
            "customer_receipt": "inbound.CustomerReturnReceiptLine",
            "customer_receipt_header": "inbound.CustomerReturnReceipt",
            "return": "outbound.ReturnOrder",
            "transfer": "outbound.AssistedOwnershipTransfer",
            "product": "products.Product",
            "warehouse": "locations.Warehouse",
            "customer": "baseinfo.Customer",
            "supplier": "baseinfo.Supplier",
        }.items()
    }
    return """WITH RECURSIVE
ledger AS (
    SELECT x.*, LOWER(x.src_model) AS source_kind
    FROM {tx} x
    WHERE x.owner_id = %s AND x.id <= %s AND x.is_deleted = 0 AND x.is_active = 1
),
task_path AS (
    SELECT t.id AS execution_id, t.id, t.owner_id, t.warehouse_id,
           t.source_app, t.source_model, t.source_pk, 0 AS depth
    FROM {task} t
    WHERE EXISTS (SELECT 1 FROM ledger x WHERE x.src_id = t.id
        AND x.source_kind IN ('wmstask', 'tasking.wmstask')
        AND x.owner_id = t.owner_id AND x.warehouse_id = t.warehouse_id)
    UNION ALL
    SELECT p.execution_id, t.id, t.owner_id, t.warehouse_id,
           t.source_app, t.source_model, t.source_pk, p.depth + 1
    FROM task_path p JOIN {task} t
      ON p.source_pk REGEXP '^[1-9][0-9]*$' AND t.id = CAST(p.source_pk AS UNSIGNED)
      AND t.owner_id = p.owner_id
      AND t.warehouse_id = p.warehouse_id
    WHERE LOWER(p.source_model) IN ('wmstask', 'tasking.wmstask')
      AND LOWER(COALESCE(p.source_app, '')) IN ('', 'tasking') AND p.depth < 8
),
roots AS (
    SELECT * FROM task_path
    WHERE LOWER(COALESCE(source_model, '')) NOT IN ('wmstask', 'tasking.wmstask')
),
resolved AS (
    SELECT x.*, t.id AS task_id, t.task_type, t.task_no, t.posting_note AS task_posting_note,
      r.source_app AS root_app, r.source_model AS root_model,
      i.id AS inbound_id, i.order_no AS inbound_no,
      COALESCE(i.inbound_type, ir.inbound_type) AS inbound_type,
      ir.id AS receipt_id, ir.receipt_no,
      o.id AS outbound_id, o.order_no AS outbound_no, o.outbound_type,
      s.id AS sale_id, s.sale_no,
      pr.id AS pos_return_id, pr.return_no AS pos_return_no,
      cr.id AS customer_return_id, cr.number AS customer_return_no,
      ro.id AS return_id, ro.return_no, ro.recipient_name,
      tr.id AS transfer_id, tr.transfer_no,
      COALESCE(sup.name, cust.name, ro.recipient_name, '') AS counterparty,
      p.name AS product_name, p.code AS product_code, p.sku AS product_sku,
      p.spec AS product_spec, w.name AS warehouse_name
    FROM ledger x
    JOIN {product} p ON p.id = x.product_id AND p.owner_id = x.owner_id
    JOIN {warehouse} w ON w.id = x.warehouse_id
    LEFT JOIN {task} t ON x.source_kind IN ('wmstask', 'tasking.wmstask')
      AND t.id = x.src_id AND t.owner_id = x.owner_id AND t.warehouse_id = x.warehouse_id
    LEFT JOIN roots r ON r.execution_id = t.id
    LEFT JOIN {receipt_line} irl
      ON x.source_kind IN ('inboundreceiptline', 'inbound.inboundreceiptline')
      AND irl.id = x.src_id AND irl.product_id = x.product_id
    LEFT JOIN {receipt} ir ON (ir.task_id = t.id
        OR (x.source_kind IN ('inboundreceipt', 'inbound.inboundreceipt') AND ir.id = x.src_id)
        OR ir.id = irl.receipt_id)
      AND ir.owner_id = x.owner_id AND ir.warehouse_id = x.warehouse_id
      AND (ir.order_id IS NULL OR EXISTS (SELECT 1 FROM {inbound} receipt_order
        WHERE receipt_order.id = ir.order_id AND receipt_order.owner_id = x.owner_id
        AND receipt_order.warehouse_id = x.warehouse_id))
    LEFT JOIN {sale_line} sl ON x.source_kind IN ('possaleline', 'pos.possaleline')
      AND sl.id = x.src_id AND sl.owner_id = x.owner_id AND sl.product_id = x.product_id
    LEFT JOIN {sale} s ON s.id = sl.sale_id AND s.warehouse_id = x.warehouse_id
    LEFT JOIN {outbound_line} ol
      ON ol.id = sl.outbound_order_line_id AND ol.product_id = x.product_id
    LEFT JOIN {inbound} i ON i.id = COALESCE(ir.order_id,
        CASE WHEN LOWER(r.source_model) IN ('inboundorder', 'inbound.inboundorder')
            AND LOWER(COALESCE(r.source_app, '')) IN ('', 'inbound')
            AND r.source_pk REGEXP '^[1-9][0-9]*$' THEN CAST(r.source_pk AS UNSIGNED) END,
        CASE WHEN x.source_kind IN ('inboundorder', 'inbound.inboundorder') THEN x.src_id END)
      AND i.owner_id = x.owner_id AND i.warehouse_id = x.warehouse_id
    LEFT JOIN {outbound} o ON o.id = COALESCE(
        CASE WHEN LOWER(r.source_model) IN ('outboundorder', 'outbound.outboundorder')
            AND LOWER(COALESCE(r.source_app, '')) IN ('', 'outbound', 'pos')
            AND r.source_pk REGEXP '^[1-9][0-9]*$' THEN CAST(r.source_pk AS UNSIGNED) END,
        CASE WHEN x.source_kind IN ('outboundorder', 'outbound.outboundorder') THEN x.src_id END,
        CASE WHEN s.id IS NOT NULL THEN ol.order_id END)
      AND o.owner_id = x.owner_id AND o.warehouse_id = x.warehouse_id
    LEFT JOIN {pos_return_line} prl ON x.source_kind IN ('posreturnline', 'pos.posreturnline')
      AND prl.id = x.src_id AND prl.owner_id = x.owner_id AND prl.product_id = x.product_id
    LEFT JOIN {pos_return} pr ON pr.id = prl.return_order_id AND pr.warehouse_id = x.warehouse_id
    LEFT JOIN {customer_receipt} crr
      ON x.source_kind IN ('customerreturnreceiptline', 'inbound.customerreturnreceiptline')
      AND crr.id = x.src_id AND crr.line_id = x.src_line_id
    LEFT JOIN {customer_receipt_header} crh ON crh.id = crr.receipt_id
    LEFT JOIN {customer_return_line} crl ON crl.id = crr.line_id AND crl.product_id = x.product_id
    LEFT JOIN {customer_return} cr ON cr.id = crl.order_id AND cr.id = crh.order_id
      AND cr.owner_id = x.owner_id AND cr.warehouse_id = x.warehouse_id
    LEFT JOIN {return} ro ON x.source_kind IN ('returnorder', 'outbound.returnorder')
      AND ro.id = x.src_id AND ro.owner_id = x.owner_id AND ro.warehouse_id = x.warehouse_id
    LEFT JOIN {transfer} tr ON x.tx_type IN ('OWNER_XFER_IN', 'OWNER_XFER_OUT')
      AND tr.transfer_no COLLATE utf8mb4_unicode_ci = x.src_no COLLATE utf8mb4_unicode_ci
      AND tr.warehouse_id = x.warehouse_id
      AND ((x.tx_type = 'OWNER_XFER_IN' AND tr.target_owner_id = x.owner_id)
        OR (x.tx_type = 'OWNER_XFER_OUT' AND tr.source_owner_id = x.owner_id))
    LEFT JOIN {supplier} sup ON sup.id = COALESCE(i.supplier_id, ir.supplier_id)
      AND sup.owner_id = x.owner_id
    LEFT JOIN {customer} cust ON cust.id = COALESCE(o.customer_id, s.selected_customer_id)
      AND cust.owner_id = x.owner_id
),
classified AS (
    SELECT resolved.*,
      CASE
        WHEN tx_type IN ('MOVE_IN', 'MOVE_OUT')
          OR (pair_id IS NOT NULL AND tx_type IN ('RECEIVE', 'ISSUE'))
          OR (task_type IN ('PUTAWAY', 'RELOC', 'REPLEN') AND tx_type IN ('RECEIVE', 'ISSUE'))
          THEN 'movement'
        WHEN tx_type = 'OWNER_XFER_IN' AND transfer_id IS NOT NULL THEN 'transfer_in'
        WHEN tx_type = 'OWNER_XFER_OUT' AND transfer_id IS NOT NULL THEN 'transfer_out'
        WHEN tx_type = 'ADJ_GAIN' THEN 'adjust_gain'
        WHEN tx_type = 'ADJ_LOSS' THEN 'adjust_loss'
        WHEN tx_type = 'RECEIVE' AND (customer_return_id IS NOT NULL OR pos_return_id IS NOT NULL
          OR inbound_type = 'CUST_RETURN') THEN 'customer_return'
        WHEN tx_type = 'RECEIVE' AND (sale_id IS NOT NULL OR return_id IS NOT NULL) THEN 'reversal'
        WHEN tx_type = 'ISSUE' AND (return_id IS NOT NULL OR outbound_type = 'SUPPLIER_RETURN')
          THEN 'supplier_return'
        WHEN tx_type = 'RECEIVE' AND (inbound_id IS NOT NULL OR receipt_id IS NOT NULL
          OR (task_type = 'RECEIVE' AND (
            (LOWER(root_app) = 'wmspda' AND LOWER(root_model) = 'receivewithoutorder')
            OR task_posting_note LIKE '%%PDA无ASN收货%%'))) THEN 'inbound'
        WHEN tx_type = 'ISSUE' AND (outbound_id IS NOT NULL OR sale_id IS NOT NULL) THEN 'outbound'
        ELSE 'unknown'
      END AS category,
      CASE
        WHEN transfer_id IS NOT NULL THEN CONCAT('transfer:', transfer_id)
        WHEN customer_return_id IS NOT NULL THEN CONCAT('customer_return:', customer_return_id)
        WHEN pos_return_id IS NOT NULL THEN CONCAT('pos_return:', pos_return_id)
        WHEN return_id IS NOT NULL THEN CONCAT('return:', return_id)
        WHEN inbound_id IS NOT NULL THEN CONCAT('inbound:', inbound_id)
        WHEN outbound_id IS NOT NULL THEN CONCAT('outbound:', outbound_id)
        WHEN sale_id IS NOT NULL THEN CONCAT('pos:', sale_id)
        WHEN receipt_id IS NOT NULL THEN CONCAT('receipt:', receipt_id)
        WHEN task_id IS NOT NULL THEN CONCAT('task:', task_id)
        ELSE CONCAT('unknown:', id)
      END AS document_key,
      CASE WHEN transfer_id IS NOT NULL THEN transfer_no
        ELSE COALESCE(customer_return_no, pos_return_no, return_no, inbound_no, outbound_no,
                      sale_no, receipt_no, task_no, '') END AS document_no,
      CASE WHEN transfer_id IS NOT NULL THEN CONCAT('transfer:', transfer_id)
        WHEN task_id IS NOT NULL THEN CONCAT('task:', task_id)
        WHEN receipt_id IS NOT NULL THEN CONCAT('receipt:', receipt_id)
        WHEN sale_id IS NOT NULL THEN CONCAT('pos:', sale_id)
        WHEN pos_return_id IS NOT NULL THEN CONCAT('pos_return:', pos_return_id)
        WHEN customer_return_id IS NOT NULL THEN CONCAT('customer_return:', customer_return_id)
        WHEN return_id IS NOT NULL THEN CONCAT('return:', return_id)
        WHEN inbound_id IS NOT NULL THEN CONCAT('inbound:', inbound_id)
        WHEN outbound_id IS NOT NULL THEN CONCAT('outbound:', outbound_id)
        ELSE CONCAT('unknown:', id) END AS execution_key
    FROM resolved
),
facts AS (
    SELECT classified.*,
      CASE WHEN posted_at IS NULL OR category = 'unknown' THEN CONCAT('unverified:', id)
      ELSE CONCAT(execution_key, ':', category, ':',
        COALESCE(NULLIF(posting_batch, ''),
          CASE WHEN task_id IS NOT NULL OR receipt_id IS NOT NULL THEN 'posted'
               ELSE CONCAT('unresolved-', id) END)) END AS event_key,
      COALESCE((category NOT IN ('unknown', 'movement') AND posted_at IS NOT NULL
       AND execution_key NOT LIKE 'unknown:%%'
       AND (task_id IS NOT NULL OR receipt_id IS NOT NULL
         OR NULLIF(posting_batch, '') IS NOT NULL)), 0) AS countable
    FROM classified
)
""".format(**tables)


NUMBERING_SQL = """
, event_base AS (
    SELECT event_key, MIN(id) AS event_id, MIN(posted_at) AS event_at,
      MIN(document_key) AS document_key, MIN(category) AS category,
      MIN(countable) AS countable
    FROM facts GROUP BY event_key
), document_numbers AS (
    SELECT event_base.*, CASE WHEN countable THEN
      SUM(countable) OVER (PARTITION BY document_key, category
        ORDER BY event_at, event_id ROWS UNBOUNDED PRECEDING) END AS document_sequence
    FROM event_base
), product_events AS (
    SELECT f.event_key, f.product_id, MIN(f.category) AS category,
      MIN(f.posted_at) AS event_at, MIN(f.id) AS first_id, MIN(f.countable) AS countable
    FROM facts f GROUP BY f.event_key, f.product_id
), product_numbers AS (
    SELECT product_events.*, CASE WHEN countable THEN
      SUM(countable) OVER (PARTITION BY product_id, category
        ORDER BY event_at, first_id ROWS UNBOUNDED PRECEDING) END AS product_sequence
    FROM product_events
), numbered AS (
    SELECT f.*, d.event_id, d.event_at, d.document_sequence, p.product_sequence
    FROM facts f JOIN document_numbers d ON d.event_key = f.event_key
    JOIN product_numbers p ON p.event_key = f.event_key AND p.product_id = f.product_id
)
"""
