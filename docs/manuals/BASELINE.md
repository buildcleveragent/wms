# WMS 角色手册业务基线

**文档版本：** V2.1

**记录日期：** 2026-09-07（Asia/Shanghai）

- 基线提交：`c9175bfcb837fe740471710c5267ac8d5147911d`（`main`）。
- 采用当前工作区实际代码，包含未提交业务修改；版本日期不代表已部署日期。
- 核对优先级：服务端业务与权限校验、已注册客户端页面和操作、现有测试契约、旧手册。
- 本次仅编辑文档、文档图片及构建/校验脚本，不修改业务代码，不连接数据库。

## 工作区内容指纹

业务范围为 `allapp/`、`wmsmaster/`、五个客户端目录、`templates/`、`static/`、`requirements/` 中 Git 已跟踪或未忽略的文件。
共记录 1138 个文件；逐文件 SHA-256 按相对路径排序后形成 JSON，再计算集合指纹。
此记录仅用于本次文档核对前后的漂移检查，不包含凭据或业务数据。

- 开始指纹：`356bd7e4f225a505489b66b7dba5e59f09e1af6728a57ae1db9db7bc7e664103`。
- 交付前复核结果见[文档验收记录](VALIDATION.md)。

## 开始时已存在的未提交变更

以下修改均早于本次手册更新。文档描述包含这些代码，不将其推断为已经上线。

```text
 M allapp/accounts/admin.py
 M allapp/accounts/tests.py
 M allapp/core/test_preserved_data_transfer.py
 M allapp/inbound/excel_import.py
 M allapp/inbound/export_print.py
 M allapp/inventory/views.py
 M allapp/outbound/export_print.py
 M allapp/outbound/services.py
 M allapp/pos/models.py
 M allapp/pos/serializers.py
 M allapp/pos/services.py
 M allapp/pos/shift_services.py
 M allapp/pos/tests.py
 M allapp/pos/views.py
 M allapp/products/identifier_excel.py
 M allapp/products/test_identifier_migrations.py
 M allapp/reports/views.py
 M allapp/salesapp/test_salemini_unit.py
 M allapp/salesapp/tests.py
 M allapp/tasking/admin.py
 M allapp/tasking/views_relocation.py
 M allapp/tasking/views_replenishment.py
 M requirements/dev.txt
 M requirements/prod.txt
 M requirements/requirements.in
 M requirements/requirements.txt
 M requirements/test.txt
 M sales-miniapp/package-lock.json
 M sales-miniapp/package.json
 M sales-miniapp/scripts/run-quality-gate.mjs
 M sales-miniapp/stores/cart.js
 M sales-miniapp/utils/request.js
 M wmsandroid/nativeResources/android/res/xml/network_security_config.xml
 M wmsandroid/package-lock.json
 M wmsandroid/package.json
 M wmsandroid/pages/inventory/move/detail.vue
 M wmsandroid/pages/inventory/replenish/detail.vue
 M wmsandroid/tests/unit/mobileContracts.test.js
 M wmsandroid/utils/request.js
 M wmsbossbilling/package-lock.json
 M wmsbossbilling/package.json
 M wmsbossbilling/utils/request.js
 M wmsmaster/auth_views.py
 M wmsmaster/settings.py
 M wmsmaster/urls.py
 M wmsownersale/package-lock.json
 M wmsownersale/package.json
 M wmsownersale/store/auth.js
 M wmsownersale/tests/auth-store.test.js
 M wmsownersale/utils/request.js
 M wmspda/components/outbound/AssistedOrderPanel.vue
 M wmspda/package-lock.json
 M wmspda/package.json
 M wmspda/pages/inventory/move/detail.vue
 M wmspda/pages/inventory/replenish/detail.vue
 M wmspda/pages/orders/cart.vue
 M wmspda/pages/pos/index.vue
 M wmspda/pages/pos/report.vue
 M wmspda/tests/unit/requestBaseUrl.test.js
 M wmspda/utils/outboundPrint.js
 M wmspda/utils/receiveTaskDocument.js
 M wmspda/utils/request.js
?? allapp/accounts/login_middleware.py
?? allapp/accounts/test_session_login_throttle.py
?? allapp/core/print_tickets.py
?? allapp/core/test_coverage_gate.py
?? allapp/core/test_database_safety.py
?? allapp/core/test_external_acceptance.py
?? allapp/core/test_junit_reader.py
?? allapp/core/test_load_client_safety.py
?? allapp/core/test_release_orchestration.py
?? allapp/core/test_release_readiness.py
?? allapp/core/test_security_acceptance.py
?? allapp/core/test_test_candidate.py
?? allapp/core/test_test_coordination.py
?? allapp/core/test_test_evidence.py
?? allapp/core/test_test_process.py
?? allapp/core/xlsx_safety.py
?? allapp/pos/access.py
?? allapp/pos/migrations/0010_poscustomerrepayment_idempotency_fingerprint_and_more.py
?? allapp/pos/test_cross_flow_acceptance.py
?? allapp/pos/test_repayment_concurrency.py
?? allapp/pos/test_repayment_migrations.py
?? allapp/reports/test_pdf_acceptance.py
?? allapp/tasking/admin_scope.py
?? allapp/tasking/test_movement_api_acceptance.py
?? requirements/test-layer.txt
?? sales-miniapp/tests/cart-recovery.test.mjs
?? sales-miniapp/tests/request-auth.test.mjs
?? wmsandroid/tests/unit/accountSwitch.test.js
?? wmsandroid/tests/unit/printTickets.test.js
?? wmsandroid/tests/unit/scanSequence.test.js
?? wmsbossbilling/tests/refresh-recovery.test.js
?? wmsmaster/test_coordination.py
?? wmsmaster/test_database.py
?? wmsmaster/test_evidence.py
?? wmsownersale/tests/refresh-recovery.test.js
?? wmspda/tests/unit/accountSwitch.test.js
?? wmspda/tests/unit/printTickets.test.js
?? wmspda/tests/unit/repaymentRecovery.test.js
?? wmspda/tests/unit/scanSequence.test.js
?? wmspda/utils/repaymentRecovery.js
```

## 文档与图片基线

- 六个角色分册与合订版共同使用 `src/` 中的正文；专项指南独立维护。
- [角色与动作矩阵](ROLE_MATRIX.md)与[内容核对记录](CONTENT_REVIEW.md)提供维护依据。
- 现有登录截图未通过当前界面一致性核对，本版停止引用；文字登录步骤以当前页面为准。
- 角色入口图依据当前角色与主要客户端重新校正，属于导航示意图，不是实机截图。
- 文档构建和静态核对不能替代启用功能后的权限验证、支付联调或设备验收。
