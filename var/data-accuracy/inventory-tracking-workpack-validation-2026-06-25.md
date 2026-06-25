# Inventory Tracking Workpack Validation 2026-06-25

## Summary

- Live issue objects: 403
- Live problem flags: 677
  - missing_batch_no: 391
  - missing_expiry_date: 137
  - missing_production_date: 149
- Repair template rows: 403
- Business reply rows: 182
- Priority rows: 182
- Product review rows: 182

## Template Freshness

- Missing live issue objects from repair template: 0
- Stale objects in repair template no longer live issues: 0
- Problem flag mismatches between live data and template: 0
- Duplicate source/id rows in repair template: 0

## Business Reply Coverage

- Live issue objects not covered by business reply id lists: 0
- Business reply id references that are not live issue objects: 0
- business_confirmed_batch_no: business_reply_filled=0 priority_filled=0
- business_confirmed_production_date: business_reply_filled=0 priority_filled=0
- business_confirmed_expiry_date: business_reply_filled=0 priority_filled=0
- evidence_source: business_reply_filled=0 priority_filled=0
- confirmed_by: business_reply_filled=0 priority_filled=0
- confirmed_at: business_reply_filled=0 priority_filled=0

## Product Review Coverage

- Live issue products not covered by product review: 0
- Product review rows not in live issue products: 0
- A_疑似包装耗材_确认控制口径: 16
- B_效期商品_补真实批次生产效期: 43
- C_批次商品_补批次或确认关闭控制: 123

## Mismatch Detail

Mismatch detail CSV: `inventory-tracking-workpack-mismatches-2026-06-25.csv`

Repair template matches current live issue object set and problem flags.
