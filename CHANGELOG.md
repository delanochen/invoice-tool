# 更新日志 / Changelog

本文件记录每次版本发布的用户可见变更与重要内部修改，供人与后续自动化流程（发布、审计、AI 助手）了解各版本改动内容，避免重复排查已完成的工作。版本号与根目录 `VERSION` 文件保持一致，发布时以 `release: vX.Y.Z` 提交更新。

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [0.1.226] - 2026-09-17

### 修复（安全）
- 发票导出页与发票详情页的客户/公司地址字段不再使用 `|safe` 直接输出未转义内容，改为新增的 `nl2br` 模板过滤器（先 HTML 转义、再保留换行），消除内部用户可注入的存储型 XSS（`templates/invoice_export.html`、`templates/invoice_detail.html`、`app.py`）。

### 修复（用户体验）
- 新增 403 友好错误页：外部账号访问无权限的工单/工作日报时，不再显示 Flask 默认英文 Forbidden 页，改为与站点风格一致的中文提示（外部账号提示仅能查看所属客户的工单并联系管理员开通权限）。`app.py` 新增 `@app.errorhandler(403)`，`templates/error.html` 的页面标题改为跟随传入标题。

## 更早版本

0.1.225 及之前的变更未记录在本文件中，可通过 `git log --oneline` 查看历史提交（功能提交 + 对应的 `release:` 提交）。
