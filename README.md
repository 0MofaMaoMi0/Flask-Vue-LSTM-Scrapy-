# MovieInsight 2.0

**MovieInsight 2.0** 是一个面向电影目录治理、观影行为分析、可解释个性化推荐与可控 AI 探索的全栈系统，采用 **模块化 Flask（Python）+ Vue 3 / TypeScript（Vite）** 前后端分离架构，配套 **Scrapy 豆瓣辅助采集** 与 **`ingest_hub` 统一数据采集清洗管线**。

---

## 一、项目介绍

### 1. 核心功能模块

| 模块 | 页面 / 路径 | 说明 |
| --- | --- | --- |
| **欢迎与身份认证** | `/`、`/login`、`/register` | 基于 Session + CSRF + Argon2 密码哈希的安全登录注册、登录限流提示与账号注销保护 |
| **影院首页与电影库** | `/home`、`/movies` | 热门/推荐榜单展示、电影检索筛选、影片详情（TMDB 热度、制作国家、演职员阵容与海报兜底） |
| **我的电影与全球制片分布** | `/movies#behavior` | 观看记录、收藏、想看、评分等行为管理；基于 `globe.gl` 的 3D 全球电影制片国家交互分布与排行分析 |
| **推荐频道与反馈评估** | `/recommendations` | 以用户评分、收藏、想看、已看、不感兴趣等真实行为为核心的可解释本地推荐排序，含曝光/点击反馈记录 |
| **AI 探索与悬浮助手** | `/ai`、悬浮助手 | **AI-FIRST + 用户级 BYOK**：用户在设置页绑定个人模型 Key 与 TMDB Token（服务端按账户加密持久化），支持受控工具调用与无 Key 本地兜底 |
| **个人设置与数据洞察** | `/settings`、`/insights` | 个人模型/TMDB 凭据托管、账号安全设置；基于 `ECharts` 的个人观影趋势与平台调用账本可视化 |
| **管理员数据驾驶舱** | `/admin` | 仅管理员可见：全站指标概览、国家对比/下钻、影片资产治理、TMDB 增量补采分析、推荐效果与操作审计面板 |
| **统一采集与清洗管线** | `scripts/data_collector.py` | 支持 `tmdb_supplement`、`tmdb_trending`、`tmdb_catalog`、`douban_archive`、`local_envelope` 五类数据源的 `select → capture → verify → commit` 幂等流水线 |

### 2. 技术栈

- **后端（`backend/`）**：Python 3.11+、Flask 3.1.2、SQLAlchemy 2.0、Alembic 1.17、PyMySQL、Argon2-cffi、Cryptography、Pytest
- **前端（`movie-web/`）**：Vue 3.5、TypeScript 5.9、Vite 7、Pinia 3、Vue Router 4.5、Vuetify 3.9、ECharts 6.1、Globe.gl 2.46
- **数据采集与清洗（`scrapy/` + `backend/catalog/ingest_hub/`）**：Scrapy（限速/robots 合规、支持 `ArchiveEnvelopePipeline` 离线归档）+ 统一校验/封条入库内核
- **数据库（MySQL 8.0+）**：
  - 主库（目录 + 认证）：`movies`、`movie_external_ids`、`movie_source_records`、`movie_ratings_summary`、`genres`、`people`、`movie_credits`、`movie_assets`、`users`、`user_sessions` 等核心表
  - 行为库（`MI2_M6_ACTIVITY_SCHEMA`）：行为事件、用户状态、推荐曝光/点击回执等
  - 助手辅助库（`MI2_M7_DATABASE_URL`）：按账号加密存储 BYOK 模型凭据与工具审计记录

### 3. 目录结构

```text
.
├─ backend/                         # 模块化 Flask 正式后端
│  ├─ app/                          # 应用工厂、认证、M6 隔离连接与本地服务入口 (m6_serve.py)
│  ├─ catalog/                      # 电影目录、推荐排序、TMDB 补采与 ingest_hub 统一采集内核
│  ├─ assistant/                    # AI 探索引擎、BYOK 加密存储与可控工具集
│  ├─ analytics/                    # 数据洞察与管理员统计指标服务
│  ├─ migrations/                   # Alembic 数据库迁移脚本（0001_auth / 0002_m6_catalog / 0003_m6_activity）
│  ├─ m6_checks/ m8_checks/         # 后端离线单测与回归测试集
│  ├─ recommendation_eval/          # 推荐算法离线评估框架与测试
│  ├─ ops/                          # 隔离环境运维与调度工具
│  ├─ admin_api.py / media_api.py   # 驾驶舱接口与白名单图片代理缓存
│  ├─ run.py / manage.py            # 本地开发启动入口
│  └─ requirements.txt              # 后端 Python 依赖
├─ movie-web/                       # Vue 3 + Vite 正式前端
│  ├─ src/
│  │  ├─ api/                       # 认证与基础接口封装
│  │  ├─ components/                # 通用组件（AppSidebar、MovieGlobe、AssistantChat、admin/ 等）
│  │  ├─ services/                  # 业务状态、AI 探索、推荐反馈、TMDB 与个人数据服务
│  │  ├─ stores/                    # Pinia 全局状态（auth）
│  │  ├─ views/                     # 全部页面视图（Home / Library / MovieDetail / MyMovies / Recommendation / AIExplore / Insights / Settings / Admin）
│  │  └─ lib/globe/                 # 3D 地球渲染引擎与国家 ISO 映射
│  ├─ public/                       # 本地电影海报兜底素材与地球纹理/国旗矢量资源
│  ├─ tools/                        # 前端自动化校验与 E2E 测试脚本
│  ├─ package.json                  # 前端依赖与构建脚本
│  └─ vite.config.ts                # Vite 构建、分包与 /api/* 代理配置
├─ scrapy/                          # 豆瓣辅助采集模块（保留限速与 robots.txt 约束）
├─ scripts/                         # 项目级 CLI 与启动工具
│  ├─ data_collector.py             # 统一数据采集与清洗入库 CLI
│  ├─ tmdb_supplement.py            # TMDB 补采兼容 CLI
│  ├─ launch_project.py             # 全链路一键启动器（隧道 + 前端 + 浏览器）
│  ├─ launch_frontend.py            # 前端启动与端口健康检查器
│  ├─ tunnel_supervisor.py          # 受控 SSH 隧道维护脚本
│  ├─ windows/                      # Windows 入口（start-frontend.cmd / check-backend.cmd / project.ps1）
│  └─ tests/                        # 启动器离线自测
├─ docs/                            # 仓库内文档导航
├─ data/                            # 本地素材占位目录
├─ legacy/                          # 历史旧版资产保留目录（不作为运行入口）
├─ project_config.py                # 共享配置加载与检查脚本
├─ .env.example                     # 根环境变量模板
├─ 启动项目.cmd                      # Windows 一键启动脚本（配合既有远程 SSH 隧道配置）
└─ 启动说明.md                       # 一键入口使用说明
```

### 4. 运行链路

```text
浏览器 (http://127.0.0.1:5174)
   │
   ▼  Vite Dev Server (/api/v1, /api/catalog, /api/admin, /api/media 代理)
MI2_API_TARGET (默认 http://127.0.0.1:8091；SSH 隧道模式为 http://127.0.0.1:3629)
   │
   ▼
Flask 后端 (本机 127.0.0.1:8091 或 隔离服务 127.0.0.1:5000) ──▶ MySQL 8.0
```

---

## 二、新设备下载与运行指南

以下说明适用于从 GitHub 克隆代码到**一台全新设备**（Windows 或 Linux）并完整运行项目。

### 1. 环境前置准备

- **Git**
- **Python 3.11+**（建议 3.11，Windows 请勾选添加到 `PATH`）
- **Node.js 20+**（建议 LTS 版本，需与 Vite 7 兼容，包含 `npm`）
- **MySQL 8.0+**（若在本地运行后端）

### 2. 克隆代码与安装依赖

#### （1）克隆仓库

```bash
git clone https://github.com/0MofaMaoMi0/Flask-Vue-LSTM-Scrapy-.git
cd Flask-Vue-LSTM-Scrapy-
```

#### （2）安装后端 Python 依赖

建议使用虚拟环境（保持与仓库脚本兼容路径 `.local-cache/auth-venv`）：

**Windows (PowerShell)：**

```powershell
python -m venv .local-cache\auth-venv
.\.local-cache\auth-venv\Scripts\python.exe -m pip install -U pip
.\.local-cache\auth-venv\Scripts\python.exe -m pip install -r backend\requirements.txt
```

**Linux / macOS：**

```bash
python3 -m venv .local-cache/auth-venv
.local-cache/auth-venv/bin/python -m pip install -U pip
.local-cache/auth-venv/bin/python -m pip install -r backend/requirements.txt
```

> 可选：如需运行 `scrapy/` 采集模块，可另建独立环境 `.venv-scrapy` 并安装 `scrapy/requirements.txt` 与 `scrapy/requirements-images.txt`。

#### （3）安装前端依赖

```bash
cd movie-web
npm install
cd ..
```

---

### 3. 配置与启动（两种模式）

#### 模式 A：新设备本机独立运行（全新机器本地开发 / 演示）

本模式在当前机器的本地 MySQL 8.0 上直接运行前后端，不依赖外部 Ubuntu 虚拟机。

##### 选项 A1：完整全功能模式（推荐，包含电影库、行为、推荐、AI/BYOK、驾驶舱）

> **命名与连接契约**：完整隔离模式在代码（`backend/app/m6_database.py`、`backend/assistant/store.py`）中有严格校验，请按如下约定准备：
> - 主库（目录 + 认证）名称须匹配 `^mi2_m6_[a-z0-9_]{8,48}$`（如 `mi2_m6_local_dev`）；
> - 行为库可使用独立库（如 `mi2_m6_local_act`）；
> - 助手辅助库（M7）名称须匹配 `^mi2_m7_[a-z0-9_]{1,40}$`（如 `mi2_m7_local_aux`）；
> - 主库账号（App / Migration / Audit）须匹配 `^mi2[a-z0-9_]{1,29}$`（如 `mi2devapp`、`mi2devmigrate`、`mi2devaudit`），M7 账号须匹配 `^mi2m7[a-z0-9_]{1,24}$`（如 `mi2m7app`）；
> - 所有数据库连接串必须显式写为 `mysql+pymysql://用户:密码@127.0.0.1:3306/库名`（不可用 `localhost`，不可附带 `?charset=...` 等 query 参数）。

1. **创建本地 MySQL 库与账号（在本地 MySQL 8.0 执行）**

   ```sql
   CREATE DATABASE mi2_m6_local_dev CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
   CREATE DATABASE mi2_m6_local_act CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
   CREATE DATABASE mi2_m7_local_aux CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;

   CREATE USER 'mi2devapp'@'127.0.0.1' IDENTIFIED BY '<App密码>';
   CREATE USER 'mi2devmigrate'@'127.0.0.1' IDENTIFIED BY '<Migrate密码>';
   CREATE USER 'mi2devaudit'@'127.0.0.1' IDENTIFIED BY '<Audit密码>';
   CREATE USER 'mi2m7app'@'127.0.0.1' IDENTIFIED BY '<M7密码>';

   GRANT SELECT, INSERT, UPDATE, DELETE ON mi2_m6_local_dev.* TO 'mi2devapp'@'127.0.0.1';
   GRANT SELECT, INSERT, UPDATE, DELETE ON mi2_m6_local_act.* TO 'mi2devapp'@'127.0.0.1';
   GRANT ALL PRIVILEGES ON mi2_m6_local_dev.* TO 'mi2devmigrate'@'127.0.0.1';
   GRANT ALL PRIVILEGES ON mi2_m6_local_act.* TO 'mi2devmigrate'@'127.0.0.1';
   GRANT SELECT, INSERT ON mi2_m6_local_act.* TO 'mi2devaudit'@'127.0.0.1';
   GRANT SELECT, INSERT, UPDATE, DELETE, CREATE ON mi2_m7_local_aux.* TO 'mi2m7app'@'127.0.0.1';
   FLUSH PRIVILEGES;

   -- 记录本机实例 UUID，供下面环境变量 MI2_M6_SERVER_UUID 使用
   SELECT @@server_uuid;
   ```

2. **生成 M7 凭据加密主密钥（32 字节二进制文件）与本地目录**

   完整模式启动时会对设置页中的个人 API Key / TMDB Token 做 AES-GCM 加密托管（`MI2_CREDENTIALS_MASTER_FILE` 为必需项，切勿提交到 Git）：

   ```powershell
   # Windows PowerShell 示例（在仓库根目录执行，路径请勿放在 C 盘受保护目录）
   New-Item -ItemType Directory -Force .local-cache\local-runtime | Out-Null
   [IO.File]::WriteAllBytes("$PWD\.local-cache\local-runtime\m7-master.key", [Security.Cryptography.RandomNumberGenerator]::GetBytes(32))
   ```

   ```bash
   # Linux / macOS 示例
   mkdir -p .local-cache/local-runtime
   python3 -c 'import os, pathlib; p = pathlib.Path(".local-cache/local-runtime/m7-master.key"); p.write_bytes(os.urandom(32)); os.chmod(p, 0o600)'
   ```

3. **设置环境变量（当前终端注入，切勿把真实密码提交到仓库）**

   参考 [backend/.env.example](backend/.env.example)、[backend/.env.m6.example](backend/.env.m6.example) 与 [backend/.env.m7.example](backend/.env.m7.example)：

   ```powershell
   # Windows PowerShell 示例（请替换 <> 中的实际值）
   $env:MI2_M6_ISOLATED = "true"
   $env:MI2_M6_SCHEMA = "mi2_m6_local_dev"
   $env:MI2_M6_ACTIVITY_SCHEMA = "mi2_m6_local_act"
   $env:MI2_M6_APP_USER = "mi2devapp"
   $env:MI2_M6_MIGRATION_USER = "mi2devmigrate"
   $env:MI2_M6_AUDIT_USER = "mi2devaudit"
   $env:MI2_M6_SERVER_UUID = "<本机_MYSQL_SERVER_UUID>"
   $env:MI2_DATABASE_URL = "mysql+pymysql://mi2devapp:<App密码>@127.0.0.1:3306/mi2_m6_local_dev"
   $env:MI2_MIGRATION_DATABASE_URL = "mysql+pymysql://mi2devmigrate:<Migrate密码>@127.0.0.1:3306/mi2_m6_local_dev"
   $env:MI2_AUDIT_DATABASE_URL = "mysql+pymysql://mi2devaudit:<Audit密码>@127.0.0.1:3306/mi2_m6_local_dev"
   $env:MI2_M7_DATABASE_URL = "mysql+pymysql://mi2m7app:<M7密码>@127.0.0.1:3306/mi2_m7_local_aux"
   $env:MI2_CREDENTIALS_MASTER_FILE = "$PWD\.local-cache\local-runtime\m7-master.key"
   $env:MI2_M6_DDL_LEDGER = "$PWD\.local-cache\local-runtime\m6-ddl-ledger.jsonl"
   $env:MI2_MEDIA_CACHE = "$PWD\.local-cache\local-runtime\media"
   $env:MI2_TRENDING_CACHE = "$PWD\.local-cache\local-runtime\trending"
   $env:MI2_ALLOWED_ORIGINS = "http://127.0.0.1:5174"
   $env:MI2_COOKIE_SECURE = "false"
   $env:MI2_ADMIN_USERS = "admin"
   ```

   > 注意：`MI2_M6_DDL_LEDGER` 必须指向**绝对路径且尚未存在的文件**（父目录需已创建），Alembic 迁移时会自动创建该文件及同目录 `0003_m6_activity.jsonl` 作为 DDL 审计记录。

4. **初始化数据库表结构（主库/行为库 Alembic 迁移 + M7 辅助库建表）**

   ```powershell
   cd backend
   # 1) 执行 0001_auth -> 0002_catalog -> 0003_m6_activity 完整迁移
   ..\.local-cache\auth-venv\Scripts\python.exe -m alembic upgrade head

   # 2) 初始化 M7 辅助库表（sessions/requests/budgets/cache/usage/provider_credentials 及个人档案/笔记/反馈表）
   ..\.local-cache\auth-venv\Scripts\python.exe -c "import os; from sqlalchemy import create_engine; from assistant.store import tables; from assistant.credential_store import credential_table; from assistant.personal_features import tables as p_tables; e = create_engine(os.environ['MI2_M7_DATABASE_URL']); tables().create_all(e); credential_table().create(e, checkfirst=True); [t.create(e, checkfirst=True) for t in p_tables().values()]"
   cd ..
   ```

   > 初始化完成后，建议收窄 `mi2m7app` 权限，仅保留 `SELECT, INSERT, UPDATE, DELETE`（应用运行期禁止自动执行 DDL）。

5. **启动后端与前端**

   - **终端 1 — 启动完整隔离后端**（监听 `127.0.0.1:5000`）：
     ```powershell
     cd backend
     ..\.local-cache\auth-venv\Scripts\python.exe -m app.m6_serve
     ```
   - **终端 2 — 启动前端开发服务器**（监听 `http://127.0.0.1:5174`）：
     ```powershell
     cd movie-web
     $env:MI2_API_TARGET = "http://127.0.0.1:5000"
     npm run dev
     ```

   打开浏览器访问 `http://127.0.0.1:5174/`：
   - 注册账号即可使用（若用户名在 `MI2_ADMIN_USERS` 列表中，侧边栏会显示「管理员驾驶舱」入口 `/admin`）；
   - 新库初始无影片数据时，可使用第 3 节的 `scripts/data_collector.py` 导入 TMDB 影片数据，或在设置页 `/settings` 配置个人 DeepSeek API Key 与 TMDB Token。

---

##### 选项 A2：轻量基础认证开发模式（仅调试登录/注册/会话接口）

若只需调试基础身份认证接口（不挂载电影目录、推荐、驾驶舱与 AI 路由）：

```powershell
# 1) 本地 MySQL 创建 mi2_auth_dev 库并授权后，仅需设置基础连接串
$env:MI2_DATABASE_URL = "mysql+pymysql://<用户>:<密码>@127.0.0.1:3306/mi2_auth_dev"
$env:MI2_ALLOWED_ORIGINS = "http://127.0.0.1:5174"

# 2) 仅执行 0001_auth（不要跑 upgrade head，0002_catalog 仅在 MI2_M6_ISOLATED=true 时运行）
cd backend
..\.local-cache\auth-venv\Scripts\python.exe -m alembic upgrade 0001_auth
..\.local-cache\auth-venv\Scripts\python.exe run.py   # 默认监听 127.0.0.1:8091
```

另开终端启动前端（默认 `MI2_API_TARGET` 即指向 `http://127.0.0.1:8091`）：

```powershell
cd movie-web
npm run dev
```

---

#### 模式 B：通过受控 SSH 隧道连接既有远程 Ubuntu 后端

适用于团队成员在本地仅跑前端、后端由隔离 Ubuntu 虚拟机托管的场景（本地无需安装或运行 MySQL）：

1. 在仓库父级目录（与本仓库同级）准备好私有运行时配置：
   - `../运行配置/Ubuntu/http-tunnel.json`（包含 `local_port`、`remote_target`、`ssh_host`、`ssh_user`、`host_key_alias`）
   - `../运行配置/Ubuntu/private/` 中的 SSH 私钥与 `known_hosts`
2. 确认 Ubuntu 已启动且其内部服务监听 `127.0.0.1:5000`。
3. 在仓库根目录执行：

```bat
启动项目.cmd              :: 自动检查/建立 3629 SSH 隧道、启动 5174 前端并打开浏览器
启动项目.cmd --check      :: 仅做离线启动条件自检
启动项目.cmd --health     :: 仅检查远端健康状态（/api/v1/health 与 /api/v1/ready）
启动项目.cmd --no-browser :: 启动但不自动弹出浏览器
```

或仅单独管理前端 / 后端状态检查（不自动拉起隧道）：

```bat
scripts\windows\start-frontend.cmd
scripts\windows\start-frontend.cmd --check
scripts\windows\check-backend.cmd
```

---

### 4. 新设备常见启动排查

| 现象 | 原因与处理 |
| --- | --- |
| `RuntimeError: Explicit M6 schema required` / `M6 connection contract mismatch` | 检查 `MI2_M6_SCHEMA` 是否匹配 `mi2_m6_*`、账号名是否以 `mi2` 开头、连接串主机是否为 `127.0.0.1:3306` 且无 query 参数 |
| `RuntimeError: Explicit M6 migration environment required` | 未开启 `MI2_M6_ISOLATED=true` 时不要运行 `alembic upgrade head`，基础认证模式请运行 `alembic upgrade 0001_auth` |
| `RuntimeError: New absolute DDL ledger required` | `MI2_M6_DDL_LEDGER` 必须设为绝对路径且文件尚不存在；重跑前请删除或更换新文件名 |
| `CredentialError: Persistent credentials require the protected installer` | 完整模式下未设置 `MI2_CREDENTIALS_MASTER_FILE` 或文件非 32 字节；按模式 A 第 2 步生成密钥文件即可 |
| 前端 `/api/...` 返回 `502 / ECONNREFUSED` | 确认 `MI2_API_TARGET` 与后端实际监听端口一致（本机隔离模式 `5000`、基础模式 `8091`、SSH 隧道模式 `3629`） |

---

## 三、数据采集与日常验证

### 1. 统一数据采集与清洗 CLI（`scripts/data_collector.py`）

用于从 TMDB 或豆瓣离线归档采集、校验并事务入库（采集 TMDB 时需设置环境变量 `TMDB_READ_TOKEN`，或通过 `--token-env` 指定其他变量名）：

```powershell
# 查看命令帮助
.\.local-cache\auth-venv\Scripts\python.exe scripts\data_collector.py --help

# 标准四段式流程（示例：TMDB 目录选标 → 抓取 → 校验 → 入库）
$env:TMDB_READ_TOKEN = "<你的_TMDB_Read_Access_Token>"
.\.local-cache\auth-venv\Scripts\python.exe scripts\data_collector.py select --source tmdb_catalog --job-id job-01 --summary-only
.\.local-cache\auth-venv\Scripts\python.exe scripts\data_collector.py capture --plan <plan.json> --archive <archive_dir> --execute --summary-only
.\.local-cache\auth-venv\Scripts\python.exe scripts\data_collector.py verify --archive <archive_dir>\job-01 --strict --summary-only
.\.local-cache\auth-venv\Scripts\python.exe scripts\data_collector.py commit --archive <archive_dir>\job-01 --dsn-env MI2_DATABASE_URL --seal <commit_seal> --execute --summary-only
```

### 2. 离线回归与质量检查命令

```powershell
# 1) 后端全套离线单元测试（无需连接真实 MySQL）
cd backend
..\.local-cache\auth-venv\Scripts\python.exe -m pytest m6_checks\ m8_checks\ recommendation_eval\tests\ -q
cd ..

# 2) 前端 TypeScript 类型检查与生产构建
cd movie-web
npm run build
cd ..

# 3) 启动器与隧道配置离线自检
.\.local-cache\auth-venv\Scripts\python.exe scripts\tests\selftest_launcher.py

# 4) 配置变量检查（仅列变量名与来源，不打印密钥值）
.\.local-cache\auth-venv\Scripts\python.exe project_config.py --check
```

---

## 四、安全与工程约定

- **零敏感信息入库**：真实 `.env`、`敏感信息调用/`、SSH 私钥、Cookie、TMDB Token、用户 BYOK API Key 及数据库文件均被 `.gitignore` 严格排除，严禁提交到仓库。
- **爬虫合规默认**：`scrapy/` 默认遵守目标站点 `robots.txt` 与限速重试策略，不关闭合规约束。
- **扩展文档入口**：更多仓库内导航见 [docs/README.md](docs/README.md) 与 [启动说明.md](启动说明.md)。