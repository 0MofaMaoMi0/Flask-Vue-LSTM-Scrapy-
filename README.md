# MovieInsight 2.0

当前正式应用是 `backend/`（模块化 Flask）与 `movie-web/`（Vue 3）；`scrapy/` 是唯一豆瓣采集辅助模块。旧 Flask/Vue2 业务代码已按授权清理，剩余模型、词表、日志、用户配置等保护物统一放在 `legacy/`，不再作为运行入口。

## 标准目录

```text
项目源码/
├─ backend/                 Flask 正式应用
├─ movie-web/               Vue 3 正式前端（Vite，5174）
├─ scrapy/                  豆瓣采集辅助（保留限速/robots）
├─ scripts/                 项目级运行工具
│  ├─ launch_frontend.py    canonical 前端启动器
│  ├─ windows/
│  │  ├─ start-frontend.cmd 正常前端入口
│  │  ├─ check-backend.cmd  后端只读健康检查
│  │  └─ project.ps1        统一参数适配（BOM，兼容 PS5）
│  └─ tests/                离线启动器自测
├─ docs/                    简洁文档导航（详细日志仍在仓库外）
├─ data/                    数据/素材（不随代码重构移动）
├─ legacy/                  历史保留资产
│  ├─ Flask/                旧模型/词表/日志/IDE/上传/静态等保护物
│  └─ vue/                  旧 Vue2 保留配置/IDE（含敏感 .npmrc，不读取）
├─ project_config.py        scrapy 共享配置入口，保留
├─ .env.example             配置变量样例（不含真实秘密）
└─ .local-cache/ .venv*/    现有环境与缓存，原位忽略
```

根目录不再放 `run_backend.bat`/`run_frontend.bat`；旧文件已同卷移入 `scripts/windows/` 并改名为实际职责名称。旧的 `Flask/`、`vue/` 目录不再位于仓库根；请从 `legacy/` 查看保护物，不要复制回运行路径。

## Windows 正常使用

从本仓库根目录执行：

```bat
scripts\windows\start-frontend.cmd          :: 启动或复用 5174 前端
scripts\windows\check-backend.cmd           :: 只读 GET 后端健康
scripts\windows\start-frontend.cmd --check  :: 离线自检，不启动、不发 HTTP
scripts\windows\check-backend.cmd --help    :: 查看后端检查入口
```

> 入口分工：`启动项目.cmd` 负责一键拉起/检查受控 SSH 隧道、启动前端并打开浏览器（详见 `启动说明.md`）；`scripts/windows/start-frontend.cmd` 仅负责前端启动/复用与健康检查，不拉起隧道。

也可以从项目根目录使用既有 `start-latest.ps1`（工作区外兼容入口，转发到仓库内统一入口）：

```powershell
.\start-latest.ps1 -CheckOnly
.\start-latest.ps1 -HealthOnly
.\start-latest.ps1
```

当前链路：本机前端 **5174** → 既有受控 SSH 隧道 **3629** → 隔离 Ubuntu 应用本机 **5000**。`8091` 是旧静态缺省，不是当前后端；只读验收端口 1461/3575 不得作为正常入口。Ubuntu/隧道不可用时，启动器只报告失败，不自行启动或重启后端/隧道/数据库，不安装依赖。

## 脚本与配置边界

- `scripts/launch_frontend.py` 只读取隧道 JSON 的 `local_port` 与 `remote_target`；默认文件为 `../运行配置/Ubuntu/http-tunnel.json`，可用 `--tunnel-json` 覆盖。
- 运行日志、TEMP、STATE 与 Node 编译缓存统一写入仓库 `.local-cache/startup/` 的明确子目录，不写旧报告目录，也不与运行中的 Vite 缓存混用。
- `start-frontend.cmd` 默认启动/复用前端；`--check` 只做离线参数核对；`--dry-run` 只打印启动计划。`check-backend.cmd` 默认只读健康，`--check` 做离线核对。
- 不执行 `npm install`，不新增 Python 环境，不使用 `ExecutionPolicy Bypass`；前端复用 `F:/Node/node.exe` 与既有 `movie-web/node_modules`，Python 复用 `.local-cache/auth-venv`。
- 真实 `.env`、`.npmrc`、Cookie、私钥、数据库和原始日志不加入提交；只维护 `.env.example` 的变量名和安全占位值。历史已跟踪模型/词表只复用既有Git blob记录归档路径，不重新导入内容。`scrapy/` 继续保留限速与 robots 约束。

## 文档与状态

- [当前项目总览](../项目工作日志/00-索引/当前项目总览.md)
- [项目工作日志索引](../项目工作日志/00-索引/README.md)
- [M10 冻结交付](../项目工作日志/06-开发与验证/M10-20261115/final-delivery/)
- [旧源码清理执行核验](../项目工作日志/05-环境与运维/旧源码清理-20261115/最终清理核验.json)
- [本次结构整理结果](../项目工作日志/05-环境与运维/项目结构整理-20261115/最终结构核验.json)
- [legacy/README.md](legacy/README.md)：历史保留物说明
- `docs/README.md`：当前文档导航与历史文档去向

M10 交付仍为 `PENDING_OWNER`；M9 仍为 `LIMITED_PASS_DESKTOP_RETAINED`。本 README 不把历史限定验收写成生产验收，也不改变凭据事件和 6 次未归因 HTTP201 的开放状态。