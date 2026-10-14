# 数据流

## 采集与持久化

1. `scrapy/douban/spiders/` 产出 `Subject`、`MovieMeta`、`Comment` 或 `PersonMeta` 等 Item。
2. `CoverPipeline` 下载电影封面到 `IMAGES_STORE`（默认 `scrapy/storage/`）；`DoubanPipeline` 根据 Item 类型查询并插入/更新 MySQL 的 `subjects`、`movies`、`comments`、`person` 等表。
3. Flask 的 `models/` 读取应用侧电影、评论、用户和订单表；协同过滤模块另以 PyMySQL 连接并读取数据库。

## 分析、推荐与展示

1. `movieApi.py` 查询电影和评论数据，构造词云、年份、类型、国家、评分和时间轴所需的统计结果。
2. `ItemCF.py` 与 `UserCF.py` 提供电影推荐；路由为 `/movie/getRecomendation` 和 `/movie/getRecomendation2`（现有拼写如此，改名会影响前端兼容性）。
3. Vue 页面通过 `src/api/` 发起请求，路由把可视化页面分到 `Dash1` 至 `Dash5`，并将数据交给 ECharts 组件渲染。

## 情感分析

1. `deeplearning/train.py` 使用 PaddleNLP 的 `chnsenticorp` 数据集建立词表、训练可选网络并输出训练产物；它不是爬虫评论的自动训练流程。
2. `predict_lstm.py` 使用仓库内的 `vocab.json` 与 `checkpoints/final.pdparams` 构建双向 LSTM，在 CPU 上处理输入文本。
3. Flask 的 `POST /deeplearning/senti_single` 接收 JSON 中的 `data`，调用单条推理并返回标签与概率。

训练、全量爬取和批量写库都具有较高成本或副作用，默认不在日常验证中执行。
