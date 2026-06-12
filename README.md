# NAS 发票工具

适合部署在群晖 NAS Container Manager 上的开票系统。支持多用户、客户编号、项目维护、发票工作流、附件、ZIP 导出、Gmail SMTP、核销和统计图表。

## 启动

1. 修改 `docker-compose.yml`：
   - `SECRET_KEY`
   - `ADMIN_EMAIL`
   - `ADMIN_PASSWORD`

   邮件 SMTP 设置进入系统后在“公司设置”里维护。
2. 在群晖 Container Manager 里用 Project 导入本目录，或 SSH 运行：

```bash
docker compose up -d --build
```

3. 打开：

```text
http://你的NAS地址:8088
```

## 功能

- 客户编号从 `00001` 自动递增，支持客户查找。
- 经理和管理员维护项目；开票时只能选择已启用项目，并自动带出税率。
- 外部用户可自行注册，但必须由管理员绑定客户后才能创建和查询发票。
- 员工或外部员工创建发票并上传多个附件，提交经理审核。
- 经理通过消息进入发票详情审核，可退回或确认完成。
- 发票完成后可记录收款日期、金额和备注进行核销。
- 概览统计开票、完成、已核销、待核销、流程中，并显示月度开票和核销趋势。
- 附件支持 Word、Excel、PDF 和图片，可多选上传、预览、删除、下载和随 ZIP 导出。
- 邮件发送的 SMTP host、端口、账号、应用专用密码、发件人和 TLS 设置在“公司设置”中维护。
- 导出 ZIP 会包含发票 PDF 和该发票的全部附件。

## 数据

数据库：

```text
./data/invoices.db
```

附件：

```text
./data/attachments
```

本次代码不考虑历史数据；要彻底重建，删除 NAS 项目目录里的 `data` 文件夹后重新 Build。

## Google 工单地图

系统默认继续使用免费的 OpenStreetMap、U.S. Census Geocoder 和 Nominatim。

如需启用 Google 地图和更准确的 Google 地址解析：

1. 在 Google Cloud 启用 `Maps JavaScript API` 和 `Geocoding API`。
2. 创建一个浏览器 API Key，并按实际访问地址限制允许的网站来源。
3. 在 NAS 项目目录创建 `.env`：

```text
GOOGLE_MAPS_BROWSER_API_KEY=你的浏览器API密钥
```

4. 在 Google Cloud 控制台设置每日请求配额和费用提醒。
5. 重新创建容器：

```bash
sudo docker compose up -d --build --force-recreate
```

配置密钥后，工单地图会自动切换为 Google 地图，并在浏览器中实时解析地址。Google 坐标不会写入数据库。未配置密钥时，系统自动保留免费模式。
