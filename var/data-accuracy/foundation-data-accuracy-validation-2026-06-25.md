# Foundation Data Accuracy Validation 2026-06-25

## Check Counts

| Check | Issues |
| --- | ---: |
| inventory_detail_negative_quantities | 0 |
| inventory_summary_negative_quantities | 0 |
| inventory_transaction_zero_qty_delta | 0 |
| inventory_detail_location_warehouse_mismatch | 0 |
| inventory_transaction_location_warehouse_mismatch | 0 |
| inventory_detail_subwarehouse_warehouse_mismatch | 0 |
| inventory_transaction_subwarehouse_warehouse_mismatch | 0 |
| location_subwarehouse_warehouse_mismatch | 0 |
| inventory_detail_product_owner_mismatch | 0 |
| inventory_transaction_product_owner_mismatch | 0 |
| inventory_summary_product_owner_mismatch | 0 |
| task_line_from_location_warehouse_mismatch | 0 |
| task_line_to_location_warehouse_mismatch | 0 |
| task_owner_warehouse_null_guard | 0 |
| task_line_negative_quantities | 0 |
| task_line_done_gt_plan | 0 |
| outbound_line_nonpositive_qty | 0 |
| outbound_line_product_owner_mismatch | 0 |
| posted_task_line_reserved_status | 2 |

## Status Distribution

| Kind | Status | Count |
| --- | --- | ---: |
| outbound_submit_status | SUBMITTED | 14 |
| outbound_approval_status | OWNER_APPROVED | 1 |
| outbound_approval_status | OWNER_PENDING | 1 |
| outbound_approval_status | WHS_APPROVED | 12 |
| outbound_closed | 0 | 2 |
| outbound_closed | 1 | 12 |
| task_status | COMPLETED | 29 |
| task_status | DRAFT | 28 |
| task_review_status | APPROVED | 29 |
| task_review_status | NOT_READY | 28 |
| task_posting_status | NOT_READY | 28 |
| task_posting_status | POSTED | 29 |
| task_line_status | DRAFT | 199 |
| task_line_status | RELEASED | 199 |
| task_line_status | RESERVED | 2 |

## Issue Detail

Issue detail CSV: `foundation-data-accuracy-issues-2026-06-25.csv`

Non-zero issue rows: 2
