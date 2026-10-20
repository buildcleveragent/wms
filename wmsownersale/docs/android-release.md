# wmsownersale Android 首次发布

当前 Android 生产 API：`http://8.148.198.200:8080`。链路为 Android → 公网机器 8080 → FRP → 仓库 WMS。这里填写业务端口，不是 FRP 控制端口；不要加 `/api`、页面路径或 `#`。

HTTP 是本次选定的临时方案，会明文传输登录令牌和业务数据。HTTPS 支持保留，切换步骤见文末。本文件提供部署步骤，不代表已修改生产服务或已生成正式 APK。

## 1. 构建配置

在 `wmsownersale` 项目目录执行（首次准备才复制，已有配置不要覆盖）：

```bash
cp config/android/.env.example config/android/.env.production
cp config/android/.env.example config/android/.env.development
npm ci
```

两个文件内容为：

```dotenv
VITE_API_BASE_URL=http://8.148.198.200:8080
VITE_ALLOW_HTTP_API=true
VITE_ALLOW_PRIVATE_API=false
```

本次工作区已经建立上述两个本地配置。正式编译读取 `.env.production`，HBuilderX 调试读取 `.env.development`。环境文件被 Git 忽略，可提交的模板为 `config/android/.env.example`。shell 中同名变量优先于文件，应在打包前检查是否有遗留覆盖。

Android 配置目录由 Vite 按实际平台选择；H5、小程序继续读取项目根目录的配置。`WMS_DEV_API_TARGET` 仅是 H5 开发代理，不能改变已安装 App 的后端地址。生产地址被编译进资源，修改后需要重新打包安装。

```bash
npm run test:contracts
npm run test:unit
npm run build:h5
npm run build:android
```

`build:android` 使用与现有 uni-app 完全相同版本的 App 编译依赖，输出 `dist/build/app/`；这是 App 资源，不是 APK。构建会生成并复制 `AndroidManifest.xml` 与 `nativeResources/android/res/xml/network_security_config.xml`。HTTP 正式版仅放行外部主机 `8.148.198.200`，其余主机默认禁止明文。原生白名单不限制端口，业务请求使用 API 配置中的 `8080`。调试版额外允许 localhost/127.0.0.1，供自定义基座 ADB reverse 使用。

商品图片使用接口返回的公网 URL。上传、鉴权图片及导出使用同一个 API 基址；Android 导出使用 HTML5+ IO 校验下载文件，避免把错误 JSON 当作 Excel 打开。保留扫码 Barcode 模块和相机权限，不增加电话或日志权限。

## 2. 生产后端与 FRP

在部署新版后端时，将以下值合并到服务器的环境配置；保留现有其他合法主机、数据库和密钥配置：

```dotenv
APP_ENV=production
DEBUG=False
ALLOWED_HOSTS=8.148.198.200
WMS_ANDROID_HTTP_API_ORIGIN=http://8.148.198.200:8080
CORS_ALLOW_ALL_ORIGINS=False
```

`WMS_ANDROID_HTTP_API_ORIGIN` 默认空值，表示不启用 HTTP 例外。配置后，仅当 Host 和端口匹配且路径以 `/api/` 开头时跳过 HTTPS 重定向。认证、角色、仓库隔离等 API 权限仍执行。它是服务入口级别例外，无法区分请求来自 Android 还是其他 HTTP 客户端。后台管理、Cookie Secure、HSTS 和其他路径的 HTTPS 策略不变。不应设置 HTTP CSRF/CORS 来源或关闭全部 HTTPS 跳转来解决原生 App 请求问题。

确认 FRP 的公网 `8080` 实际转发到提供 WMS 的仓库服务。仓库本地地址尚未提供，不能将示例内网地址直接当成真实配置。推荐转发到仓库 Nginx，由其代理 Django 并提供商品图片；`DEBUG=False` 时 Django 开发路由不会替代生产媒体服务。

在当前纯 HTTP/TCP 转发链路的可信 Nginx 中保留公网 Host/端口，并覆盖客户端传入的转发协议头：

```nginx
# 放入现有 WMS server 块；上游端口和图片目录须采用实际部署值。
location /api/ {
    proxy_set_header Host $http_host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_pass http://127.0.0.1:8000;
}

# 示例目录，替换为实际 MEDIA_ROOT/products/；只公开商品图片。
location /media/products/ {
    alias /srv/wms/media/products/;
    autoindex off;
}
```

不要将私有退货/入库证据目录公开为媒体目录，证据继续经过鉴权 API。图片绝对地址由请求的 Host 和协议生成；出现内网 IP 时修正代理 Host，不在客户端猜测替换。仅在受控反向代理确实覆盖协议头时启用 `TRUST_PROXY_SSL_HEADER=True`；若 FRP 直接 TCP 转发到 Django，则设置 `TRUST_PROXY_SSL_HEADER=False`，避免信任公网客户端伪造的协议头。

本次不自动部署。部署顺序：后端代码与环境配置 → FRP/代理及媒体只读检查 → Android APK。此次没有数据库迁移。

## 3. HBuilderX 云打包与签名

身份保持如下：

| 项目 | 值 |
| --- | --- |
| Android 包名 | `com.jinqiaorongtong.wmsownersale` |
| DCloud AppID | `__UNI__344493F` |
| versionName / versionCode | `1.0.0` / `100` |
| App 显示名 | `wmsowner`（保留原配置） |

使用拥有该 AppID 权限的 DCloud 账号登录 HBuilderX，导入 `wmsownersale` 源码项目，安装与 CLI 编译器匹配的 App 开发插件（本次 CLI 报告 4.81）。不要导入 H5 构建目录作为 Android 项目。

首次生成专用正式签名证书，在具备 JDK `keytool` 的受控打包电脑执行。目录放在仓库外，命令会交互式要求密码和签名主体信息，不在命令或日志中写密码：

如果使用 HBuilderX 的 Android 云端证书，可继续使用该方式，无需执行下面的本地证书生成命令。用户本次云打包已经选择云端证书；首次成功发布后应固定包名和签名证书，后续升级保持一致。

```bash
keytool -genkeypair -v -keystore /安全备份目录/wmsownersale-release.jks -alias wmsownersale-release -keyalg RSA -keysize 2048 -validity 10000
```

妥善备份证书、别名和密码，后续升级必须沿用；不要使用调试证书或重用仓库操作 App 的身份。证书不能提交 Git。

在 HBuilderX「发行 → 原生 App 云打包」中仅选择 Android，核对包名、AppID、版本、正式证书，优先使用安心打包。确认打包资源包含当前网络安全配置。修改原生网络规则后必须重建自定义基座或 APK，热更新 JavaScript 不会改变原生规则。

APK 生成后保存文件 SHA-256、签名证书指纹和构建日期，使用 Android 打包工具核验包名、签名及网络安全资源。当前执行环境没有 HBuilderX、DCloud 登录会话、JDK keytool 或连接的 Android 真机，尚未创建签名证书、提交云打包或生成正式 APK。

## 4. 验收与切换 HTTPS

本地测试与真机验证分开记录。真机安装后关闭仓库 Wi-Fi，使用移动网络检查：登录和刷新令牌、商品图片、数量去零和未计价订单显示、订单只读详情、扫码、上传照片、鉴权照片预览、Excel 导出、断网重试和退出后重新登录。上传和订单写入使用测试环境或指定测试数据，不代替操作员处理真实订单。

公网只读检查可以执行：

```bash
curl --connect-timeout 5 --max-time 10 -o /dev/null -w '%{http_code}\n' http://8.148.198.200:8080/healthz/live
curl --connect-timeout 5 --max-time 10 -o /dev/null -w '%{http_code}\n' http://8.148.198.200:8080/api/token/
```

健康检查预期 200；登录接口 GET 预期 405（登录必须 POST）。这只能证明入口可达，不能证明登录成功或 Android 真机可用。

以后切换 HTTPS：先部署有效证书与可信代理，验证 HTTPS API 和图片；把两个 Android 环境文件的基址改为 HTTPS，并设 `VITE_ALLOW_HTTP_API=false`；重新编译/签名、提高版本号并验证安装。正式版将不再包含外部 HTTP 白名单。旧 HTTP APK 完成更新后，再清空后端 `WMS_ANDROID_HTTP_API_ORIGIN`，关闭临时 HTTP 服务。后端不要在 HTTP 请求上伪造 `X-Forwarded-Proto: https`。

参考：[DCloud 云打包](https://uniapp.dcloud.net.cn/dev/app/cloud-build.html)、[Android 网络安全配置](https://uniapp.dcloud.io/tutorial/app-sec-android)、[HTML5+ 文件 IO](https://www.html5plus.org/doc/zh_cn/io.html)。

## 云打包相机特性重复（2026-10-21 修复）

本次云端失败点是 `:includePermissions:processReleaseManifest`，提示 `android.hardware.camera` 和 `android.hardware.camera.autofocus` 重复。不要把这两个 `<uses-feature>` 再加入 `app-plus.distribute.android.permissions`；该列表只保留权限，避免与云端生成的相机特性重复。

相机硬件可选的声明放在项目根目录 `AndroidManifest.xml` 中，通过 `tools:node="replace"` 合并覆盖。Barcode 模块及 CAMERA 权限保留。重新云打包时同步更新 `manifest.json` 和 `AndroidManifest.xml` 两个文件；如果导入的是编译产物，应使用此次重新生成的完整 `dist/build/app/`，不要复用旧资源。
