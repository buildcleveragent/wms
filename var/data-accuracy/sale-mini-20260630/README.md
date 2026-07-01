# Sale Mini Inventory Tracking Repair Workpack

生成时间：2026-06-30

这个工作包用于处理 `reconcile_data_accuracy --fail-on-issues` 报出的库存批次/效期缺失问题。

## 文件

- `inventory_tracking_business_reply.csv`
  - 给业务人员填写。
  - 每行按 `owner + product + warehouse + location` 聚合。
  - 需要填写真实的 `business_confirmed_batch_no`、`business_confirmed_production_date`、`business_confirmed_expiry_date`，并补充 `evidence_source`、`confirmed_by`、`confirmed_at`。

- `inventory_tracking_repair_template.csv`
  - 技术修复模板。
  - 包含 `InventoryDetail` / `InventoryTransaction` 的精确行 ID。
  - 不建议业务人员直接改这张表。

## 回填后执行

```bash
SECRET_KEY=test-secret-key CORS_ALLOWED_ORIGINS=http://localhost \
.venv/bin/python manage.py merge_inventory_tracking_business_reply \
  var/data-accuracy/sale-mini-20260630/inventory_tracking_repair_template.csv \
  var/data-accuracy/sale-mini-20260630/inventory_tracking_business_reply.csv \
  --output var/data-accuracy/sale-mini-20260630/inventory_tracking_repair_ready.csv
```

确认 `inventory_tracking_repair_ready.csv` 无误后再应用：

```bash
SECRET_KEY=test-secret-key CORS_ALLOWED_ORIGINS=http://localhost \
.venv/bin/python manage.py apply_inventory_tracking_repairs \
  var/data-accuracy/sale-mini-20260630/inventory_tracking_repair_ready.csv
```

最后复查：

```bash
SECRET_KEY=test-secret-key CORS_ALLOWED_ORIGINS=http://localhost \
.venv/bin/python manage.py reconcile_data_accuracy --fail-on-issues --limit 20
```

注意：不要为了通过校验而填写虚假的批号、生产日期或效期。这里修的是真实库存追踪资料，必须来自入库单据、供应商资料、仓库实物标签或其他可追溯凭证。
