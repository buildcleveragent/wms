# 生产业务数据清理运行手册

`purge_business_data` 用于清空易变业务数据，同时保留表结构、Django 迁移记录、
用户权限、货主客户、仓库基础档案和可复用配置。命令只执行 `DELETE`，不会执行
`DROP`、`TRUNCATE`、重置自增 ID、回退商品 SKU 序号或回退单号序列。

## 重要限制

- 当前仅支持 MySQL。
- 命令不会创建或验证备份，只会把备份编号或路径写入审计事件。
- 命令不会停止 Web、后台任务或计费调度；正式执行前必须由运维停止这些进程。
- 命令不会删除 `media/` 中的商品图片等文件。
- Django Session 和 DRF Token 会被删除。SimpleJWT 没有启用撤销机制，因此已经签发
  的 Access Token 最长仍可使用 1 天，Refresh Token 最长仍可使用 7 天。
- 数据库中出现未分类表，或者保留表仍通过外键引用待清理表时，正式执行会被拒绝。
- 目标代码中已声明但数据库尚未通过迁移创建的表会显示为 `MISSING` 并安全跳过。

## 保留数据备份包

如果计划删除并重建整个数据库，应先使用 `backup_preserved_data` 导出保留模型。
备份包是权限为 `0700` 的目录，包含 `preserved-data.sql.gz` 和 `manifest.json`；
其中含用户密码哈希、联系方式、地址和权限配置等敏感数据，必须放在加密存储中并异地保存。

备份不包含 `AuditEvent`、`SystemLog`、Django Admin Log、`StrategyLog`、业务清理模型或
`django_migrations`。商品分类图片只备份数据库路径，`media/` 文件必须另行备份。

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

1. 通知业务进入维护窗口，停止 Web、后台任务、计费调度和其他数据库写入方。
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
   `BUSINESS_DATA_PURGE`、操作者、目标库、备份引用和每张表的删除行数。
7. 执行结构迁移：

   ```bash
   .venv/bin/python manage.py migrate --noinput
   ```

8. 重新导入商品档案及所需业务数据，随后重建库存快照和报表 ETL。
9. 执行数据准确性核对和关键业务冒烟测试。
10. 恢复 Web、后台任务及调度服务，结束维护窗口。

如果采用删除整个数据库再重建的方式，应将第 2 步替换为上述保留数据备份，并在新库
`migrate` 后先执行 `restore_preserved_data`，再导入商品与业务数据。

## 失败处理

- SQL 执行失败时，整批 `DELETE` 会回滚，当前连接的外键检查设置会恢复；命令会尽力
  写入失败审计事件。
- 如果提示命名锁已被占用，说明另一清理任务仍在运行；不得并行执行，先确认原任务状态。
- 如果进程被强制终止，MySQL 会回滚未提交事务并释放连接级命名锁。重新执行前必须再次
  dry-run，并核对业务表是否仍完整。
- 不要手工删除 `django_migrations`，也不要用 `DROP TABLE` 或 `TRUNCATE` 替代本命令。
