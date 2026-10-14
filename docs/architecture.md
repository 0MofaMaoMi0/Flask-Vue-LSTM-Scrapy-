# 系统架构

本目录为源码根，包含三个相互独立的运行单元：Flask 后端、Vue 2 前端和 Scrapy 爬虫。

## 组件与职责

| 组件 | 入口/位置 | 职责 |
| --- | --- | --- |
| Flask | `Flask/app.py` | 注册蓝图，提供用户、电影、订单、支付、文件、短信及单条情感分析接口。 |
| 数据访问 | `Flask/models/`、`Flask/algorithm/` | SQLAlchemy 模型访问用户、电影、评论、订单；ItemCF/UserCF 直接用 PyMySQL 计算推荐。 |
| 情感分析 | `Flask/deeplearning/` | PaddleNLP 模型定义、训练脚本、词表与 `final.pdparams` 推理权重；接口调用 `sentimentalAnalysis_single`。 |
| Vue | `vue/src/` | Vue Router 管理页面，Vuex 管理全局状态；`src/api/request.js` 的 `/api` 基址经开发代理访问后端。 |
| Scrapy | `scrapy/douban/` | spiders 采集电影、评论和人物；pipeline 将 Item 写入 MySQL，并下载封面。 |

## 配置边界

`Flask/config.py` 从 `.env` 加载 MySQL、Flask 和密钥配置；Flask 侧缺失必填项会在导入配置时抛错。Scrapy 的 `douban/database.py` 直接从环境读取同名 MySQL 变量，但未自行加载 `.env`，因此启动爬虫时需要由运行环境提供变量。示例变量仅放在 `.env.example`。

## 接口边界

`app.py` 为 `/user`、`/movie`、`/order`、`/alipay` 和 `/test` 注册蓝图。文件上传、OCR、短信和 `/deeplearning/senti_single` 在应用文件中直接声明。前端新增接口时，应同步核验 Flask 路由、Vue `src/api/` 调用与页面消费字段。
