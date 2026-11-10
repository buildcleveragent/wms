# wmsbossbilling Android 内部测试包

本项目的 Android 安装身份固定为：

| 项目 | 值 |
| --- | --- |
| 包名 | `uni.app.UNIWMSJYFX` |
| DCloud AppID | `__UNI__4BA38C0` |
| 显示名称 | 仓储经营分析中心 |
| 签名 | 原 DCloud 账号中该 AppID 使用的云端证书 |
| API | `http://8.148.198.200:8080`（临时） |

`npm run build:app` 只生成 `dist/build/app/` 原生资源，不生成 APK。APK 必须通过
HBuilderX 云打包，并继续选择旧版本使用的云端证书。

## 1. 读取旧包身份

本次以用户指定的 `unpackage/release/apk/bv2new2.apk` 为升级基线，已从 APK 原生清单
确认包名为 `uni.app.UNIWMSJYFX`、版本为 `1.0.0/100`。本次升级版本为 `1.0.1/101`，
继续使用原云端证书。其他归档 APK 使用过不同包名，不能与本包互相覆盖安装。

覆盖升级前必须从安装旧版的手机或旧 APK 读取版本与证书，不允许使用仓库默认的
`1.0.0/100` 猜测。连接手机并安装 Android SDK Platform Tools 后执行：

```bash
adb shell dumpsys package uni.app.UNIWMSJYFX | grep -E 'versionName|versionCode'
adb shell pm path uni.app.UNIWMSJYFX
adb pull /data/app/实际返回路径/base.apk old-wmsbossbilling.apk
apksigner verify --print-certs old-wmsbossbilling.apk
```

Windows PowerShell 中可使用：

```powershell
adb shell dumpsys package uni.app.UNIWMSJYFX |
  Select-String 'versionName|versionCode'
adb shell pm path uni.app.UNIWMSJYFX
```

也可以直接对保存的旧 APK 执行：

```bash
apkanalyzer manifest application-id old-wmsbossbilling.apk
apkanalyzer manifest version-name old-wmsbossbilling.apk
apkanalyzer manifest version-code old-wmsbossbilling.apk
apksigner verify --print-certs old-wmsbossbilling.apk
```

包名必须与上表一致。记录旧版 `versionName`、`versionCode` 和证书 SHA-256 指纹；
不得提交旧 APK、证书或口令。

## 2. 准备升级版本

复制本地升级资料模板：

```bash
cp config/android/release-identity.example.json config/android/release-identity.local.json
```

在本地文件中填写实测值。该文件已被 Git 忽略。执行：

```bash
npm run prepare:android-release
npm run verify:android-release
git diff -- manifest.json
```

准备脚本固定包名与 AppID，将 `versionCode` 设置为旧值加一，并把三段式
`versionName` 的补丁号加一。旧版本号必须是从 APK 读取的正整数，证书指纹必须是完整的
SHA-256。检查无误后运行：

```bash
npm run test:unit
npm run build:h5
npm run build:android-release
```

Android 生产配置来自 `config/android/.env.production`；模板只允许
`http://8.148.198.200:8080`。网络安全 XML 默认禁止明文，仅为该主机建立临时例外。

## 3. HBuilderX 打包

1. 使用拥有 `__UNI__4BA38C0` 的原 DCloud 账号登录 HBuilderX，并打开本项目源码。
2. 先制作自定义调试基座，确认原生清单、图标和 HTTP 网络规则已进入基座。
3. 在“发行 → 原生 App 云打包”中只选择 Android，核对包名、AppID 和新版本号。
4. 继续选择旧 APK 使用的云端证书；不得新建证书或改用调试证书。
5. 下载 APK 后核验身份：

```bash
apkanalyzer manifest application-id new-wmsbossbilling.apk
apkanalyzer manifest version-name new-wmsbossbilling.apk
apkanalyzer manifest version-code new-wmsbossbilling.apk
apksigner verify --print-certs new-wmsbossbilling.apk
sha256sum new-wmsbossbilling.apk
```

新旧证书 SHA-256 指纹必须完全相同，新 `versionCode` 必须恰好为旧值加一。记录
APK SHA-256、签名指纹、版本号和构建日期，记录中不得包含账号口令。

## 4. 真机验收

保留旧版应用及其数据，直接安装新 APK，确认系统执行覆盖升级。分别在 Wi-Fi 和
移动网络验证：启动、登录、刷新令牌、退出、后台恢复、断网重连、全部 Boss 页面、
仓库/货主/账期/日期筛选，以及 Excel 下载、文件写入和 `uni.openDocument`。

生产后端应保留精确配置：

```dotenv
DEBUG=False
ALLOWED_HOSTS=8.148.198.200
WMS_ANDROID_HTTP_API_ORIGIN=http://8.148.198.200:8080
CORS_ALLOW_ALL_ORIGINS=False
```

只合并该主机到已有合法 `ALLOWED_HOSTS`，不要覆盖其他生产主机。HTTP 例外只应用于
匹配主机的 `/api/`，不能关闭全局 HTTPS 跳转或放宽 CORS。切换 HTTPS 时移除 HTTP
例外、重新提高版本号并重新打包。
