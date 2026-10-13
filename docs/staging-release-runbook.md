# 测试与生产发布流程

## 环境边界

| 环境 | 分支 | 域名 | 数据库 | 宿主机目录 | 本机端口 |
| --- | --- | --- | --- | --- | --- |
| 测试 | `staging` | `test.invoice.prasinospower.com` | `invoice_test` | `/opt/invoice-tool-test`、`/srv/invoice-tool-test` | `127.0.0.1:8089` |
| 生产 | `main` | `invoice.prasinospower.com` | `invoice_production` | `/opt/invoice-tool`、`/srv/invoice-tool` | `8088` |

测试 Compose 只启动 Web 应用，不启动邮件、照片或 AI 后台任务。测试环境必须使用
独立密钥、独立附件目录，并通过 `DEPLOYMENT_ENV=test` 显示红色测试标识。

## 日常发布

1. 开发完成后，把待验收提交推送到 `staging`。
2. `invoice-tool-test-deploy.timer` 自动部署并只允许连接 `invoice_test`。
3. 在测试域名执行自动测试和人工验收。失败时继续修复 `staging`，生产不变。
4. 验收通过后，把完全相同的提交快进提升到 `main`。
5. 人工启动 `invoice-tool-deploy.service` 发布生产；生产定时器保持禁用。

任何没有明确目标环境的“提交、推送、部署”指令，都必须先确认是测试还是生产。

## Cloudflare

在 Cloudflare Tunnel 中增加公开主机名 `test.invoice.prasinospower.com`，上游指向服务器
上的测试服务。若 Tunnel 运行在 Docker 网络内，目标使用
`http://invoice-tool-test:8000`，并将 Tunnel 容器接入 `invoice-tool-test_default`；不要
复制或提交 Tunnel token。

## 测试数据

测试库只能使用批准的脱敏数据或测试种子。不得把生产客户、员工、地址、邮箱、银行、
税务、附件或密钥原样写入 `invoice_test`。业务金额、工时、里程、状态和关联结构可以在
脱敏后保留，以便验证计算。
