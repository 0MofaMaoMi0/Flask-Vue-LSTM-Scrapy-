# 项目源码文档导航

详细开发记录、交接、运维与验收文档统一保存在仓库外的 `项目工作日志/`；本文件仅保留源码仓库内的常用入口与结构导航。

## 现行文档入口

| 用途 | 位置 |
|---|---|
| 仓库总览、脚本与边界说明 | [README.md](../README.md) |
| 一键启动说明 | [启动说明.md](../启动说明.md) |
| 当前项目总览（权威现状） | [`项目工作日志/00-索引/当前项目总览.md`](../../项目工作日志/00-索引/当前项目总览.md) |
| 项目工作日志索引 | [`项目工作日志/00-索引/README.md`](../../项目工作日志/00-索引/README.md) |
| 统一数据采集与清洗使用手册 | [`项目工作日志/06-开发与验证/统一采集清洗组件-20261119/`](../../项目工作日志/06-开发与验证/统一采集清洗组件-20261119/) |
| 阿里云 ECS 部署与运维记录 | [`项目工作日志/05-环境与运维/阿里云ECS部署记录-20261122.md`](../../项目工作日志/05-环境与运维/阿里云ECS部署记录-20261122.md) |

## 目录结构

```text
项目源码/
├─ backend/       模块化 Flask 正式后端（含 catalog/ingest_hub/）
├─ movie-web/     Vue 3 + Vite 正式前端（5174）
├─ scrapy/        豆瓣采集辅助（支持 ArchiveEnvelopePipeline 离线归档）
├─ scripts/       统一启动器、隧道维护、统一采集清洗 CLI（data_collector.py）与 Windows 入口
├─ docs/          本导航
└─ data/          数据与素材
```

## 常用入口与运行链路

- **一键启动（`3306` 数据库隧道 + 本机 Flask `8091` + Vite `5174` + 浏览器）**：双击或执行根目录 `启动项目.cmd`（支持 `--check` / `--health` / `--no-browser`）
- **一键关闭本机全套服务**：双击或执行根目录 `停止项目.cmd`（支持 `--check` / `--dry-run`，严格校验进程归属，不误杀其他进程）
- **仅前端 / 仅后端检查**：

```bat
scripts\windows\start-frontend.cmd
scripts\windows\start-frontend.cmd --check
scripts\windows\check-backend.cmd
```

- **统一采集与清洗入库 CLI**：`.local-cache\auth-venv\Scripts\python.exe scripts\data_collector.py --help`
- **当前本地运行链路**：本机前端 `127.0.0.1:5174` → Windows 本地 Flask `127.0.0.1:8091` → SSH 数据库隧道 `127.0.0.1:3306` → 隔离 Ubuntu MySQL `127.0.0.1:3306`