import csv
from pathlib import Path

import MySQLdb
import MySQLdb.cursors

BASE = Path("var/data-accuracy")
REPORT_PATH = BASE / "foundation-data-accuracy-validation-2026-06-25.md"
ISSUES_PATH = BASE / "foundation-data-accuracy-issues-2026-06-25.csv"


CHECKS = [
    (
        "inventory_detail_negative_quantities",
        """
        SELECT id, owner_id, warehouse_id, product_id, location_id,
               onhand_qty, allocated_qty, locked_qty, damaged_qty, available_qty
        FROM inventory_inventorydetail
        WHERE is_active=1
          AND (onhand_qty<0 OR allocated_qty<0 OR locked_qty<0
               OR damaged_qty<0 OR available_qty<0)
        """,
    ),
    (
        "inventory_summary_negative_quantities",
        """
        SELECT id, owner_id, product_id,
               onhand_qty, allocated_qty, locked_qty, damaged_qty, available_qty
        FROM inventory_inventorysummary
        WHERE is_active=1
          AND (onhand_qty<0 OR allocated_qty<0 OR locked_qty<0
               OR damaged_qty<0 OR available_qty<0)
        """,
    ),
    (
        "inventory_transaction_zero_qty_delta",
        """
        SELECT id, owner_id, warehouse_id, product_id, location_id, qty_delta, src_model, src_id
        FROM inventory_inventorytransaction
        WHERE is_active=1 AND posted_at IS NOT NULL AND ROUND(qty_delta,4)=0
        """,
    ),
    (
        "inventory_detail_location_warehouse_mismatch",
        """
        SELECT d.id, d.owner_id, d.warehouse_id, l.warehouse_id AS location_warehouse_id,
               d.product_id, d.location_id
        FROM inventory_inventorydetail d
        JOIN locations_location l ON l.id=d.location_id
        WHERE d.is_active=1 AND d.warehouse_id<>l.warehouse_id
        """,
    ),
    (
        "inventory_transaction_location_warehouse_mismatch",
        """
        SELECT t.id, t.owner_id, t.warehouse_id, l.warehouse_id AS location_warehouse_id,
               t.product_id, t.location_id
        FROM inventory_inventorytransaction t
        JOIN locations_location l ON l.id=t.location_id
        WHERE t.is_active=1 AND t.posted_at IS NOT NULL AND t.warehouse_id<>l.warehouse_id
        """,
    ),
    (
        "inventory_detail_subwarehouse_warehouse_mismatch",
        """
        SELECT d.id, d.owner_id, d.warehouse_id, s.warehouse_id AS subwarehouse_warehouse_id,
               d.product_id, d.subwarehouse_id
        FROM inventory_inventorydetail d
        JOIN locations_subwarehouse s ON s.id=d.subwarehouse_id
        WHERE d.is_active=1 AND d.subwarehouse_id IS NOT NULL
          AND d.warehouse_id<>s.warehouse_id
        """,
    ),
    (
        "inventory_transaction_subwarehouse_warehouse_mismatch",
        """
        SELECT t.id, t.owner_id, t.warehouse_id, s.warehouse_id AS subwarehouse_warehouse_id,
               t.product_id, t.subwarehouse_id
        FROM inventory_inventorytransaction t
        JOIN locations_subwarehouse s ON s.id=t.subwarehouse_id
        WHERE t.is_active=1 AND t.posted_at IS NOT NULL AND t.subwarehouse_id IS NOT NULL
          AND t.warehouse_id<>s.warehouse_id
        """,
    ),
    (
        "location_subwarehouse_warehouse_mismatch",
        """
        SELECT l.id, l.code, l.warehouse_id, s.warehouse_id AS subwarehouse_warehouse_id,
               l.subwarehouse_id
        FROM locations_location l
        JOIN locations_subwarehouse s ON s.id=l.subwarehouse_id
        WHERE l.subwarehouse_id IS NOT NULL AND l.warehouse_id<>s.warehouse_id
        """,
    ),
    (
        "inventory_detail_product_owner_mismatch",
        """
        SELECT d.id, d.owner_id, p.owner_id AS product_owner_id, d.product_id, p.code
        FROM inventory_inventorydetail d
        JOIN products_product p ON p.id=d.product_id
        WHERE d.is_active=1 AND d.owner_id<>p.owner_id
        """,
    ),
    (
        "inventory_transaction_product_owner_mismatch",
        """
        SELECT t.id, t.owner_id, p.owner_id AS product_owner_id, t.product_id, p.code
        FROM inventory_inventorytransaction t
        JOIN products_product p ON p.id=t.product_id
        WHERE t.is_active=1 AND t.posted_at IS NOT NULL AND t.owner_id<>p.owner_id
        """,
    ),
    (
        "inventory_summary_product_owner_mismatch",
        """
        SELECT s.id, s.owner_id, p.owner_id AS product_owner_id, s.product_id, p.code
        FROM inventory_inventorysummary s
        JOIN products_product p ON p.id=s.product_id
        WHERE s.is_active=1 AND s.owner_id<>p.owner_id
        """,
    ),
    (
        "task_line_from_location_warehouse_mismatch",
        """
        SELECT tl.id, t.task_no, t.warehouse_id, l.warehouse_id AS location_warehouse_id,
               tl.from_location_id
        FROM tasking_wmstaskline tl
        JOIN tasking_wmstask t ON t.id=tl.task_id
        JOIN locations_location l ON l.id=tl.from_location_id
        WHERE tl.is_active=1 AND tl.from_location_id IS NOT NULL
          AND t.warehouse_id<>l.warehouse_id
        """,
    ),
    (
        "task_line_to_location_warehouse_mismatch",
        """
        SELECT tl.id, t.task_no, t.warehouse_id, l.warehouse_id AS location_warehouse_id,
               tl.to_location_id
        FROM tasking_wmstaskline tl
        JOIN tasking_wmstask t ON t.id=tl.task_id
        JOIN locations_location l ON l.id=tl.to_location_id
        WHERE tl.is_active=1 AND tl.to_location_id IS NOT NULL
          AND t.warehouse_id<>l.warehouse_id
        """,
    ),
    (
        "task_owner_warehouse_null_guard",
        """
        SELECT id, task_no, owner_id, warehouse_id, status
        FROM tasking_wmstask
        WHERE is_active=1 AND (owner_id IS NULL OR warehouse_id IS NULL)
        """,
    ),
    (
        "task_line_negative_quantities",
        """
        SELECT id, task_id, qty_plan, qty_done, status
        FROM tasking_wmstaskline
        WHERE is_active=1 AND (qty_plan<0 OR qty_done<0)
        """,
    ),
    (
        "task_line_done_gt_plan",
        """
        SELECT id, task_id, qty_plan, qty_done, status
        FROM tasking_wmstaskline
        WHERE is_active=1 AND qty_done>qty_plan
        """,
    ),
    (
        "outbound_line_nonpositive_qty",
        """
        SELECT id, order_id, product_id, base_qty
        FROM outbound_outboundorderline
        WHERE is_active=1 AND base_qty<=0
        """,
    ),
    (
        "outbound_line_product_owner_mismatch",
        """
        SELECT l.id, l.order_id, o.owner_id AS order_owner_id, p.owner_id AS product_owner_id,
               l.product_id, p.code
        FROM outbound_outboundorderline l
        JOIN outbound_outboundorder o ON o.id=l.order_id
        JOIN products_product p ON p.id=l.product_id
        WHERE l.is_active=1 AND o.owner_id<>p.owner_id
        """,
    ),
    (
        "posted_task_line_reserved_status",
        """
        SELECT tl.id, t.task_no, t.status AS task_status, t.review_status, t.posting_status,
               tl.status AS line_status, tl.qty_plan, tl.qty_done, tl.product_id, p.code
        FROM tasking_wmstaskline tl
        JOIN tasking_wmstask t ON t.id=tl.task_id
        LEFT JOIN products_product p ON p.id=tl.product_id
        WHERE tl.is_active=1
          AND t.status='COMPLETED'
          AND t.posting_status='POSTED'
          AND tl.status='RESERVED'
        """,
    ),
]


STATUS_QUERIES = [
    (
        "outbound_submit_status",
        "SELECT submit_status AS status, COUNT(*) count FROM outbound_outboundorder "
        "WHERE is_active=1 GROUP BY submit_status ORDER BY submit_status",
    ),
    (
        "outbound_approval_status",
        "SELECT approval_status AS status, COUNT(*) count FROM outbound_outboundorder "
        "WHERE is_active=1 GROUP BY approval_status ORDER BY approval_status",
    ),
    (
        "outbound_closed",
        "SELECT CAST(is_closed AS CHAR) AS status, COUNT(*) count "
        "FROM outbound_outboundorder WHERE is_active=1 GROUP BY is_closed ORDER BY is_closed",
    ),
    (
        "task_status",
        "SELECT status, COUNT(*) count FROM tasking_wmstask "
        "WHERE is_active=1 GROUP BY status ORDER BY status",
    ),
    (
        "task_review_status",
        "SELECT review_status AS status, COUNT(*) count FROM tasking_wmstask "
        "WHERE is_active=1 GROUP BY review_status ORDER BY review_status",
    ),
    (
        "task_posting_status",
        "SELECT posting_status AS status, COUNT(*) count FROM tasking_wmstask "
        "WHERE is_active=1 GROUP BY posting_status ORDER BY posting_status",
    ),
    (
        "task_line_status",
        "SELECT status, COUNT(*) count FROM tasking_wmstaskline "
        "WHERE is_active=1 GROUP BY status ORDER BY status",
    ),
]


def connect():
    return MySQLdb.connect(
        user="wmsuser",
        unix_socket="/run/mysqld/mysqld.sock",
        db="wms_db",
        charset="utf8mb4",
        cursorclass=MySQLdb.cursors.DictCursor,
    )


def main():
    issue_rows = []
    check_counts = []
    statuses = []
    with connect() as conn:
        cur = conn.cursor()
        for name, sql in CHECKS:
            cur.execute(sql)
            rows = cur.fetchall()
            check_counts.append((name, len(rows)))
            for row in rows:
                payload = "; ".join(f"{key}={value}" for key, value in row.items())
                issue_rows.append({"check_name": name, "detail": payload})
        for name, sql in STATUS_QUERIES:
            cur.execute(sql)
            for row in cur.fetchall():
                statuses.append(
                    {
                        "kind": name,
                        "status": row["status"],
                        "count": row["count"],
                    }
                )

    with ISSUES_PATH.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=["check_name", "detail"])
        writer.writeheader()
        writer.writerows(issue_rows)

    report = [
        "# Foundation Data Accuracy Validation 2026-06-25",
        "",
        "## Check Counts",
        "",
        "| Check | Issues |",
        "| --- | ---: |",
    ]
    for name, count in check_counts:
        report.append(f"| {name} | {count} |")
    report.extend(["", "## Status Distribution", "", "| Kind | Status | Count |", "| --- | --- | ---: |"])
    for row in statuses:
        report.append(f"| {row['kind']} | {row['status']} | {row['count']} |")
    report.extend(
        [
            "",
            "## Issue Detail",
            "",
            f"Issue detail CSV: `{ISSUES_PATH.name}`",
            "",
        ]
    )
    if issue_rows:
        report.append(f"Non-zero issue rows: {len(issue_rows)}")
    else:
        report.append("No foundation data consistency issues found.")
    REPORT_PATH.write_text("\n".join(report) + "\n", encoding="utf-8")

    print("checks", len(check_counts))
    print("issue_rows", len(issue_rows))
    for name, count in check_counts:
        if count:
            print(name, count)
    print("wrote", REPORT_PATH)
    print("wrote", ISSUES_PATH)


if __name__ == "__main__":
    main()
