"""Read-only business facts: approved physical reviews and posted receipts.

The ledger query remains unchanged for older clients. A reviewed PICK is represented
once, even before posting or after a posting retry. Money is allocated before any
report filters so calendar totals and exports add up to the same cents.
"""

from .owner_history_sql import facts_sql, table


def half_even(expression):
    """MySQL ROUND uses half-up; reproduce Decimal ROUND_HALF_EVEN for positive money."""
    return (
        f"(CASE WHEN ({expression}) * 100 - FLOOR(({expression}) * 100) = 0.5 "
        f"THEN (FLOOR(({expression}) * 100) + MOD(FLOOR(({expression}) * 100), 2)) / 100 "
        f"ELSE ROUND(({expression}), 2) END)"
    )


def statistics_sql(*, personal=False):
    tables = {
        key: table(label)
        for key, label in {
            "task": "tasking.WmsTask",
            "line": "tasking.WmsTaskLine",
            "scan": "tasking.TaskScanLog",
            "location": "locations.Location",
            "product": "products.Product",
            "unit": "products.ProductUom",
            "warehouse": "locations.Warehouse",
            "customer": "baseinfo.Customer",
            "outbound": "outbound.OutboundOrder",
            "outbound_line": "outbound.OutboundOrderLine",
            "inbound_line": "inbound.InboundOrderLine",
            "receipt": "inbound.InboundReceipt",
            "receipt_line": "inbound.InboundReceiptLine",
            "sale_line": "pos.PosSaleLine",
            "inbound": "inbound.InboundOrder",
            "customer_return": "inbound.CustomerReturn",
            "sale": "pos.PosSale",
        }.items()
    }
    personal_filter = ""
    if personal:
        # Warehouse scanners do not determine the salesperson. Follow original
        # document authorship (including returns), or the cashier for legacy POS.
        # Scope the unified facts before aggregating, paginating or exporting.
        personal_filter = """
        WHERE EXISTS (SELECT 1 FROM {inbound} own_in
          WHERE own_in.id = b.inbound_id AND own_in.created_by_id = %s)
        OR EXISTS (SELECT 1 FROM {outbound} own_out
          WHERE own_out.id = b.outbound_id AND own_out.created_by_id = %s)
        OR EXISTS (SELECT 1 FROM {customer_return} own_return
          JOIN {outbound} original ON original.id = own_return.original_order_id
            AND original.owner_id = own_return.owner_id
          WHERE own_return.id = b.customer_return_id AND original.created_by_id = %s)
        OR EXISTS (SELECT 1 FROM {sale} own_sale
          WHERE b.document_key = CONCAT('pos:', own_sale.id) AND own_sale.cashier_id = %s)
        """.format(**tables)
    return (
        facts_sql()
        + """
, review_links AS (
    SELECT p.id AS pick_id, MIN(r.id) AS review_id, COUNT(*) AS reviews
    FROM {task} p JOIN {task} r
      ON LOWER(r.source_model) IN ('wmstask', 'tasking.wmstask')
      AND LOWER(COALESCE(r.source_app, '')) IN ('', 'tasking')
      AND r.source_pk REGEXP '^[1-9][0-9]*$' AND CAST(r.source_pk AS UNSIGNED) = p.id
      AND r.owner_id = p.owner_id AND r.warehouse_id = p.warehouse_id
      AND r.task_type = 'REVIEW' AND r.status <> 'CANCELLED'
    WHERE p.owner_id = %s AND p.task_type = 'PICK'
    GROUP BY p.id
), reviewed_lines AS (
    SELECT r.id AS review_id, p.id AS pick_id, rl.id AS review_line_id,
      pl.id AS pick_line_id, rl.qty_done, r.approved_at, p.posted_at AS ledger_posted_at,
      p.posting_status, p.owner_id, p.warehouse_id, w.name AS warehouse_name,
      rl.product_id, prod.name AS product_name, prod.code AS product_code,
      prod.sku AS product_sku, prod.spec AS product_spec,
      COALESCE(u.code, '') AS base_unit,
      CASE WHEN o.id IS NOT NULL THEN ol.id END AS order_line_id,
      o.id AS outbound_id, COALESCE(o.order_no, p.task_no) AS document_no,
      COALESCE(c.name, '') AS counterparty,
      CASE WHEN o.id IS NULL THEN 'unknown'
           WHEN o.outbound_type = 'SUPPLIER_RETURN' THEN 'supplier_return'
           ELSE 'outbound' END AS category,
      CASE WHEN o.id IS NOT NULL AND u.id IS NOT NULL AND ol.is_priced = 1
        AND (ol.base_price > 0 OR o.pricing_status = 'CONFIRMED')
        THEN ol.base_price END AS unit_price
    FROM review_links link JOIN {task} p ON p.id = link.pick_id
    JOIN {task} r ON r.id = link.review_id
    JOIN {line} rl ON rl.task_id = r.id AND rl.status = 'COMPLETED'
    JOIN {line} pl ON LOWER(rl.src_model) IN ('wmstaskline', 'tasking.wmstaskline')
      AND pl.id = rl.src_id AND pl.task_id = p.id AND pl.product_id = rl.product_id
    JOIN {product} prod ON prod.id = rl.product_id AND prod.owner_id = p.owner_id
    JOIN {warehouse} w ON w.id = p.warehouse_id
    LEFT JOIN {outbound_line} ol
      ON LOWER(pl.src_model) IN ('outboundorderline', 'outbound.outboundorderline')
      AND ol.id = pl.src_id AND ol.product_id = pl.product_id
    LEFT JOIN {outbound} o ON o.id = ol.order_id
      AND o.owner_id = p.owner_id AND o.warehouse_id = p.warehouse_id
      AND LOWER(p.source_model) IN ('outboundorder', 'outbound.outboundorder')
      AND LOWER(COALESCE(p.source_app, '')) IN ('', 'outbound', 'pos')
      AND p.source_pk REGEXP '^[1-9][0-9]*$' AND CAST(p.source_pk AS UNSIGNED) = o.id
    LEFT JOIN {unit} u ON u.id = ol.base_uom_id AND o.id IS NOT NULL
    LEFT JOIN {customer} c ON c.id = o.customer_id AND c.owner_id = p.owner_id
    WHERE link.reviews = 1 AND p.status = 'COMPLETED'
      AND r.status = 'COMPLETED' AND r.review_status = 'APPROVED'
      AND r.id <= %s AND (r.approved_at <= %s OR r.approved_at IS NULL)
      AND rl.qty_done > 0
), review_scans AS (
    SELECT r.review_line_id, MIN(s.id) AS scan_id,
      COALESCE(s.lot_no, '') AS batch_no, s.mfg_date AS production_date,
      s.exp_date AS expiry_date,
      SUM(CASE WHEN s.method = 'MANUAL' THEN ROUND(s.qty_base_delta, 4)
               ELSE ABS(ROUND(s.qty_base_delta, 4)) END) AS qty
    FROM reviewed_lines r JOIN {scan} s
      ON s.task_id = r.pick_id AND s.task_line_id = r.pick_line_id
      AND s.owner_id = r.owner_id AND s.warehouse_id = r.warehouse_id
      AND (s.product_id IS NULL OR s.product_id = r.product_id)
    JOIN {line} pl ON pl.id = r.pick_line_id
    JOIN {location} loc ON loc.id = COALESCE(s.location_id, pl.from_location_id, pl.to_location_id)
      AND loc.warehouse_id = r.warehouse_id
    WHERE s.status = 'OK' AND s.review_status <> 'REJECTED'
      AND s.qty_base_delta IS NOT NULL
    GROUP BY r.review_line_id, COALESCE(s.lot_no, ''), s.mfg_date, s.exp_date
), scan_totals AS (
    SELECT review_line_id, SUM(qty) AS qty, MIN(qty) AS minimum_qty
    FROM review_scans GROUP BY review_line_id
), review_dimensions AS (
    SELECT s.* FROM review_scans s JOIN reviewed_lines r
      ON r.review_line_id = s.review_line_id
    JOIN scan_totals t ON t.review_line_id = r.review_line_id
      AND t.qty = r.qty_done AND t.minimum_qty >= 0
    WHERE s.qty > 0
    UNION ALL
    SELECT r.review_line_id, 0, '', NULL, NULL, r.qty_done
    FROM reviewed_lines r LEFT JOIN scan_totals t ON t.review_line_id = r.review_line_id
    WHERE t.review_line_id IS NULL OR t.qty <> r.qty_done OR t.minimum_qty < 0
), receipt_prices AS (
    SELECT f.id, MIN(rl.id) AS receipt_line_id,
      CASE WHEN COUNT(*) = 1 THEN MIN(rl.unit_price) END AS price
    FROM facts f JOIN {receipt_line} rl ON rl.product_id = f.product_id
      AND rl.base_uom COLLATE utf8mb4_unicode_ci = f.base_unit COLLATE utf8mb4_unicode_ci AND (
        rl.inventory_transaction_id = f.id
        OR (f.source_kind IN ('inboundreceiptline', 'inbound.inboundreceiptline')
            AND rl.id = f.src_id)
        OR (f.task_id IS NOT NULL AND rl.task_line_id = f.src_line_id
            AND rl.receipt_id = f.receipt_id))
    JOIN {receipt} rh ON rh.id = rl.receipt_id AND rh.owner_id = f.owner_id
      AND rh.warehouse_id = f.warehouse_id AND rh.id = f.receipt_id
    GROUP BY f.id
), ledger_prices AS (
    SELECT f.*,
      (f.task_type IN ('DISPATCH', 'LOAD', 'PACK') OR
        (f.task_type = 'PICK' AND legacy_task.review_status IN
          ('PENDING', 'REJECTED', 'NOT_READY', 'NEED_RECOUNT'))) AS unconfirmed_outbound,
      CASE WHEN f.category = 'inbound' THEN
        CASE WHEN rp.receipt_line_id IS NOT NULL THEN NULLIF(rp.price, 0)
             ELSE NULLIF(il.base_price, 0) END
        WHEN f.category = 'outbound' THEN
          CASE WHEN sl.id IS NOT NULL THEN sl.price
               WHEN ol.is_priced = 1 AND (ol.base_price > 0 OR EXISTS (
                 SELECT 1 FROM {outbound} priced_order WHERE priced_order.id = f.outbound_id
                   AND priced_order.pricing_status = 'CONFIRMED'))
                 THEN ol.base_price END END AS unit_price,
      CASE WHEN rp.receipt_line_id IS NOT NULL THEN CONCAT('receipt-line:', rp.receipt_line_id)
        WHEN il.id IS NOT NULL THEN CONCAT('inbound-line:', il.id)
        WHEN sl.id IS NOT NULL THEN CONCAT('sale-line:', sl.id)
        WHEN ol.id IS NOT NULL THEN CONCAT('outbound-line:', ol.id)
        ELSE CONCAT('unknown:', f.id) END AS price_key
    FROM facts f
    LEFT JOIN {task} legacy_task ON legacy_task.id = f.task_id
    LEFT JOIN receipt_prices rp ON rp.id = f.id
    LEFT JOIN {line} tl ON f.task_id IS NOT NULL AND tl.task_id = f.task_id
      AND tl.id = f.src_line_id AND tl.product_id = f.product_id
    LEFT JOIN {inbound_line} il ON il.order_id = f.inbound_id AND il.product_id = f.product_id
      AND il.base_uom COLLATE utf8mb4_unicode_ci = f.base_unit COLLATE utf8mb4_unicode_ci AND (
        (f.source_kind IN ('inboundorder', 'inbound.inboundorder') AND il.id = f.src_line_id)
        OR (LOWER(tl.src_model) IN ('inboundorderline', 'inbound.inboundorderline')
            AND il.id = tl.src_id))
    LEFT JOIN {sale_line} sl ON f.sale_id IS NOT NULL AND sl.sale_id = f.sale_id
      AND sl.id = f.src_id AND sl.owner_id = f.owner_id AND sl.product_id = f.product_id
    LEFT JOIN {outbound_line} ol ON ol.order_id = f.outbound_id AND ol.product_id = f.product_id
      AND ((f.source_kind IN ('outboundorder', 'outbound.outboundorder') AND ol.id = f.src_line_id)
        OR (LOWER(tl.src_model) IN ('outboundorderline', 'outbound.outboundorderline')
            AND ol.id = tl.src_id))
      AND EXISTS (SELECT 1 FROM {unit} ou WHERE ou.id = ol.base_uom_id
        AND ou.code COLLATE utf8mb4_unicode_ci = f.base_unit COLLATE utf8mb4_unicode_ci)
), all_business_rows AS (
    SELECT CONCAT('ledger-', f.id) AS id,
      CONCAT('ledger-', MIN(f.id) OVER (PARTITION BY f.event_key)) AS event_id,
      CASE WHEN f.category = 'outbound' AND f.sale_id IS NULL THEN NULL
           ELSE f.posted_at END AS posted_at,
      f.posted_at AS ledger_posted_at, 'POSTED' AS posting_status,
      f.product_id, f.product_name, f.product_code, f.product_sku, f.product_spec,
      f.warehouse_id, f.warehouse_name, f.base_unit COLLATE utf8mb4_unicode_ci AS base_unit,
      f.batch_no COLLATE utf8mb4_unicode_ci AS batch_no,
      f.production_date, f.expiry_date, f.qty_delta,
      CASE WHEN f.category = 'outbound' AND f.unconfirmed_outbound THEN 'unknown'
           ELSE f.category END AS category,
      f.document_key, f.document_no, f.counterparty, f.receipt_id,
      f.inbound_id, f.outbound_id, f.customer_return_id,
      CASE WHEN f.category = 'outbound' AND f.unconfirmed_outbound THEN NULL
           ELSE f.unit_price END AS unit_price,
      f.price_key, 'CNY' AS currency, 0 AS dimensions_unverified
    FROM ledger_prices f
    WHERE f.category <> 'movement'
      AND NOT (f.category IN ('outbound', 'supplier_return') AND EXISTS (
        SELECT 1 FROM review_links r WHERE r.pick_id = f.task_id))
    UNION ALL
    SELECT CONCAT('review-', r.review_line_id, '-', d.scan_id), CONCAT('review-', r.pick_id),
      r.approved_at, r.ledger_posted_at, r.posting_status,
      r.product_id, r.product_name, r.product_code, r.product_sku, r.product_spec,
      r.warehouse_id, r.warehouse_name, r.base_unit COLLATE utf8mb4_unicode_ci,
      d.batch_no COLLATE utf8mb4_unicode_ci,
      d.production_date, d.expiry_date, -d.qty, r.category,
      CASE WHEN r.outbound_id IS NOT NULL THEN CONCAT('outbound:', r.outbound_id)
           ELSE CONCAT('task:', r.pick_id) END,
      r.document_no, r.counterparty, NULL, NULL, r.outbound_id, NULL,
      r.unit_price, CONCAT('outbound-line:', r.order_line_id), 'CNY', d.scan_id = 0
    FROM reviewed_lines r JOIN review_dimensions d ON d.review_line_id = r.review_line_id
), business_rows AS (
    SELECT b.* FROM all_business_rows b {personal_filter}
), cumulative_prices AS (
    SELECT b.*, SUM(ABS(qty_delta) * unit_price) OVER (
      PARTITION BY category, price_key, unit_price, currency
      ORDER BY posted_at IS NULL, posted_at, id ROWS UNBOUNDED PRECEDING) AS cumulative_amount
    FROM business_rows b
), statistics_facts AS (
    SELECT c.*, {rounded} - {previous} AS amount,
      CASE WHEN u.name REGEXP '[一-龥]' THEN u.name ELSE '单位待核对' END AS base_unit_name
    FROM cumulative_prices c LEFT JOIN {unit} u
      ON u.code COLLATE utf8mb4_unicode_ci = c.base_unit COLLATE utf8mb4_unicode_ci
)
""".format(
            **tables,
            personal_filter=personal_filter,
            rounded=half_even("cumulative_amount"),
            previous=half_even("cumulative_amount - ABS(qty_delta) * unit_price"),
        )
    )
