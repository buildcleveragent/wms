# Business Flow Smoke Checklist

This checklist covers the 12 numbered business flows in the [test plan](test-plan.md).
Use it for demo rehearsal, staged verification, and client-side smoke after backend regression is green.

Automated suite:

- `./.venv/bin/python -m pytest --reuse-db -q allapp/test_business_flows.py`

## 1. Receive Without Order -> Inventory Visible

Automated:

- `BusinessFlowTests.test_flow_1_receive_without_order_inventory_visible`

Manual smoke:

- Login to PDA or warehouse-facing receive entry.
- Submit one no-order receive for an existing SKU, explicit owner, warehouse, and location.
- Confirm the receive request returns success and the task is posted.
- Open inventory summary and inventory detail for the same warehouse SKU code and location.

Expected:

- Inventory detail shows the new on-hand quantity.
- Inventory summary is updated.
- A receive/post record is traceable from the task.

## 2. Outbound -> Approve -> Generate Task -> Scan -> Post

Automated:

- `BusinessFlowTests.test_flow_2_outbound_approve_scan_post`

Manual smoke:

- In owner-side order entry, create one outbound order for a warehouse SKU code with available stock.
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
- Open inventory list and confirm the billed warehouse SKU code is visible.
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
- Scan the warehouse SKU code to full quantity and submit review.
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

## 9. Formal Inbound Order -> Receive -> Outbound From the Receiving Location

**Automated** · `BusinessFlowTests.test_flow_9_formal_inbound_order_to_outbound_full_chain`

- **Roles and documents.** The owner submits and approves an inbound order for 5 units of a synthetic SKU. The warehouse manager confirms it. Release the receiving task, scan 5 into the receiving location, mark all 5 accepted, then have an authorized reviewer approve and post.
- **Manual smoke.** Confirm the receiving location has 5 on hand and 5 available and that a putaway task exists. Leave that task pending for this scenario. The owner creates and approves an outbound order for 3. The warehouse manager confirms it, the picker scans 3 from the receiving location and submits for review, and a second operator with posting permission posts the pick.
- **Expected and evidence.** Both the receiving and picking tasks show `POSTED`. The receiving location ends with 2 on hand and 2 available. Under `flow-09/`, save the order and task details, the before/after inventory detail, and the `RECEIVE` and `ISSUE` ledger rows with their respective task IDs.

## 10. Formal Inbound -> Putaway -> Outbound From the Picking Location

**Automated** · `BusinessFlowTests.test_flow_10_formal_inbound_putaway_to_outbound_full_chain`

- **Roles and documents.** The owner submits and approves a new inbound order for 5 units of a fresh SKU. The warehouse team confirms, receives and posts all 5. Release and assign the resulting putaway task. The warehouse operator records the destination picking location and moves all 5 units there for review and posting.
- **Manual smoke.** After putaway, inspect both locations. Create, approve and confirm a separate outbound order for 3. Check the source location on the pick line, then have the picker scan the quantity and a second operator review and post.
- **Expected and evidence.** The posted putaway leaves 0 at the receiving location and 5 at the picking location. Its source `ISSUE` and destination `RECEIVE` share the putaway scan's posting batch. The posted outbound pick leaves 2 on hand and 2 available at the picking location. Under `flow-10/`, save the orders, task lines with both locations, scan and movement rows with batch ID, and final location balances.

## 11. Two Outbound Order Lines -> Three Location-Specific Pick Lines

**Automated** · `BusinessFlowTests.test_flow_11_multiline_multilocation_outbound_full_chain`

- **Roles and documents.** The warehouse manager confirms starting stock for two synthetic SKUs. Place 4 units of A in the primary picking location, 3 of A in overflow and 4 of B in a third location. The owner creates and approves one two-line outbound order for 6 of A and 4 of B. The warehouse manager confirms it.
- **Manual smoke.** Verify the picking task splits A across two source locations and keeps B on one line. The picker records each line's planned quantity against its own location and submits for review. A second operator posts the task.
- **Expected and evidence.** The three lines issue 4, 2 and 4 units respectively. On-hand and available quantities by location end at 0, 1 and 0. Owner stock totals end at A = 1 and B = 0. Under `flow-11/`, save the two order lines, the three pick lines, the three `ISSUE` rows for that task and the inventory detail and summary by SKU.

## 12. Received and Put Away Stock -> Daily Volume -> Accrual -> Invoice

**Automated** · `BusinessFlowTests.test_flow_12_operational_inventory_to_billing_invoice_chain`

- **Roles and documents.** Finance sets receiving, putaway and pallet storage rates to CNY 0 and volume storage to CNY 2.00 per m³ per day. The owner submits and approves a formal inbound order for 5 units of a SKU with unit volume 0.25 m³. The warehouse team receives and posts all 5, then completes and posts putaway.
- **Manual smoke.** In the same owner, warehouse and service day, run the daily metric job. Finance checks the CBM metric, accrues that day's fee, locks a one-day period, and generates its bill with an invoice number.
- **Expected and evidence.** The putaway task is `POSTED`, the daily job is `SUCCESS`, and volume is 1.25 m³. The storage accrual is CNY 2.50 and reaches `INVOICED`. The bill has one line and its subtotal and total are each CNY 2.50. Under `flow-12/`, save the task, rates, dated job and CBM metric, accrual, period and bill detail showing the invoice number and totals.

### On-site evidence for flows 9–12

Run each scenario with its own staged documents. Use the four evidence folders above relative to the candidate's QA evidence location. Record the candidate revision, test environment, run date, owner and warehouse once for the batch. Fill in each row when the manual run takes place.

| Flow | Operator and reviewer roles / names | Source document and task IDs, or period and invoice numbers | Observed result / defect | Evidence link |
| --- | --- | --- | --- | --- |
| 9 | | | | |
| 10 | | | | |
| 11 | | | | |
| 12 | | | | |
