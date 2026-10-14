---
name: flask-api-development
description: 修改本项目 Flask 后端或其 Vue API 交互时使用；覆盖蓝图、统一响应、SQLAlchemy、配置与最小回归验证，不用于数据库迁移或模型训练。
---

# Flask API 开发

本项目的应用入口是 `Flask/app.py`；它注册 `/user`、`/movie`、`/order`、`/alipay` 和 `/test` 蓝图，另定义文件、短信和情感分析路由。

1. 先读取目标路由、相关 `models/` 或 `algorithm/`、前端 `vue/src/api/` 和页面调用点。
2. 沿用 `ResMsg` 响应封装、已有 Blueprint 命名和 SQLAlchemy 使用方式。若更改请求或响应字段，更新实际前端调用；不要仅改一端。
3. 数据库配置来自 `.env`，由 `Flask/config.py` 加载。不得读取或输出其值，不得在代码中回填凭据。
4. 对变动运行最小的 Python 语法/导入检查；需要服务验证时优先使用不写库的 `/test`，且只有在本地配置与依赖已就绪时执行。

迁移、批量写入、支付、短信、OCR 和生产配置都需要用户的明确授权后才可执行。
