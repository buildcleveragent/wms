# Android 发布检查

## 1. Windows 本地环境

在 HBuilderX 中打开的项目根目录创建 `.env`（不是 `.env.txt`）：

```env
VITE_API_BASE_URL=http://后端电脑局域网IP:8001
```

PowerShell 可以使用：

```powershell
cd 'D:\$shared\winwms\wmsandroid'
Set-Content -Path '.env' -Value 'VITE_API_BASE_URL=http://192.168.1.100:8001'
Get-Content '.env'
```

将示例 IP 替换为后端电脑的实际局域网 IP。`.env` 已被忽略，不得提交。

## 2. App 身份与签名

1. 在 DCloud 开发者账号中为本项目创建全新 AppID，在 HBuilderX `manifest.json` 可视化界面中绑定；不得复用 `wmspda` 的 AppID。
2. 保持包名 `com.jinqiaorongtong.wmsandroid`、版本 `1.0.0/100` 和 App Barcode 模块。
3. 使用全新的 Android keystore。签名文件、别名、口令、APK/AAB 均存放在仓库外。

## 3. 自定义调试基座

1. 先确认手机与后端电脑连接同一 Wi-Fi，后端监听 `0.0.0.0:8001`，Windows 防火墙允许该端口。
2. 用手机浏览器访问 `http://局域网IP:8001/api/auth/profile/`；收到 401/未登录响应即证明网络可达。
3. 在 HBuilderX 制作 Android 自定义调试基座。本项目含自定义 Manifest 和 Android 资源，不使用标准基座。
4. 安装生成的 `unpackage/debug/android_debug.apk`，后续在 HBuilderX 设备选择窗口中选择该自定义基座。

所有构建都会为设备自身的 `127.0.0.1` 和 `localhost` 保留明文访问例外，供 HBuilderX 的 ADB 反向代理连接调试服务。该例外不允许访问任何局域网或公网 HTTP 主机；外部 HTTP 仍只允许 `VITE_API_BASE_URL` 配置的唯一主机。网络安全配置属于原生资源，修改后必须重新制作并安装自定义调试基座，仅重新运行前端资源不会生效。

## 4. 内部测试 APK

真机调试通过后，使用 HBuilderX“发行 → App 云打包”生成 APK，并在打包界面选择仓库外的独立签名。

Android App 构建会从 `VITE_API_BASE_URL` 解析唯一主机：HTTP 只为该主机生成明文例外，HTTPS 不生成例外。主机变更后必须重新制作基座/APK。

## 5. 真机验收

验证相机首次授权、拒绝后打开设置、闪光灯、远距离一维码、二维码、DataMatrix，仓库 Wi-Fi 断开/恢复，以及 POS 未知结果幂等重试。
