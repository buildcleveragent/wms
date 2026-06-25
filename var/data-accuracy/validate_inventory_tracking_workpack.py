import csv
from collections import Counter
from pathlib import Path

import MySQLdb
import MySQLdb.cursors

BASE = Path("var/data-accuracy")
REPAIR_PATH = BASE / "inventory-tracking-repair-template.csv"
BUSINESS_PATH = BASE / "inventory-tracking-business-reply.csv"
PRIORITY_PATH = BASE / "inventory-tracking-priority.csv"
PRODUCT_REVIEW_PATH = BASE / "inventory-tracking-product-review-2026-06-25.csv"
REPORT_PATH = BASE / "inventory-tracking-workpack-validation-2026-06-25.md"
MISMATCH_PATH = BASE / "inventory-tracking-workpack-mismatches-2026-06-25.csv"


def read_csv(path):
    with path.open(newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def split_ids(value):
    if not value:
        return []
    out = []
    for part in str(value).replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(int(part))
        except ValueError:
            continue
    return out


LIVE_ISSUE_SQL = r"""
SELECT source, id, owner_id, warehouse_id, product_id, product_code, product_name,
       location_id, batch_no, production_date, expiry_date,
       GROUP_CONCAT(problem ORDER BY problem SEPARATOR ',') problems
FROM (
  SELECT 'detail' source, d.id, d.owner_id, d.warehouse_id, d.product_id,
         p.code product_code, p.name product_name, d.location_id,
         d.batch_no, d.production_date, d.expiry_date, 'missing_batch_no' problem
  FROM inventory_inventorydetail d
  JOIN products_product p ON p.id=d.product_id
  WHERE d.is_active=1 AND COALESCE(p.batch_control,0)=1
    AND TRIM(COALESCE(d.batch_no,''))=''
  UNION ALL
  SELECT 'transaction', t.id, t.owner_id, t.warehouse_id, t.product_id,
         p.code, p.name, t.location_id,
         t.batch_no, t.production_date, t.expiry_date, 'missing_batch_no'
  FROM inventory_inventorytransaction t
  JOIN products_product p ON p.id=t.product_id
  WHERE t.is_active=1 AND t.posted_at IS NOT NULL
    AND COALESCE(p.batch_control,0)=1
    AND TRIM(COALESCE(t.batch_no,''))=''
  UNION ALL
  SELECT 'detail', d.id, d.owner_id, d.warehouse_id, d.product_id,
         p.code, p.name, d.location_id,
         d.batch_no, d.production_date, d.expiry_date, 'missing_expiry_date'
  FROM inventory_inventorydetail d
  JOIN products_product p ON p.id=d.product_id
  WHERE d.is_active=1 AND COALESCE(p.expiry_control,0)=1
    AND d.expiry_date IS NULL
  UNION ALL
  SELECT 'transaction', t.id, t.owner_id, t.warehouse_id, t.product_id,
         p.code, p.name, t.location_id,
         t.batch_no, t.production_date, t.expiry_date, 'missing_expiry_date'
  FROM inventory_inventorytransaction t
  JOIN products_product p ON p.id=t.product_id
  WHERE t.is_active=1 AND t.posted_at IS NOT NULL
    AND COALESCE(p.expiry_control,0)=1
    AND t.expiry_date IS NULL
  UNION ALL
  SELECT 'detail', d.id, d.owner_id, d.warehouse_id, d.product_id,
         p.code, p.name, d.location_id,
         d.batch_no, d.production_date, d.expiry_date, 'missing_production_date'
  FROM inventory_inventorydetail d
  JOIN products_product p ON p.id=d.product_id
  WHERE d.is_active=1 AND COALESCE(p.expiry_control,0)=1
    AND p.expiry_basis='MFG' AND d.production_date IS NULL
  UNION ALL
  SELECT 'transaction', t.id, t.owner_id, t.warehouse_id, t.product_id,
         p.code, p.name, t.location_id,
         t.batch_no, t.production_date, t.expiry_date, 'missing_production_date'
  FROM inventory_inventorytransaction t
  JOIN products_product p ON p.id=t.product_id
  WHERE t.is_active=1 AND t.posted_at IS NOT NULL
    AND COALESCE(p.expiry_control,0)=1
    AND p.expiry_basis='MFG' AND t.production_date IS NULL
  UNION ALL
  SELECT 'detail', d.id, d.owner_id, d.warehouse_id, d.product_id,
         p.code, p.name, d.location_id,
         d.batch_no, d.production_date, d.expiry_date,
         'expiry_before_production_date'
  FROM inventory_inventorydetail d
  JOIN products_product p ON p.id=d.product_id
  WHERE d.is_active=1 AND d.production_date IS NOT NULL
    AND d.expiry_date IS NOT NULL AND d.expiry_date < d.production_date
  UNION ALL
  SELECT 'transaction', t.id, t.owner_id, t.warehouse_id, t.product_id,
         p.code, p.name, t.location_id,
         t.batch_no, t.production_date, t.expiry_date,
         'expiry_before_production_date'
  FROM inventory_inventorytransaction t
  JOIN products_product p ON p.id=t.product_id
  WHERE t.is_active=1 AND t.posted_at IS NOT NULL
    AND t.production_date IS NOT NULL AND t.expiry_date IS NOT NULL
    AND t.expiry_date < t.production_date
) issues
GROUP BY source, id, owner_id, warehouse_id, product_id, product_code,
         product_name, location_id, batch_no, production_date, expiry_date
ORDER BY source, id
"""


def fetch_live_rows():
    conn = MySQLdb.connect(
        user="wmsuser",
        unix_socket="/run/mysqld/mysqld.sock",
        db="wms_db",
        charset="utf8mb4",
        cursorclass=MySQLdb.cursors.DictCursor,
    )
    try:
        cur = conn.cursor()
        cur.execute(LIVE_ISSUE_SQL)
        return cur.fetchall()
    finally:
        conn.close()


def key_from_row(row):
    return (row.get("source", "").strip(), int(row.get("id") or 0))


def main():
    live_rows = fetch_live_rows()
    live_by_key = {(row["source"], int(row["id"])): row for row in live_rows}
    live_keys = set(live_by_key)
    live_problem_counts = Counter()
    for row in live_rows:
        for problem in (row["problems"] or "").split(","):
            if problem:
                live_problem_counts[problem] += 1

    repair_rows = read_csv(REPAIR_PATH)
    repair_by_key = {}
    repair_dupes = []
    for row in repair_rows:
        key = key_from_row(row)
        if key in repair_by_key:
            repair_dupes.append(key)
        repair_by_key[key] = row
    repair_keys = set(repair_by_key)

    missing_from_template = sorted(live_keys - repair_keys)
    stale_in_template = sorted(repair_keys - live_keys)
    problem_mismatches = []
    for key in sorted(live_keys & repair_keys):
        live = set((live_by_key[key]["problems"] or "").split(","))
        template = set((repair_by_key[key].get("problems") or "").split(","))
        live.discard("")
        template.discard("")
        if live != template:
            problem_mismatches.append((key, live, template))

    business_rows = read_csv(BUSINESS_PATH)
    priority_rows = read_csv(PRIORITY_PATH)
    product_rows = read_csv(PRODUCT_REVIEW_PATH) if PRODUCT_REVIEW_PATH.exists() else []

    covered_keys = set()
    for row in business_rows:
        for issue_id in split_ids(row.get("inventory_detail_ids")):
            covered_keys.add(("detail", issue_id))
        for issue_id in split_ids(row.get("inventory_transaction_ids")):
            covered_keys.add(("transaction", issue_id))
    business_missing = sorted(live_keys - covered_keys)
    business_extra = sorted(covered_keys - live_keys)

    fill_cols = [
        "business_confirmed_batch_no",
        "business_confirmed_production_date",
        "business_confirmed_expiry_date",
        "evidence_source",
        "confirmed_by",
        "confirmed_at",
    ]
    reply_filled = {
        col: sum(1 for row in business_rows if (row.get(col) or "").strip())
        for col in fill_cols
    }
    priority_filled = {
        col: sum(1 for row in priority_rows if (row.get(col) or "").strip())
        for col in fill_cols
    }

    product_ids_live = {int(row["product_id"]) for row in live_rows}
    product_ids_review = {
        int(row["product_id"]) for row in product_rows if row.get("product_id")
    }
    product_missing = sorted(product_ids_live - product_ids_review)
    product_extra = sorted(product_ids_review - product_ids_live)
    product_bucket_counts = Counter(
        row.get("review_bucket", "") for row in product_rows
    )

    with MISMATCH_PATH.open("w", newline="", encoding="utf-8-sig") as fh:
        fieldnames = [
            "mismatch_type",
            "source",
            "id",
            "live_problems",
            "template_problems",
            "product_code",
            "product_name",
        ]
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for key in missing_from_template:
            row = live_by_key[key]
            writer.writerow(
                {
                    "mismatch_type": "missing_from_template",
                    "source": key[0],
                    "id": key[1],
                    "live_problems": row["problems"],
                    "template_problems": "",
                    "product_code": row["product_code"],
                    "product_name": row["product_name"],
                }
            )
        for key in stale_in_template:
            row = repair_by_key[key]
            writer.writerow(
                {
                    "mismatch_type": "stale_in_template",
                    "source": key[0],
                    "id": key[1],
                    "live_problems": "",
                    "template_problems": row.get("problems", ""),
                    "product_code": row.get("product_code", ""),
                    "product_name": row.get("product_name", ""),
                }
            )
        for key, live, template in problem_mismatches:
            row = live_by_key[key]
            writer.writerow(
                {
                    "mismatch_type": "problem_mismatch",
                    "source": key[0],
                    "id": key[1],
                    "live_problems": ",".join(sorted(live)),
                    "template_problems": ",".join(sorted(template)),
                    "product_code": row["product_code"],
                    "product_name": row["product_name"],
                }
            )

    report = [
        "# Inventory Tracking Workpack Validation 2026-06-25",
        "",
        "## Summary",
        "",
        f"- Live issue objects: {len(live_rows)}",
        f"- Live problem flags: {sum(live_problem_counts.values())}",
    ]
    for name, count in sorted(live_problem_counts.items()):
        report.append(f"  - {name}: {count}")
    report.extend(
        [
            f"- Repair template rows: {len(repair_rows)}",
            f"- Business reply rows: {len(business_rows)}",
            f"- Priority rows: {len(priority_rows)}",
            f"- Product review rows: {len(product_rows)}",
            "",
            "## Template Freshness",
            "",
            f"- Missing live issue objects from repair template: {len(missing_from_template)}",
            f"- Stale objects in repair template no longer live issues: {len(stale_in_template)}",
            f"- Problem flag mismatches between live data and template: {len(problem_mismatches)}",
            f"- Duplicate source/id rows in repair template: {len(repair_dupes)}",
            "",
            "## Business Reply Coverage",
            "",
            f"- Live issue objects not covered by business reply id lists: {len(business_missing)}",
            f"- Business reply id references that are not live issue objects: {len(business_extra)}",
        ]
    )
    for col in fill_cols:
        report.append(
            f"- {col}: business_reply_filled={reply_filled[col]} "
            f"priority_filled={priority_filled[col]}"
        )
    report.extend(
        [
            "",
            "## Product Review Coverage",
            "",
            f"- Live issue products not covered by product review: {len(product_missing)}",
            f"- Product review rows not in live issue products: {len(product_extra)}",
        ]
    )
    for bucket, count in sorted(product_bucket_counts.items()):
        report.append(f"- {bucket}: {count}")
    report.extend(
        [
            "",
            "## Mismatch Detail",
            "",
            f"Mismatch detail CSV: `{MISMATCH_PATH.name}`",
            "",
        ]
    )
    if not missing_from_template and not stale_in_template and not problem_mismatches:
        report.append(
            "Repair template matches current live issue object set and problem flags."
        )
    else:
        report.append(
            "Repair template does not fully match current live issue object set; "
            "inspect mismatch CSV before business filling."
        )
    REPORT_PATH.write_text("\n".join(report) + "\n", encoding="utf-8")

    print("live_objects", len(live_rows))
    print("live_problem_flags", dict(live_problem_counts))
    print(
        "template_rows",
        len(repair_rows),
        "missing",
        len(missing_from_template),
        "stale",
        len(stale_in_template),
        "problem_mismatches",
        len(problem_mismatches),
        "dupes",
        len(repair_dupes),
    )
    print(
        "business_missing", len(business_missing), "business_extra", len(business_extra)
    )
    print(
        "product_missing",
        len(product_missing),
        "product_extra",
        len(product_extra),
        dict(product_bucket_counts),
    )
    print("wrote", REPORT_PATH)
    print("wrote", MISMATCH_PATH)


if __name__ == "__main__":
    main()
