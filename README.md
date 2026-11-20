# MovieInsight 2.0

模块化 Flask + Vue 3 电影数据与智能推荐系统：正式后端位于 `backend/`，正式前端位于 `movie-web/`，`scrapy/` 负责豆瓣采集辅助；历史保护物归档于 `legacy/`，不作为运行入口。

## 目录结构

```text
项目源码/
├─ backend/                 Flask 正式应用（含 catalog/ingest_hub/ 统一采集清洗入库内核）
├─ movie-web/               Vue 3 + Vite 正式前端（5174）
├─ scrapy/                  豆瓣采集辅助（保留限速/robots，支持 ArchiveEnvelopePipeline 离线归档）
├─ scripts/                 项目级运行与运维工具
│  ├─ data_collector.py     统一数据采集与清洗入库 CLI（select / capture / verify / commit / convert-douban）
│  ├─ tmdb_supplement.py    TMDB 补采兼容 CLI
│  ├─ launch_project.py     全链路一键启动器（自动拉起/检查受控 SSH 隧道 + 前端 + 浏览器）
│  ├─ launch_frontend.py    前端启动与健康检查器
│  ├─ tunnel_supervisor.py  受控 SSH 隧道维护脚本
│  ├─ windows/              Windows 入口脚本（start-frontend.cmd / check-backend.cmd / project.ps1）
│  └─ tests/                启动器离线自测
├─ docs/                    文档导航（详细记录在仓库外 ../项目工作日志/）
├─ data/                    数据与素材
├─ legacy/                  历史保护物（模型/词表/静态资源，仅供追溯）
├─ project_config.py        scrapy 共享配置入口
├─ .env.example             配置变量样例（不含真实秘密）
├─ 启动项目.cmd             Windows 双击一键启动入口
└─ 启动说明.md              双击入口详细说明
```

## 日常启动与检查

- **一键启动（推荐）**：双击根目录 `启动项目.cmd`（Ubuntu 已开机时自动建立/复用 SSH 隧道并启动 5174 前端与浏览器）：

```bat
启动项目.cmd --check
启动项目.cmd --health
启动项目.cmd --no-browser
```

- **仅前端 / 后端检查**（仓库根目录执行）：

```bat
scripts\windows\start-frontend.cmd          :: 启动或复用 5174 前端（不自动拉起隧道）
scripts\windows\start-frontend.cmd --check  :: 离线参数核对
scripts\windows\check-backend.cmd           :: 只读 GET 后端健康检查
```

- **工作区外兼容入口**（`../start-latest.ps1`）：

```powershell
.\start-latest.ps1 -CheckOnly
.\start-latest.ps1 -HealthOnly
.\start-latest.ps1
```

当前运行链路：`127.0.0.1:5174`（本机前端） → `127.0.0.1:3629`（受控 SSH 隧道） → `127.0.0.1:5000`（隔离 Ubuntu 后端）。Ubuntu 或隧道未就绪时，前端检查器仅报告失败，不自行重启虚拟机或数据库。

## 脚本与配置边界

- **隧道与运行配置**：`scripts/launch_frontend.py` 与 `tunnel_supervisor.py` 读取 `../运行配置/Ubuntu/http-tunnel.json`（可用 `--tunnel-json` 覆盖），私钥与主机指纹保存在 `../运行配置/Ubuntu/private/`（不进入 Git）。
- **本地环境与缓存**：前端复用 `F:/Node/node.exe`（或 `MI2_NODE_EXE`）与既有 `movie-web/node_modules`，Python 复用 `.local-cache/auth-venv`，启动日志与临时缓存统一落在 `.local-cache/startup/`。
- **数据与密钥保护**：`.env`、`.npmrc`、Cookie、私钥、数据库及原始日志不入库、不提交；`scrapy/` 保留限速与 `robots` 约束；采集与补数优先使用 `scripts/data_collector.py`。

## 文档导航

- [项目源码文档导航](docs/README.md)
- [启动说明](启动说明.md)
- [当前项目总览](../项目工作日志/00-索引/当前项目总览.md)
- [项目工作日志索引](../项目工作日志/00-索引/README.md)
- [统一采集清洗使用手册](../项目工作日志/06-开发与验证/统一采集清洗组件-20261119/)
- [legacy 保护物说明](legacy/README.md)