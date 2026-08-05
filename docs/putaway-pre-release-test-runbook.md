# 上架功能预发布测试执行手册

## 1. 执行原则

- 仅在独立预生产环境执行写入、故障注入和并发测试。
- 环境须使用与生产一致的 MySQL、Redis、权限组和 Android PDA 构建。
- 每轮使用独立任务号前缀，测试前后各保存一次库存快照。
- 自动化结果不能替代 Android 真机、网络切换和现场人员职责分离验收。

## 2. 测试数据

准备两个货主、两个仓库、两名仓库操作员和一名仓库管理员。每个仓库配置暂存位、两个正常目标位、冻结位和停用位。商品至少包括普通商品、批次效期商品和序列号商品。

准备以下业务数据：

1. 正式入库单：两种商品、两个批次、两个来源库位。
2. 无订单入库：一种普通商品。
3. 一张100行的上架任务。
4. 1000张已发布的上架任务，用于列表和搜索容量测试。
5. 30张互不重复的已发布任务，分别分配给30个压测账号。

## 3. 自动化门禁

```bash
.venv/bin/pytest -q allapp/inbound/test_putaway_comprehensive.py
.venv/bin/pytest -q \
  allapp/inventory/test_posting_fail_closed.py \
  allapp/test_business_flows.py::BusinessFlowTests::test_flow_10_formal_inbound_putaway_to_outbound_full_chain
.venv/bin/pytest -n auto -q --cov=allapp --cov-report=term-missing \
  allapp/inbound allapp/tasking allapp/inventory
.venv/bin/pytest -q allapp/test_business_flows.py
```

通过标准：专项用例全部通过，仓储回归无失败，仓库总覆盖率不低于70%。MySQL环境必须执行并发专项用例；其他数据库上的跳过结果不作为上线证据。

## 4. 30操作员并发测试

复制以下结构到不纳入版本控制的 `putaway-actors.json`。写场景中每个账号必须使用独立任务和任务行：

```json
[
  {
    "username": "operator01",
    "password": "REDACTED",
    "search": "LOAD-PUT-01",
    "task_id": 1001,
    "line_id": 2001,
    "to_location_id": 301,
    "qty": "1.000"
  }
]
```

先执行只读基线：

```bash
python scripts/putaway_load_test.py \
  --base-url https://preprod.example.invalid \
  --actors putaway-actors.json \
  --workers 30 \
  --output var/test_artifacts/putaway-load-read.json
```

确认环境和测试数据后执行写场景：

```bash
python scripts/putaway_load_test.py \
  --base-url https://preprod.example.invalid \
  --actors putaway-actors.json \
  --workers 30 \
  --execute-writes \
  --confirm-preproduction \
  --output var/test_artifacts/putaway-load-write.json
```

验收门槛：查询P95不超过1秒，写入P95不超过2秒，错误率低于1%；30张任务均只产生一次有效上架记录，库存无重复或丢失。

## 5. Android PDA与H5验收

至少使用两台不同分辨率的Android PDA，逐项记录截图：

- 登录后首页显示“上架”入口；任务列表搜索、分页和数量汇总正确。
- 领取后其他账号不可见或不可操作；开始后状态变为执行中。
- 库位搜索不显示冻结、停用和其他仓库库位。
- 分两次提交三位小数数量，已上架量和待上架量即时刷新。
- 验证空值、零数、负数、超量、来源库位及更换目标库位提示。
- 提交时切断网络，再用同一页面重试，确认只产生一条记录。
- 连续点击、切后台、重新登录和返回列表不会丢失或重复状态。
- H5执行登录、列表、详情、选位和一次提交兼容性冒烟。

## 6. 端到端与故障恢复

依次执行并保存源单号、任务号、截图、API响应和库存流水：

1. 正式入库审批、收货、收货过账、任务生成、发布、领取、上架、审核、过账和入库单关闭。
2. 无订单收货、上架和库存可见。
3. 多商品、多批次、多来源库位的分次上架。
4. 上架后创建出库单，并从目标库位完成分配和拣货。
5. 操作员执行、管理员审核过账；验证双方不能越权。
6. 审核驳回、来源库存不足、过账写入失败和恢复重试。

故障场景必须证明任务、扫描日志、库存明细和流水在失败后保持原子一致。不得通过手工修改库存完成恢复。

## 7. 库存对账与报告

```bash
.venv/bin/python manage.py reconcile_data_accuracy --fail-on-issues
```

对每个测试任务额外核对：来源与目标库存变化相抵；两条流水数量相反且`pair_id`和`posting_batch`一致；批次、生产日期、有效期、序列号和货主保持不变；所有关联任务过账后入库单才关闭。

执行结果填写到 `docs/putaway-acceptance-report-template.md`。只有核心链路100%通过、整体通过率不低于95%、P0/P1为零且核心流程无P2缺陷时，结论才可填写“建议上线”。
