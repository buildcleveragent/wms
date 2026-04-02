# Business Flow Smoke Checklist

This checklist mirrors the 8 critical business loops in [docs/test-plan.md](/wms/docs/test-plan.md).
Use it for demo rehearsal, staged verification, and client-side smoke after backend regression is green.

Automated suite:

- `./.venv/bin/python -m pytest -q allapp/test_business_flows.py`

## 1. Receive Without Order -> Inventory Visible

Automated:

- `BusinessFlowTests.test_flow_1_receive_without_order_inventory_visible`

Manual smoke:

- Login to PDA or warehouse-facing receive entry.
- Submit one no-order receive for an existing SKU, explicit owner, warehouse, and location.
- Confirm the receive request returns success and the task is posted.
- Open inventory summary and inventory detail for the same SKU and location.

Expected:

- Inventory detail shows the new on-hand quantity.
- Inventory summary is updated.
- A receive/post record is traceable from the task.

## 2. Outbound -> Approve -> Generate Task -> Scan -> Post

Automated:

- `BusinessFlowTests.test_flow_2_outbound_approve_scan_post`

Manual smoke:

- In owner-side order entry, create one outbound order for a SKU with available stock.
- Approve the order and confirm a pick task is created.
- In PDA, scan the pick task to full quantity, submit review, and post with a second user.
- Re-open the order, task, and inventory detail.

Expected:

- Pick task reaches `COMPLETED` and `POSTED`.
- Inventory on-hand decreases by the picked quantity.
- Allocated quantity is released after posting.

## 3. Cancel Or Unapprove Outbound -> Reservation Released

Automated:

- `BusinessFlowTests.test_flow_3_unapprove_or_cancel_releases_reservation`

Manual smoke:

- Create and approve an outbound order so reservation is created.
- Cancel or reject it from the available warehouse/admin action.
- Re-open inventory detail and the related pick task.

Expected:

- Reserved task is cancelled.
- Allocated quantity returns to zero.
- Available quantity returns to the pre-allocation value.

Note:

- If no public cancel endpoint exists yet, verify this loop from admin or service shell before demo.

## 4. Stock Adjustment -> Inventory Updated -> Report Visible

Automated:

- `BusinessFlowTests.test_flow_4_quick_adjust_updates_inventory_and_company_report`

Manual smoke:

- Execute one positive quick adjustment in admin or inventory operation UI.
- Open company inventory summary and owner inventory summary.
- Filter by warehouse, owner, and SKU.

Expected:

- Adjusted inventory is visible in both detail and summary views.
- Warehouse-level report numbers match the adjusted quantity.

## 5. Inventory Snapshot -> Billing Metrics -> Accrual -> Lock -> Invoice

Automated:

- `BusinessFlowTests.test_flow_5_snapshot_metrics_accrual_lock_invoice`

Manual smoke:

- Prepare one historical inventory baseline.
- Run inventory snapshot generation for the billing date.
- Run billing metric generation, accrual, period lock, and invoice generation in order.
- Open bill detail and verify line totals.

Expected:

- Historical metric is created from snapshot data, not current live inventory.
- Accrual enters `LOCKED`, then `INVOICED`.
- Bill subtotal and total match the configured billing rule.

## 6. Owner Portal -> View Inventory -> View Bill -> Export Bill

Automated:

- `BusinessFlowTests.test_flow_6_owner_portal_can_view_inventory_bill_and_export`

Manual smoke:

- Login to `wmsownersale`.
- Open inventory list and confirm the billed SKU is visible.
- Open billing period or bill list, enter bill detail, and export.
- Open the exported file locally.

Expected:

- Owner user only sees scoped inventory and bill data.
- Bill detail shows lines, totals, and period.
- Exported workbook contains bill summary and lines.

## 7. PDA -> Login -> Pick/Scan Task -> State Advances

Automated:

- `BusinessFlowTests.test_flow_7_pda_pick_scan_and_state_transition`

Manual smoke:

- Login to `wmspda`.
- Open pick-task list and enter one active task.
- Scan the SKU to full quantity and submit review.
- Refresh the task list or detail page.

Expected:

- Task appears in active list before execution.
- Line quantity advances after scan.
- Task state changes to `COMPLETED` and `PENDING REVIEW`.

## 8. Console -> Billing Overview -> Bill Detail

Automated:

- `BusinessFlowTests.test_flow_8_console_billing_overview_and_detail`

Manual smoke:

- Login to Django console with billing permissions.
- Open `/console/billing/`.
- Filter by owner, warehouse, and billing period.
- Drill into the current bill detail page from overview.

Expected:

- Overview shows totals, charge breakdown, trend, and current bill.
- Bill detail page shows lines, grouped totals, and back-link to overview.
- Console drill-down uses the same bill and period scope as the overview page.
