# 生产业务数据清理运行手册

`purge_business_data` 使用事务内的 `DELETE` 清空易变业务数据，保留表结构、Django 迁移记录、
用户权限、货主客户、仓库和商品档案以及可复用配置。数据库自增 ID、仓库 SKU 编码序号和单号序列维持原值。

如果需要同时删除商品档案，请改用 `purge_business_data_new`，并遵循
[`production-business-data-purge-new.md`](production-business-data-purge-new.md)。

## 重要限制

- 当前仅支持 MySQL。
- 命令不会创建或验证备份，只会把备份编号或路径写入审计事件。
- 正式执行前由运维停止 Web、`owner-archive-worker`、`inventory-close-worker`、计费调度和其他写入方。
- 核对清理命令与该库的 `owner-archive-worker` 挂载同一专用私有卷。任务 ID 只在本库内唯一，
  隔离恢复或演练须使用独立的 `OWNER_ARCHIVE_ROOT`。
- 标准命令保留商品、包装、条码、外部标识、标识注册表及跨货主转货商品来源绑定
  `TransferProductBinding`。GS1 查询缓存、服务商限流状态及依赖商品的补货策略会被删除，
  清理后需要重新配置补货策略。
- 两种清理命令都会删除老板看板已读进度 `BossReadProgress`、检查状态 `BossCheckState`、
  货主导出任务 `OwnerArchiveJob` 和小程序结算幂等记录 `SaleMiniCheckoutSubmission`。
  数据库事务提交后，命令按本次删除的导出任务 ID 清理 `OWNER_ARCHIVE_ROOT` 下对应的
  `<id>.xlsx`、`<id>.part` 和 `<id>.work`。商品图片等 `media/` 文件保留。
- Django Session、DRF Token、SimpleJWT Outstanding/Blacklist 记录会被删除，正式清理后
  所有用户必须重新登录。清空黑名单不会主动撤销已经签发的 JWT；Access Token 最长仍可使用
  1 天，Refresh Token 最长仍可使用 7 天。
- 数据库中出现未分类表，或者保留表仍通过外键引用待清理表时，正式执行会被拒绝。
- 目标代码中已声明但数据库尚未通过迁移创建的表会显示为 `MISSING` 并安全跳过。

## 保留数据备份包

如果计划删除并重建整个数据库，应先使用 `backup_preserved_data` 导出保留模型。
备份包是权限为 `0700` 的目录，包含 `preserved-data.sql.gz` 和 `manifest.json`；
其中含用户密码哈希、联系方式、地址和权限配置等敏感数据，必须放在加密存储中并异地保存。

标准清理清单版本为 `2026-11-25.2`。该版本的保留数据备份包含转货来源绑定及其两端的商品，
排除上述四类运行记录。部署新版并完成迁移后，重新生成、复制并核验保留数据包。
恢复旧包（包括 `2026-10-23.1`）时，使用生成该包的代码和相同迁移集合在隔离空库恢复，
再按升级流程迁移。旧包缺失的来源绑定须从原库或相应完整备份核对。

备份不包含 `AuditEvent`、`SystemLog`、Django Admin Log、`StrategyLog`、业务清理模型或
`django_migrations`。商品及商品分类图片只备份数据库路径，`media/` 文件必须另行备份。

先执行只读预检：

```bash
.venv/bin/python manage.py backup_preserved_data --dry-run
```

进入维护窗口并核对预检后正式备份：

```bash
.venv/bin/python manage.py backup_preserved_data \
  --execute \
  --confirm-target 127.0.0.1:3306/wms_db \
  --operator admin \
  --output /secure-backups/wms-preserved-20260722 \
  --maintenance-confirmed
```

命令输出的 `preserved-...` 备份 ID 可作为 `purge_business_data` 的
`--backup-reference`。复制备份包后应重新计算 `preserved-data.sql.gz` 的 SHA-256，确认与
`manifest.json` 一致，并定期在隔离环境进行恢复演练。

## 恢复到新建空库

恢复只支持使用生成备份时的代码版本和完全相同的迁移集合。新建数据库后仅执行
`migrate`，不要创建超级管理员或导入任何业务数据；迁移自动创建的 ContentType、Permission、
SystemSetting 和 PrintConfig 会由备份内容替换。

```bash
.venv/bin/python manage.py migrate --noinput

.venv/bin/python manage.py restore_preserved_data \
  --dry-run \
  --input /secure-backups/wms-preserved-20260722
```

确认没有 `BLOCK` 后执行：

```bash
.venv/bin/python manage.py restore_preserved_data \
  --execute \
  --confirm-target 127.0.0.1:3306/wms_db_new \
  --operator admin \
  --input /secure-backups/wms-preserved-20260722 \
  --maintenance-confirmed \
  --fresh-database-confirmed
```

恢复命令不会执行 `DROP`、`TRUNCATE` 或建表操作，也不会覆盖新库的
`django_migrations`。已有主键会原样恢复；下一自增 ID 由 MySQL 调整为至少
`max(id) + 1`。恢复完成后再升级应用代码并执行后续迁移。

## 标准执行流程

1. 通知业务进入维护窗口，停止 Web、`owner-archive-worker`、`inventory-close-worker`、计费调度和其他数据库写入方。
2. 创建并核验数据库备份，记录备份编号、工单号或可定位的备份路径。
3. 使用即将部署的代码执行只读预检：

   ```bash
   .venv/bin/python manage.py purge_business_data --dry-run
   ```

4. 检查输出中的数据库目标、清理表、保留表、`MISSING` 和 `BLOCK`。任何 `BLOCK`
   都必须先通过更新清单或修复外键依赖解决，禁止绕过。
5. 按 dry-run 显示的目标正式执行。以下目标仅为示例：

   ```bash
   .venv/bin/python manage.py purge_business_data \
     --execute \
     --confirm-target 127.0.0.1:3306/wms_db \
     --operator admin \
     --backup-reference backup-20260722-001 \
     --maintenance-confirmed
   ```

6. 确认输出为“清理完成”，并在 Django Admin 的不可变审计事件中核对
   `BUSINESS_DATA_PURGE`、操作者、目标库、备份引用和每张表的删除行数。若本次清除了导出任务，
   确认同一清理审计 ID 有对应的 `PURGE_ARCHIVES_CLEANED` 完成记录。
7. 执行结构迁移：

   ```bash
   .venv/bin/python manage.py migrate --noinput
   ```

8. 核对保留的商品档案和转货来源绑定，以及对应导出任务的文件清理结果。重新配置补货策略，
   导入所需业务数据，随后重建库存快照和报表 ETL。
9. 执行数据准确性核对和关键业务冒烟测试。
10. 恢复 Web、后台任务及调度服务，结束维护窗口。

如果采用删除整个数据库再重建的方式，应将第 2 步替换为上述保留数据备份，并在新库
`migrate` 后先执行 `restore_preserved_data`，再导入商品与业务数据。

## 失败处理

- 若提示数据库已提交但导出文件清理未完成，保持维护窗口，检查归档卷挂载、路径和权限。
  记录本次成功清理审计 ID，使用相同命令和目标仅重试其归档文件。

  ```bash
  .venv/bin/python manage.py purge_business_data \
    --retry-archive-cleanup <成功清理审计ID> \
    --confirm-target <原目标> --operator <超级管理员> --maintenance-confirmed
  ```

  将尖括号占位符替换为实际值。重试会核对原审计中的任务 ID 和目录，拒绝清理已重新使用的任务 ID。
- SQL 执行失败时，整批 `DELETE` 会回滚，当前连接的外键检查设置会恢复；命令会尽力
  写入失败审计事件。
- 如果提示命名锁已被占用，说明另一清理任务仍在运行；不得并行执行，先确认原任务状态。
- 如果进程被强制终止，MySQL 会回滚未提交事务并释放连接级命名锁。先查看清理审计。
  如果数据库已经提交且存在待处理的归档清单，按上文重试文件清理。若未提交，再做 dry-run，
  核对业务表后重新执行。
- 不要手工删除 `django_migrations`，也不要用 `DROP TABLE` 或 `TRUNCATE` 替代本命令。

日结试点配置随主数据保留。重新开账时，先核对或调整试点首个业务日，再启动日结进程；启用中的试点会从其首个业务日起补跑。清理范围包含日结单、调度进度、盘点期初、复核历史及附件索引。维护窗口需同时备份数据库和该库专用的 `PRIVATE_CLOSE_ROOT`。数据库清理提交后，仅按审计清单删除日结附件。中途失败可用同一命令的 `--retry-archive-cleanup <成功审计ID>` 和原维护确认参数续跑。
