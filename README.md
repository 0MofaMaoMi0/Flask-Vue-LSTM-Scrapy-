# MovieInsight 2.0

当前正式系统由模块化 Flask 后端、Vue 3 前端和保留的豆瓣采集辅助模块组成。旧 `Flask/` 业务代码与旧 `vue/` Vue 2 前端已退役；`scrapy/` 仍是唯一豆瓣采集工具，按辅助能力保留。旧 LSTM 情感分析、支付宝支付、OCR 认证等不再作为当前正式能力宣传。

## 当前入口

| 用途 | 入口 |
|---|---|
| 登录 | http://127.0.0.1:5174/login ，使用已有项目账号，不是数据库 root |
| 启动或复用前端 | 根目录 `start-latest.ps1`；已有依赖，不安装、不重建 |
| 静态自检 | `..\start-latest.ps1 -CheckOnly` |
| 后端只读健康 | `..\start-latest.ps1 -HealthOnly` |
| 正式后端 | `backend/` |
| 正式前端 | `movie-web/` |
| 豆瓣采集辅助 | `scrapy/`，保留限速、robots 和独立 `.venv-scrapy` |
| 当前交付/版本 | `../项目工作日志/06-开发与验证/M10-20261115/final-delivery/` |
| 项目总入口 | 根目录 `项目入口.md` |

## 运行链路

正常链路是：本机前端 **5174** → 既有受控 SSH 隧道 **3629** → 隔离 Ubuntu 应用本机 **5000**。`8091` 只是源码里的旧静态缺省，不是当前运行后端。不要直接启动本机旧 Flask、连接旧 MySQL80，也不要用 8091 代理替代当前隧道。

`run_backend.bat` 只做后端健康核对，不启动本机旧/新数据库或服务；`run_frontend.bat` 复用根启动器，不执行 `npm install`。二者都不绕过 PowerShell 执行策略。

## 数据、模型与秘密

- `Flask/deeplearning/checkpoints/`、`Flask/deeplearning/vocab.json` 是旧 LSTM 的模型/词表数据，保留原位；删除范围只限旧业务源码。
- `scrapy/` 的采集、限速、robots 与独立环境保留，不重写采集系统。
- `data/`、`_design_refs/`、`movie-web/catalog-dist/`、`movie-web/real-catalog-dist/`、`movie-web/real-catalog.html`、`movie-web/real-catalog.vite.config.ts` 为用户数据/设计资料，保持原样。
- 真实 `.env`、`敏感信息调用/*.env`、Cookie、私钥和数据库/原始日志不进入源码、文档或归档；不要打印或提交其内容。

## 旧代码清理与回退

清理前检查点：`b8bc17135aeadcb9e342629f980e51c667062ec8`（parent 为 M10 的 `0ebd247...`），回退引用：`refs/mi2/cleanup/pre-legacy-20261115`。精确清单、依赖证据和入口替换稿在：

```text
项目工作日志\05-环境与运维\旧源码清理-20261115\
```

本轮已核对检查点并由首脑执行精确回收清理；实际结果见工作区外的清理执行记录。需要恢复旧文件时，从上述检查点取回；数据库/模型/备份不能只靠 Git 恢复。

## 状态边界

M10 交付仍为 `PENDING_OWNER`；M9 仍为 `LIMITED_PASS_DESKTOP_RETAINED`。本 README 不把历史限定验收写成生产验收，也不改变凭据事件和 6 次未归因 HTTP201 的开放状态。