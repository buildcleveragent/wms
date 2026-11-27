# 含商品档案的生产业务数据清理运行手册

`purge_business_data_new` 在标准 `purge_business_data` 的清理范围基础上，额外删除
商品档案及其直接维护表。

## 额外清理范围

新版命令额外删除：

- 商品 `Product`
- 商品包装 `ProductPackage`
- 商品条码 `ProductBarcode`
- 商品外部标识 `ProductExternalIdentifier`
- 商品标识注册表 `ProductIdentifierRegistry`
- 商品库存预警策略 `ProductInventoryAlertPolicy`
- 货主商品映射 `OwnerProductMapping`
- 跨货主转货商品来源绑定 `TransferProductBinding`

商品分类、品牌、计量单位和温区属于可复用字典，仍然保留。GS1 查询缓存和服务商限流
状态与其他业务运行数据一样会被清理。

两种清理命令都会删除老板看板已读进度、检查状态、货主导出任务和小程序结算幂等记录。
数据库事务提交后，命令按被清除的导出任务 ID 清理 `OWNER_ARCHIVE_ROOT` 中对应的
`<id>.xlsx`、`<id>.part` 和 `<id>.work`。商品图片等 `media/` 文件保留。

删除商品和相关业务数据的事务会同时将所有货主的 `next_sku_sequence` 重置为 `1`。
事务失败时序号随商品数据回滚。数据库自增 ID 和单号序列维持原值。

## 使用方法

先执行只读预检：

```bash
.venv/bin/python manage.py purge_business_data_new --dry-run
```

确认输出目标、`DELETE`、`KEEP`、`MISSING` 和 `BLOCK` 后，停止 Web、
`owner-archive-worker`、`inventory-close-worker`、计费调度及其他写入方，并创建和核验完整数据库备份。核对清理命令与该库的
`owner-archive-worker` 共用该库专用的私有卷。隔离演练使用独立 `OWNER_ARCHIVE_ROOT`。正式执行示例：

```bash
.venv/bin/python manage.py purge_business_data_new \
  --execute \
  --confirm-target 127.0.0.1:3306/wms_db \
  --operator admin \
  --backup-reference backup-20260814-001 \
  --maintenance-confirmed
```

新版清单版本为 `2026-11-25.2`，成功和失败尝试使用审计事件
`BUSINESS_DATA_PURGE_NEW`，审计来源为 `purge_business_data_new`。成功审计的
`after.reset_owner_sku_sequences` 记录实际发生序号变化的货主数量。

`--backup-reference` 只记录备份编号、工单号或路径，不会验证备份是否存在或可恢复。
由于此命令会删除商品档案，应使用已经核验的完整数据库备份；项目现有的
`backup_preserved_data` 按标准清单备份，恢复该备份会恢复商品档案与转货商品来源绑定。

若数据库已提交但归档文件清理失败，保持维护窗口，检查归档卷和权限。将以下占位符替换为
本次成功清理审计 ID、原目标和超级管理员，再重试该批文件。

```bash
.venv/bin/python manage.py purge_business_data_new \
  --retry-archive-cleanup <成功清理审计ID> \
  --confirm-target <原目标> --operator <超级管理员> --maintenance-confirmed
```

其余维护窗口、失败回滚、命名锁和恢复要求参见
[`production-business-data-purge.md`](production-business-data-purge.md)。

日结试点配置随主数据保留。重新开账时，先核对或调整试点首个业务日，再启动日结进程；启用中的试点会从其首个业务日起补跑。清理范围包含日结单、调度进度、盘点期初、复核历史及附件索引。维护窗口需同时备份数据库和该库专用的 `PRIVATE_CLOSE_ROOT`。数据库清理提交后，仅按审计清单删除日结附件。中途失败可用同一命令的 `--retry-archive-cleanup <成功审计ID>` 和原维护确认参数续跑。
