# Prasinos Power iOS（内部版）

这是现有 Flask 系统的原生 iOS 客户端外壳。业务、账号和权限继续由 NAS 上的系统统一管理；App 提供独立入口、持久登录、拍照/文件上传、下载分享、原生返回/首页/刷新和服务器切换。

## 构建环境

- macOS 14 或更新版本
- Xcode 15 或更新版本
- XcodeGen（`brew install xcodegen`）
- iOS 16 或更新版本

## 第一次生成并安装

1. 将整个 `ios-app` 文件夹复制或 clone 到 Mac。
2. 在终端进入该文件夹，运行 `xcodegen generate`。
3. 打开 `PrasinosPower.xcodeproj`。
4. 选择 `PrasinosPower` target > Signing & Capabilities，选择你或公司的 Apple Developer Team。
5. 如签名提示 Bundle ID 已占用，把 `com.prasinospower.internal` 改成唯一值。
6. 用 USB 连接 iPhone，选择该设备，点击 Run。首次安装后可能需要在 iPhone 的“设置 > 通用 > VPN 与设备管理”信任开发者。

首次启动输入团队系统地址。外网使用时应填写 Cloudflare Tunnel 对应的 HTTPS 域名；局域网可填写 `http://NAS-IP:8088`。

App 登录后默认进入“团队打卡”：选择站点、取得 GPS、调整水印时间并拍照，照片加水印后会自动上传 NAS 的 `clock-ins/日期/站点编号` 目录。

## 团队分发（不进 App Store）

- 少量设备：每台 iPhone 连接 Xcode 安装，免费 Apple ID 签名通常需周期性重新安装。
- 普通 Apple Developer Program：使用 Ad Hoc 签名 IPA，需提前登记每台团队 iPhone 的 UDID；用户安装 IPA 后即可使用，不上架 App Store。
- Apple Developer Enterprise Program：公司符合 Apple 企业计划资格时，可给内部 IPA 做企业签名。员工从公司内部下载地址安装后，在“设置 > 通用 > VPN 与设备管理”中信任企业开发者；无需逐台登记 UDID，也无需上架 App Store。

“手机信任证书”指的是企业签名安装方式。普通个人/公司开发者账号不能仅靠手动信任绕过设备登记，仍需要有效的签名证书和 provisioning profile。

## 把下载入口部署到网页登录页

1. 在 Mac 上用企业签名或 Ad Hoc 签名导出 `PrasinosPower.ipa`。
2. 将文件上传到 NAS 项目的持久化数据目录：`data/mobile-app/PrasinosPower.ipa`。当前 Docker 映射下，对应容器内 `/app/data/mobile-app/PrasinosPower.ipa`。
3. 确保员工用 HTTPS 域名访问系统。iOS OTA 安装不能使用普通 HTTP NAS 地址。
4. 登录首页的“下载 iPhone 内部版 App”会自动变成可安装按钮，并使用系统生成的 OTA manifest 安装。

如修改 Bundle ID 或版本号，请在容器环境变量中同步设置 `IOS_BUNDLE_ID` 和 `IOS_APP_VERSION`。

## 安全建议

- 外网访问只使用 HTTPS，不要直接暴露 NAS 的 8088 端口。
- 上线前务必更换 `docker-compose.yml` 里的默认 `SECRET_KEY` 和管理员密码，并把密钥放在 `.env`。
- 若服务器域名或地址变更，点击 App 底部齿轮即可修改。
