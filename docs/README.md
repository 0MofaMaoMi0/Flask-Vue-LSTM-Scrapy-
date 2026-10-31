# 文档已迁出源码目录

本项目的说明、交接、计划、巡检与总结文档**不再放在源码仓库内**，已统一迁至：

```text
F:\Python-project\Flask-Vue-LSTM-scrapy\项目工作日志\
```

入口文件（先看这个）：

```text
F:\Python-project\Flask-Vue-LSTM-scrapy\项目工作日志\00-索引\README.md
```

## 原文件去向

| 原路径（本目录下） | 新位置（`项目工作日志` 下） |
| --- | --- |
| `docs\handoff.md` | `01-交接与现状\handoff.md` |
| `docs\project-framework.md` | `02-架构与设计\project-framework.md` |
| `docs\scrapy-development-plan.md` | `03-计划与验收\scrapy-development-plan.md` |
| `docs\reports\2026-10-16-scrapy-completion-assessment.md` | `04-巡检与评估报告\2026-10-16-scrapy-completion-assessment.md` |
| `docs\architecture.md` | `90-历史与过时\architecture.md` |
| `docs\data-flow.md` | `90-历史与过时\data-flow.md` |
| `docs\troubleshooting.md` | `90-历史与过时\troubleshooting.md` |

迁移日期：2026-10-31，同盘移动，内容未改动。

## 现行文档（按权威性）

1. `项目工作日志\02-架构与设计\project-framework.md` —— 架构与数据流权威说明
2. `项目工作日志\01-交接与现状\交接-2026-10-31.md` —— 当前状态交接（实测）
3. `项目工作日志\03-计划与验收\scrapy-development-plan.md` —— 阶段 0–6 与验收标准
4. `项目工作日志\04-巡检与评估报告\2026-10-31-项目盘点与后续准备.md` —— 最新盘点

## 如需恢复原位置

```powershell
cd 'F:\Python-project\Flask-Vue-LSTM-scrapy\项目源码'
git checkout -- docs/architecture.md docs/data-flow.md docs/troubleshooting.md
```

其余未纳入 Git 的文档，按上表从 `项目工作日志` 手工移回即可。