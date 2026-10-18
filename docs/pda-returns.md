# PDA 退货出库

入口：wmspda 首页「退货出库」，仓库统计页也提供统计入口。

## 业务规则

- 仓库操作员可建单、编辑/删除本人草稿、确认退货及整单冲销本人已完成单据；本仓记录和统计可查询、导出。
- 单据限定一个仓库、货主、接收方。同条码跨货主商品分别显示，必须明确选择库存货主。
- 操作员选择实际库位、批次和序列号。退货对象可为该货主，或可靠批次记录中的原供应商；无供应商来源只能退货主。
- 数量以基本单位录入，最多四位小数。正常退货和现场发现破损合计不得超过可用库存，已登记破损退货不得超过破损库存。已分配、锁定库存不动用。
- 确认立即扣减库存，不进入销售审核和拣货流程。整单冲销恢复原库存维度；现场发现破损恢复为破损，不能恢复为可用。冲销前应核实实物仍在仓库或已实际退回。
- 原库存维度失效、库存不足或成本层不一致时，整单操作失败，不留下部分库存变化。
- 请求结果不明时使用页面「继续确认结果」恢复同一请求，勿另开一单替代未确认请求。
- 退货统计以确认时间计入，冲销以冲销时间计入，跨日冲销保留原日实绩。不同基本单位分别汇总。导出包含汇总、明细、正常/原有破损/现场破损的区分。
- 本功能不涉及退款、价格、供应商结算或货权转移。退货记录独立于销售出库记录；库存流水来源为 `ReturnOrder`。

## 部署

通过正常发布流程应用新增的 `outbound.0009_pda_return_orders` 迁移并发布 wmspda 前端。升级已有角色组时，默认角色同步命令保留现有权限，需要额外授予仓库操作员组 `outbound.process_pda_return`。

若部署环境采用完整默认角色模板，可运行 `python manage.py sync_wms_role_groups --ensure-defaults` 补齐模板权限；自定义权限环境只给指定操作员/组添加上述权限即可。无需授予仓库管理员审批权限。

不要为上线此功能清理业务库存、重新建立成本层或改写已部署迁移。FIFO 开关沿用现有配置；记录了成本层的退货冲销恢复原层。未记录成本层的退货在后来启用 FIFO 后禁止直接冲销，需先核对成本层，避免账实不符。

## API

前缀 `/api/pda/return-orders/`：

- `GET products/`：搜索、按货主分页列出库存商品。
- `GET stock-options/?product_id=...`：分页列出具体库存与原供应商来源。
- `GET options/`：统计和记录筛选选项。
- `GET/POST /`：查询列表、创建草稿；创建须传 UUID `request_id`。
- `GET/PATCH/DELETE /{id}/`：详情、完整替换草稿内容、软删除；修改和删除须传 `version`。
- `POST /{id}/confirm/`、`POST /{id}/reverse/`：须传 UUID `request_id` 和 `version`；冲销另需 `reason`。
- `GET stats/`、`GET export/`：日期范围最多366天；默认当天。支持 `owner_id`、`supplier_id`、`recipient_type`、`product_id`、`operator_id`。导出超过50000事件行会提示缩小范围，不静默截断。

草稿明细传 `inventory_id`、`available_qty`、`damaged_qty`、`new_damage_qty`。后端重新验证全部归属、供应商来源和实际库存；不能通过请求指定其他仓库。

## 验证

按 AGENTS.md 使用 MySQL 8 和 `.env.test.local`，目标只能是 `wms_db_test`。新增迁移首次验证需 `--reuse-db --create-db`，后续复用。

- 后端：`pytest --reuse-db allapp/outbound/test_pda_returns.py`
- 前端单元：在 `wmspda` 运行 `npm run test:unit`
- 浏览器：`npm run test:e2e -- pda-returns.spec.js`
- 构建：`npm run build:h5`

Android 真机验收还需验证硬件扫码、现场网络中断恢复及 Excel 下载/打开。H5 Playwright 测试不能替代硬件验收。

### 本次界面验收

当前环境未提供 Browser 插件，采用仓库现有 Playwright / Chromium 测试实际 H5 页面，接口使用可控模拟；后端库存正确性由独立 MySQL 测试验证。

| 环境 / 检查 | 结果 |
| --- | --- |
| 手机视口 390 × 844：选择货主和供应商、三类退货数量、确认、冲销、统计 | 通过 |
| 桌面视口 1280 × 900：未知供应商只能退货主、响应丢失后刷新恢复、临时 403 后继续原请求 | 通过 |
| 草稿清空明细后重新选择其他货主 | 通过 |
| 页面脚本异常、相关控制台错误、手机页面横向溢出 | 未发现 |
| wmspda 前端单元测试 | 21 个文件、92 项通过 |
| H5 生产构建 | 通过 |

界面截图：`/tmp/wmspda-return-mobile.png`、`/tmp/wmspda-return-desktop.png`。测试与构建仍有现有工具链提示：Vite 配置未来原生加载兼容性、控制台颜色环境变量冲突、部分构建插件耗时；未屏蔽这些提示。

### 本次后端验证

- MySQL 8：因新增迁移，首次重建 `wms_db_test`，执行全部 148 个真实迁移并检查新表、约束和迁移状态；后续测试均使用 `--reuse-db`，未写入业务库 `wms_db`。
- 最终扩展回归：302 项测试、84 个子测试全部通过，包含本功能 26 项测试、完整出库模块、库存范围和锁、失败回滚、库存快照并发、访问范围及默认角色权限同步。扩展回归使用仓库提供的 `--fast-password-hashers`，仅在测试进程生效。
- 关键场景包括同条码多货主、三类数量限制、多明细整单回滚、原供应商来源校验、本人冲销、权限撤销、确认/冲销幂等与并发、删除结果丢失后重试、FIFO 原成本层恢复及破损快照。
- Django 系统检查、迁移一致性检查以及新增 Python 文件的 Black、isort、Ruff、Flake8 检查通过；`git diff --check` 通过。
- 未执行全仓库测试或生产发布；Android 真机验收尚未执行。前述首次失败的测试数据和快照主键假设已修正，最终回归无失败或跳过。

扩展回归证据：`/tmp/wms-pda-return-integration.log`、`/tmp/wms-pda-return-integration.xml`。
