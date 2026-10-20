# Android 公网接入验证记录

日期：2026-10-21（Asia/Singapore）。目标 API：`http://8.148.198.200:8080`。

## 后续云端失败与修复

用户已从 HBuilderX 成功提交 Android 云端证书打包，但云端 Android 原生清单合并失败：[错误日志](https://app.liuyingyong.cn/build/errorLog/cfa9d120-ccbd-11f1-96cb-1573d4d3ee39)。`includePermissions/AndroidManifest.xml` 中 camera/autofocus 特性重复。修复已从权限数组移除两个 feature，并在独立 Android 清单中保留非必需特性的合并覆盖声明；相机权限和 Barcode 模块不变。

本次针对性 Android 配置测试 14 项、页面契约 28 页及 Android App 资源重新构建和产物检查通过；原生云端清单合并必须以重新提交后的实际结果为准。下面的 241 项全量测试和依赖审计记录属于首次交付；此次清单修改没有触发无关后端或数据库测试。正式 APK 和真机验证仍未完成。

## 已执行并通过

| 检查 | 结果 |
| --- | --- |
| `npm run test:unit` | 33 个文件、241 项测试通过 |
| `npm run test:contracts` | 28 个注册页面通过 |
| `npm run build:h5` | 构建通过 |
| Android App 编译 | `uni build -p app` 通过，输出 `dist/build/app/` |
| `node scripts/verify-android-build.mjs` | 公网 API、AppID、版本、独立包名、Barcode 模块、原生清单及 HTTP 白名单一致 |
| 后端配置与 HTTP 中间件 | 18 项测试及 18 个子测试通过 |
| Python 静态检查 | Black、isort、Ruff 通过；`git diff --check` 通过 |
| 公网只读探测 | `/healthz/live` 返回 200；`GET /api/token/` 返回 405，无重定向 |

后端测试命令：

```bash
.venv/bin/pytest --reuse-db -q --tb=short allapp/core/test_android_http_policy.py allapp/core/test_environment.py allapp/core/test_release_configuration.py
```

覆盖：HTTP 例外默认关闭、指定主机/端口/API 路径、不同来源及后台继续 HTTPS、非法 Host、伪造 forwarded host、HTTPS 安全头、现有健康检查豁免、API 仍要求登录、真实生产配置保留 Secure Cookie 与 CORS 限制。

前端增加：Android 专用开关、HTTPS、小程序/H5不接受公网 HTTP、无浏览器 URL 对象的 JS 环境、Android HTML5+ 文件读取与错误 JSON 拦截；原有认证轮换、账号切换等测试继续通过。上述原生文件测试使用模拟 API，不能代替真机验收。

这些后端测试不需要数据库连接；本次没有复用、重建或写入任何数据库，也没有迁移。没有运行无关 MySQL 业务测试或完整后端发布门禁。

## 失败项及未解决告警

- `npm audit --json` 返回非零：96 项依赖告警（1 中等、92 高、3 严重）。严重项涉及 `proxy-addr` 及其依赖链 `express`、`@dcloudio/vite-plugin-uni`；不能将数量等同于 APK 内 96 个已验证漏洞。尚未逐项评估实际可达性或修复。本次仅新增与现有 uni-app 版本一致的 App 编译依赖，没有执行 `npm audit fix --force`。安装时摘要曾为 32 项，以上采用随后完整审计结果。
- 构建仍有 Vite CommonJS/ESM 配置加载兼容性提示；Android 构建另有上游 `amd` 输出选项告警，构建进程返回成功。原生启动仍待真机确认。
- 开发中两个测试工具问题已修复并复测通过：生产配置测试的文件层级定位错误，以及移除全局 URL 导致 Vitest 自身无法动态导入（现改为独立 VM 验证）。最终上述测试无失败或跳过项。

## 阻塞和未验证

- 没有 HBuilderX、可用 DCloud 登录会话、JDK keytool 或连接的 Android 设备：未生成专用证书，未提交云打包，未生成/签名/安装正式 APK。
- 未验证移动网络上的真实登录、商品图片、扫码、上传、导出、断网重试、账号退出与升级安装。
- 未验证 FRP 内网目标、实际生产代理/media 配置或生产环境是否已设置新 HTTP 例外；没有自动修改服务器。
- 未进行生产业务写入或真实订单操作。公网 GET 405 只证明接口路由可达，不代表登录通过。
- 未运行微信小程序构建，本次目标为 Android；没有调整其 AppID 或 HTTPS 规则。

## 交付文件

- 构建配置：`config/android/.env.production`（本地忽略文件）；可提交模板：`config/android/.env.example`。
- App 资源：`dist/build/app/`；压缩包：`dist/wmsownersale-android-resources-1.0.0.zip`。**资源包不是 APK。**
- 操作说明：[Android 打包与生产接入](android-release.md)。
- 本次会话日志：`/tmp/wmsownersale-android-unit-final.log`、`/tmp/wmsownersale-android-build-final.log`、`/tmp/wmsownersale-android-h5-build.log`、`/tmp/wmsownersale-android-npm-audit.json`。

生产服务尚未部署。需要后端与代理配置生效、正式 APK 签名及真机验收后，才能确认此次 Android 交付完整可用。
