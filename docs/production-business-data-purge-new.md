# 含商品档案的生产业务数据清理运行手册

`purge_business_data_new` 在标准 `purge_business_data` 的清理范围基础上，额外删除
商品档案及其直接维护表。原有 `purge_business_data` 行为不变，仍会保留商品档案。

## 额外清理范围

新版命令额外删除：

- 商品 `Product`
- 商品包装 `ProductPackage`
- 商品条码 `ProductBarcode`
- 商品外部标识 `ProductExternalIdentifier`
- 商品标识注册表 `ProductIdentifierRegistry`

商品分类、品牌、计量单位和温区属于可复用字典，仍然保留。GS1 查询缓存和服务商限流
状态与其他业务运行数据一样会被清理。

命令只删除数据库记录，不删除 `media/` 中的商品图片文件，也不会重置数据库自增 ID
或单号序列。商品和相关业务数据删除成功后，会在同一事务中将所有货主的
`next_sku_sequence` 重置为 `1`；任何清理或审计写入失败都会同时回滚该重置。

## 使用方法

先执行只读预检：

```bash
.venv/bin/python manage.py purge_business_data_new --dry-run
```

确认输出目标、`DELETE`、`KEEP`、`MISSING` 和 `BLOCK` 后，停止 Web、后台任务、
计费调度及其他写入方，并创建和核验完整数据库备份。正式执行示例：

```bash
.venv/bin/python manage.py purge_business_data_new \
  --execute \
  --confirm-target 127.0.0.1:3306/wms_db \
  --operator admin \
  --backup-reference backup-20260814-001 \
  --maintenance-confirmed
```

新版清单版本为 `2026-08-14.2`，成功和失败尝试使用审计事件
`BUSINESS_DATA_PURGE_NEW`，审计来源为 `purge_business_data_new`。成功审计的
`after.reset_owner_sku_sequences` 记录实际发生序号变化的货主数量。

`--backup-reference` 只记录备份编号、工单号或路径，不会验证备份是否存在或可恢复。
由于此命令会删除商品档案，应使用已经核验的完整数据库备份；项目现有的
`backup_preserved_data` 按标准清单备份并包含商品档案，恢复该备份会恢复商品数据。

其余维护窗口、失败回滚、命名锁和恢复要求参见
[`production-business-data-purge.md`](production-business-data-purge.md)。
