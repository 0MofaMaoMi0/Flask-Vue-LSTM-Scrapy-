# 基于 Python 的深度学习豆瓣电影数据可视化 + 情感分析推荐系统

> 技术栈：Flask + Vue + LSTM(PaddleNLP) + Scrapy
> 一个「爬虫采集 → 数据存储 → 深度学习情感分析 → 协同过滤推荐 → 可视化大屏」的完整闭环系统。

---

## 一、功能概览

| 模块 | 功能 |
| --- | --- |
| 数据采集 | 在遵守 robots.txt 和限速配置下采集电影详情、公开短评与影人信息，支持定向 ID 和运行上限 |
| 情感分析 | 基于 PaddleNLP 的 Bi-LSTM 中文情感二分类（positive / negative），批量给影评打标签 |
| 智能推荐 | ItemCF（基于物品）与 UserCF（基于用户）两种协同过滤推荐算法 |
| 数据可视化 | 词云、年代产量、类型排行、国家分布、评分散点、时间轴、世界地图等 5 套大屏 |
| 用户体系 | 注册 / 登录 / 实名认证（OCR）/ 修改密码 / 修改资料 / 余额充值（支付宝） |

---

## 二、目录结构

```
YuanDaiMa/
├── Flask/                      # 后端服务（Flask）
│   ├── app.py                  # 应用入口（默认 0.0.0.0:8080）
│   ├── api/                    # 接口层
│   │   ├── movieApi.py         # 电影/可视化/推荐接口（核心，15 个接口）
│   │   ├── userApi.py          # 用户接口
│   │   ├── orderApi.py         # 订单接口
│   │   ├── alipayApi.py        # 支付宝支付与回调
│   │   ├── baiduApi.py         # 身份证 OCR
│   │   ├── fileApi.py / testApi.py
│   ├── models/                 # ORM 模型（SQLAlchemy）
│   │   ├── model.py            # tb_user
│   │   ├── movie.py            # movies2
│   │   ├── comments.py         # comments2
│   │   └── order.py            # tb_order
│   ├── algorithm/              # 推荐算法
│   │   ├── ItemCF.py           # 基于物品的协同过滤
│   │   └── UserCF.py           # 基于用户的协同过滤
│   ├── deeplearning/           # 情感分析（PaddleNLP）
│   │   ├── model.py            # LSTM 网络定义
│   │   ├── train.py            # 训练脚本
│   │   ├── predict_lstm.py     # 推理（对外提供 sentimentalAnalysis_single）
│   │   ├── utils.py            # 数据预处理
│   │   ├── vocab.json          # 词表
│   │   └── checkpoints/        # 模型权重（final.pdparams）
│   ├── base/                   # 统一响应封装 / 全局 db 对象
│   ├── utils/                  # 短信、工具函数
│   └── requirements.txt
├── scrapy/                     # 爬虫（Scrapy）
│   ├── douban/
│   │   ├── spiders/            # movie_subject / movie_meta / movie_comment / person_meta
│   │   ├── pipelines.py        # 数据清洗与入库
│   │   ├── middlewares.py      # 历史中间件实现（默认不启用）
│   │   ├── proxylib.py         # 历史代理辅助代码（默认不启用）
│   │   ├── database.py         # MySQL 连接
│   │   └── settings.py
│   ├── sql/douban.sql          # 建表脚本
│   └── requirements.txt
├── vue/                        # 前端（Vue2 + Vuetify + ECharts）
│   ├── src/views/Charts/       # Dash1~Dash5 可视化大屏
│   ├── src/views/MachineLearning/  # 情感分析交互页
│   ├── src/views/Pages/        # 账号设置相关页面
│   └── package.json
└── 数据库脚本/                  # MySQL 数据导出（*.sql）
```

---

## 三、环境要求

| 组件 | 版本建议 |
| --- | --- |
| Python | 3.11（项目 `.venv` 已使用 Python 3.11.5 验证） |
| MySQL | 5.7 或 8.0 |
| Node.js | 16+（已在 Node.js 24.21.0 验证构建与开发服务） |
| JDK | 不需要（仅 PyCharm 运行 Python 时无关） |

---

## 四、快速开始

### 1. 导入数据库

数据库脚本体积较大（约 86 MB），**不要用客户端一次性粘贴执行**，否则会因超过服务端
`max_allowed_packet`（默认 64 MB）报 `2006 - Server has gone away`。请用命令行导入：

```bat
mysql -uroot -p --default-character-set=utf8mb4 --max_allowed_packet=1G < "数据库脚本\flask_douban_comment.sql"
```

导入后应包含 11 张表：`comments`、`comments2`、`movies`、`movies2`、`subjects`、
`person`、`person_obj`、`proxys`、`tb_user`、`tb_order`、`tb_rate`。

### 2. 配置数据库连接

`.env.example` 是预留的配置模板；可复制为 `.env` 并填写实际账号密码：

```
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=你的密码
MYSQL_DATABASE=flask_douban_comment
```

> 后端与爬虫均通过项目根目录的 `project_config.py` 加载 `.env`；进程环境变量优先于
> 文件中的同名配置。

### 3. 启动后端

```powershell
# 在项目根目录执行；首次安装时使用官方 PyPI 与 PaddlePaddle CPU 源
.\.venv\Scripts\python.exe -m pip install -r Flask\requirements.txt -i https://pypi.org/simple --extra-index-url https://www.paddlepaddle.org.cn/packages/stable/cpu/
.\.venv\Scripts\python.exe -m pip install --no-deps python-alipay-sdk==3.0.4

# 启动前确保 MySQL 服务已运行，然后任选一种方式启动后端
.\run_backend.bat
# 或：cd Flask; ..\.venv\Scripts\python.exe app.py --device cpu --batch_size 32 --network lstm
```

`run_backend.bat` 固定使用项目 `.venv`。服务默认监听 `http://127.0.0.1:8080`，可用
`http://127.0.0.1:8080/test` 验证是否启动成功。

### 4. 启动前端

```bat
cd vue
npm install
npm run serve
```

浏览器访问终端提示的地址（默认 `http://localhost:8999`；后端使用 8080，若需调整可在
`vue.config.js` 中调整）。

### 5. 运行爬虫（独立环境）

爬虫的依赖版本与后端冲突，必须使用单独环境：

```powershell
# 在项目根目录执行；首次运行时创建并安装
.\.venv\Scripts\python.exe -m venv .venv-scrapy
.\.venv-scrapy\Scripts\python.exe -m pip install -r scrapy\requirements.txt --index-url https://pypi.org/simple
.\.venv-scrapy\Scripts\python.exe -m pip install -r scrapy\requirements-images.txt --index-url https://pypi.org/simple

# 进入爬虫目录（需要读取任务时确保 MySQL 已启动）
cd scrapy
# 默认从豆瓣电影 explore 页的动态接口收集 1 页电影 ID
..\.venv-scrapy\Scripts\python.exe -m scrapy crawl movie_subject
# 电影详情默认最多处理 100 条
..\.venv-scrapy\Scripts\python.exe -m scrapy crawl movie_meta -a limit=100
# 将电影演职员 ID 回填到 person_obj
..\.venv-scrapy\Scripts\python.exe -m douban.rebuild_pid
```

评论、人物采集及 `movies → movies2` 的只读预览/显式同步命令见
[`scrapy/README.md`](scrapy/README.md)。爬虫仅在 `.env` 配置 Cookie 时注入会话，
代理和随机 UA 默认关闭，不会自动进行全量抓取。

海报默认作为 WebP 持久化到项目根目录 `data/covers/`，MySQL 仅保存
原始 URL、相对路径、哈希和状态。Flask 通过 `/media/covers/` 提供带长缓存的
图片响应，Vue 通过 `/api/media/covers/` 同源代理访问。图片下载遵守来源站点
的 `robots.txt`；被禁止的来源只记录失败状态并保留原始 URL，不自动绕过。
需要批量补齐时，可通过数据库中的 IMDb ID 使用可选的 TMDB 适配器，或者导入
按豆瓣 ID 命名的已授权本地图片；配置和命令见 `scrapy/README.md`。

---

## 五、配置说明

后端和爬虫的主数据库连接由项目根目录 `.env` 统一提供：

| 文件 | 行 | 内容 |
| --- | --- | --- |
| `Flask/config.py` | — | 加载 `.env` 并构造 Flask/SQLAlchemy 配置 |
| `Flask/algorithm/ItemCF.py` | — | 使用 `Config` 创建 MySQL 连接 |
| `Flask/algorithm/UserCF.py` | — | 使用 `Config` 创建 MySQL 连接 |
| `Flask/docomment.py` | — | 使用 `Config.SQLALCHEMY_DATABASE_URI` |
| `scrapy/douban/database.py` | — | 使用同名环境变量，默认目标库为 `flask_douban_comment` |
| `Flask/test/test*.py` | — | 历史练习脚本，仍可能含旧连接串 |

> `.env` 已被 `.gitignore` 排除，不应提交。第三方服务密钥仍需按 `.env.example` 补齐。

---

## 六、主要接口一览

前缀 `/movie`（`Flask/api/movieApi.py`）：

| 接口 | 说明 |
| --- | --- |
| `GET /movie/getWordCut` | 词云数据（jieba 分词） |
| `GET /movie/get` | 关键词搜索电影 |
| `GET /movie/getHot` | 高分热门电影 |
| `GET /movie/getChart1` `getChart2` `getChart3` `getAreaChart` | 年代/年份产量统计 |
| `GET /movie/getTypeRank` `getNationRank` | 类型、国家排行 |
| `GET /movie/getTypeRate` | 类型-评分散点 |
| `GET /movie/getTimeLine` | 国家出品时间轴 |
| `GET /movie/getRecomendation` | ItemCF 推荐 |
| `GET /movie/getRecomendation2` | UserCF 推荐 |
| `POST /movie/getComments` | 影片影评（含情感标签） |

其他前缀：`/user`（注册登录等）、`/order`（订单）、`/alipay`（支付）、`/file`（上传）、
`/deeplearning/senti_single`（单条文本情感分析）。

---

## 七、常见问题

**Q1：导入 SQL 报 `2006 - Server has gone away`？**
文件超过服务端 `max_allowed_packet`（默认 64 MB）。要么用命令行导入（推荐），要么把
`my.ini` 里的 `max_allowed_packet` 调到 256M 后重启 MySQL 服务。

**Q2：后端启动报 `Access denied` / 连不上数据库？**
检查 MySQL 服务是否启动、第 39 行连接串里的口令是否与本机一致、库名是否为
`flask_douban_comment`。

**Q3：情感分析接口报 Paddle 相关错误？**
先安装 `paddlepaddle` 与 `paddlenlp`，确认 `deeplearning/checkpoints/final.pdparams`
与 `vocab.json` 存在，且 `predict_lstm.py` 中加载路径使用 `os.path.dirname(__file__)`
拼出的绝对路径。

**Q4：前端启动或构建报 `ERR_OSSL_EVP_UNSUPPORTED`？**
Vue CLI 4 使用的 Webpack 4 与新版 Node.js 的 OpenSSL 默认配置不兼容。项目的 npm 脚本已
自动设置 `NODE_OPTIONS=--openssl-legacy-provider`；请使用 `npm run serve` 或 `npm run build`，
不要直接调用 `vue-cli-service`。若 `npm install` 本身失败，再检查网络或切换 Node.js LTS。

---

## 八、说明

本项目为毕业设计/学习用途，爬虫部分请遵守目标站点 robots 协议与相关法律法规，
控制抓取频率，仅用于个人学习研究，勿用于商业用途。
