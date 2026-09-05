# Prasinos Power 业务管理平台

Prasinos Power 内部使用的 Flask 业务平台，整合客户、合同、工单、现场工作日报、员工报销、客户结算、发票、工资核算、人员资料和现场照片管理。

生产站点：`https://invoice.prasinospower.com`

## 主要功能

- 客户、站点、合同、项目和工单管理，支持工单日历、地图、状态和人员分配。
- 现场工作日报、客户费用结算、发票审核、附件、邮件发送、收款核销和 Excel/ZIP 导出。
- 员工报销支持代填，分别保存费用归属员工和实际提交人。
- 薪酬按员工等级计算标准、交通、加班和假期工资，并拆分自驾、随行、租车驾驶、餐费和报告补贴；按时长计算的自驾及随行车补统一使用交通时薪。
- 现场工作 PWA 使用员工账号登录，支持固定工单、拍照前定位、断网草稿、恢复上传、照片压缩和按工单/日期归档。
- 设备照片支持 Machine Number OCR，以及人工确认的位置号、集装箱号和水泵保险编号；水印显示站点、Technician、设备信息、时间、地址与坐标。
- 照片台账保留工单、施工员、实际拍摄账号、拍摄时间、水印时间、上传时间、位置和文件路径；设备维修清单按设备批次汇总并支持 Excel 导出。
- 删除无业务单据关联的工单时检查并二次确认照片目录，确认后同时清理照片；当月新工单优先补用空缺编号。
- 员工注册要求地址和手机号，并保存中文、英语或西班牙语交流偏好；短信验证需要另行配置服务商。
- 员工资料附件和证书查询；更完整的证书分类、人员评价、人员报表和短信功能正在逐步加入。
- 角色、菜单和操作权限覆盖管理员、经理、财务、内部员工、外部经理和外部员工。

## 生产部署

生产环境运行在 PVE 虚拟机中的 Debian：

```text
应用代码       /opt/invoice-tool
SQLite及附件   /srv/invoice-tool/data
工单照片       /srv/invoice-tool/shared-photos
部署备份       /srv/invoice-tool/backups
容器端口       127.0.0.1:8088 → container:8000
公网入口       Cloudflare Tunnel → invoice.prasinospower.com
```

Docker Compose 运行三个服务：

- `invoice-tool`：Flask/Gunicorn Web 应用。
- `invoice-tool-photo-worker`：照片整理、缩略图和重复文件处理。
- `invoice-tool-cloudflared`：HTTPS 公网隧道，不直接暴露 Debian 的应用端口。

`invoice-tool-deploy.timer` 定期检查 GitHub `main` 分支。检测到新提交后，部署脚本会：

1. 在线备份 SQLite 数据库。
2. 将代码更新到指定 Git 提交。
3. 重新构建并启动容器。
4. 执行 HTTP 健康检查。
5. 健康检查失败时回滚代码并重新构建上一版本。

Debian 使用本仓库专用的 GitHub Deploy Key 拉取代码，不需要保存 GitHub 用户密码。

## 配置与启动

复制环境变量示例并填写密钥：

```bash
cp .env.example .env
docker compose up -d --build
```

主要环境变量：

```text
SECRET_KEY=
ADMIN_EMAIL=
ADMIN_PASSWORD=
CLOUDFLARE_TUNNEL_TOKEN=
DATA_HOST_DIR=/srv/invoice-tool/data
SHARED_PHOTOS_HOST_DIR=/srv/invoice-tool/shared-photos
GOOGLE_MAPS_BROWSER_API_KEY=
GOOGLE_GEOCODING_API_KEY=
```

SMTP、公司资料、水印时间调整密码和业务参数在系统设置页面维护。生产密钥只保存在 Debian 的 `.env`，不提交到 GitHub。

## 数据与照片

SQLite、业务附件和照片都保存在 Debian 的持久化目录中。删除或重新构建容器不会删除 `/srv/invoice-tool` 中的数据。

现场照片保存结构：

```text
/srv/invoice-tool/shared-photos/
  工单编号/
    pictures/
      YYYY-MM-DD/
        YYYYMMDD_HHMMSS-u员工ID-照片UUID.jpg
```

照片先在手机 IndexedDB 中保存为待上传草稿，点击完成后上传。锁屏、关闭页面或断网时上传可能暂停，重新打开 PWA 并联网后可以继续。

## 地图

未配置 Google Maps 密钥时，系统使用 OpenStreetMap、U.S. Census Geocoder 和 Nominatim。配置浏览器及 Geocoding API Key 后，工单地图切换到 Google 地图。

## 验证

```bash
python -m unittest discover -s . -p "test_*.py" -v
```

测试使用隔离数据库和临时文件目录，覆盖权限、审批、工单、报销、结算、工资、照片上传、离线元数据、归档和自动部署安全检查。PWA 相机、定位和主屏幕安装仍需在 iPhone/Android 真机上验证。

现场工作实现细节见 [`FIELD_WORK.md`](FIELD_WORK.md)。
