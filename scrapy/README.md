# 豆瓣 Scrapy 模块

该目录负责采集电影 ID、电影详情、公开短评和影人信息。爬虫只写原始表，
Flask 使用的应用表由显式后处理步骤生成或同步。

## 数据流

```text
movie_subject -> subjects
subjects -> movie_meta -> movies
movies -> movie_comment -> comments
movies -> rebuild_pid -> person_obj -> person_item -> person

movies -> sync_movies -> movies2
comments -> Flask/docomment.py 情感分析 -> comments2
movies(imdb_id) -> movie_cover_tmdb -> CoverPipeline -> data/covers
```

## 安全默认值

- 遵守目标站点 robots.txt。
- 同一 IP 最多 1 个并发请求，总并发为 2。
- 基础下载间隔 10 秒，并启用 AutoThrottle。
- 仅对超时、429 和常见 5xx 状态做最多 2 次重试。
- Cookie 未配置时中间件自动停用；代理和随机 UA 中间件仍默认关闭。
- 详情和人物爬虫默认每次最多处理 100 条。
- 评论爬虫默认最多处理 50 部电影、每部 11 页。

robots.txt 禁止的路径不会被采集，这是预期行为。不要通过代理、提高并发或关闭
robots 配置绕过站点限制。

## 环境

从项目根目录创建独立环境：

```powershell
.\.venv\Scripts\python.exe -m venv .venv-scrapy
.\.venv-scrapy\Scripts\python.exe -m pip install -r scrapy\requirements.txt --index-url https://pypi.org/simple
.\.venv-scrapy\Scripts\python.exe -m pip install -r scrapy\requirements-images.txt --index-url https://pypi.org/simple
```

数据库连接由项目根目录的 `.env` 提供：

```text
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=change_me
MYSQL_DATABASE=flask_douban_comment
MYSQL_DATABASE_SPIDER=flask_douban_comment
```

不要把实际密码提交到仓库。

海报使用项目根目录的持久化目录，数据库只保存原始 URL、相对路径和状态：

```text
POSTER_STORAGE_ROOT=data/covers
POSTER_PUBLIC_PREFIX=/api/media/covers
```

首次启用前先预览并应用只新增列的海报结构迁移：

```powershell
cd scrapy
..\.venv-scrapy\Scripts\python.exe -m douban.migrate_posters
..\.venv-scrapy\Scripts\python.exe -m douban.migrate_posters --apply
```

如需使用自己的豆瓣会话，在项目根目录 `.env` 中配置：

```text
DOUBAN_COOKIE=浏览器复制的完整 Cookie
DOUBAN_USER_AGENT=复制 Cookie 时浏览器的 navigator.userAgent
```

项目也兼容已有的 `COOKIE` 变量，但新配置建议使用
`DOUBAN_COOKIE`。Cookie 只会注入 `movie.douban.com` 请求，不会发给图片域名。
可以先执行一次只读 HTTP 请求验证配置（不读写数据库）：

```powershell
cd scrapy
..\.venv-scrapy\Scripts\python.exe -m douban.check_session
```

## 运行顺序

以下命令均在 `scrapy/` 目录执行。

### 1. 收集电影 ID

默认从 `https://movie.douban.com/explore` 进入，读取该页使用的
动态推荐接口。只保留电影类型的数字 ID，忽略片单和广告；默认仅请求
1 页，每页最多 20 个响应项：

```powershell
..\.venv-scrapy\Scripts\python.exe -m scrapy crawl movie_subject
```

明确需要更多页时可调整上限，例如最多 3 页：

```powershell
..\.venv-scrapy\Scripts\python.exe -m scrapy crawl movie_subject -a max_pages=3
```

也保留逗号分隔的自定义静态种子页入口：

```powershell
..\.venv-scrapy\Scripts\python.exe -m scrapy crawl movie_subject -a seed_urls=https://movie.douban.com/cinema/nowplaying/beijing/
```

### 2. 采集电影详情

默认处理最多 100 个尚未存在于 `movies` 的 ID：

```powershell
..\.venv-scrapy\Scripts\python.exe -m scrapy crawl movie_meta -a limit=100
```

也可以只处理指定 ID：

```powershell
..\.venv-scrapy\Scripts\python.exe -m scrapy crawl movie_meta -a movie_ids=1292052,1295644
```

只有明确需要完整运行时才使用 `-a limit=0`。

### 3. 采集公开短评

```powershell
..\.venv-scrapy\Scripts\python.exe -m scrapy crawl movie_comment -a limit=50 -a max_pages=11
```

也可以使用 `-a movie_ids=1292052` 定向采集。评分会被规范化为 1～5，
缺少评论 ID 或正文的条目会被跳过。

### 补齐或修复海报

不重新请求电影详情页，只下载数据库中缺失或失败的海报：

```powershell
..\.venv-scrapy\Scripts\python.exe -m scrapy crawl movie_cover -a limit=100
```

也可定向修复：

```powershell
..\.venv-scrapy\Scripts\python.exe -m scrapy crawl movie_cover -a movie_ids=1292052
```

允许采集的海报会限制在 360×540 以内，转换为 WebP，保存到
`data/covers/<2位>/<2位>/<URL哈希>.webp`。同一来源 URL 会复用缓存，
默认 90 天后才重新检查。图片域名如通过 `robots.txt` 禁止自动下载，任务会
保留原始 URL，并把状态和原因记录为 `failed`；不要关闭 robots 配置绕过限制，
应改用获得授权的图片来源或导入已有本地图片。

#### 通过 TMDB 获取海报

TMDB 官方 API 支持用 IMDb ID 匹配电影。本项目优先读取 `movies.imdb_id`；
旧数据没有 IMDb ID 时，会保守地使用电影名和上映年份搜索，并只接受年份匹配且
包含海报的结果；缺少年份的记录默认跳过。该功能仅在 `.env` 配置 API Read
Access Token 后启用：

```text
TMDB_API_READ_TOKEN=你的_Bearer_Token
TMDB_LANGUAGE=zh-CN
TMDB_IMAGE_SIZE=w500
```

先定向测试一部电影，再扩大到有限批次：

```powershell
..\.venv-scrapy\Scripts\python.exe -m scrapy crawl movie_cover_tmdb -a movie_ids=1292052
..\.venv-scrapy\Scripts\python.exe -m scrapy crawl movie_cover_tmdb -a limit=50
```

TMDB 查询结果中的 `poster_path` 会转换为官方 `w500` 图片 URL，然后继续使用
现有 WebP 转换、哈希去重、本地缓存和 MySQL 状态字段。启用前应阅读 TMDB
[认证说明](https://developer.themoviedb.org/docs/authentication-application)与
[署名要求](https://developer.themoviedb.org/docs/faq)；项目“关于”页面已经加入
官方要求的文字声明，部署前还应从 TMDB 官方署名页面下载并展示获准使用的 Logo。

#### 导入已有本地海报

将获得授权的图片按 `<豆瓣电影ID>.jpg/png/webp` 命名后放入一个目录。先做
不写数据库的验证：

```powershell
..\.venv-scrapy\Scripts\python.exe -m douban.import_posters D:\posters --limit 100
```

确认统计后应用导入；该命令会同时更新 `movies` 和已存在的 `movies2` 行：

```powershell
..\.venv-scrapy\Scripts\python.exe -m douban.import_posters D:\posters --limit 100 --apply
```

只有明确要处理全部文件时才使用 `--limit 0 --apply`。子目录中的文件需要额外
添加 `--recursive`。文件会统一转换为 360×540 以内的 WebP，并按内容哈希去重。

### 4. 回填并采集影人

```powershell
..\.venv-scrapy\Scripts\python.exe -m douban.rebuild_pid
..\.venv-scrapy\Scripts\python.exe -m scrapy crawl person_item -a limit=100
```

也可以使用 `-a person_ids=1054521,1047973` 定向采集。

## 同步应用电影表

Flask 读取 `movies2`，爬虫写入 `movies`。先进行只读预览：

```powershell
..\.venv-scrapy\Scripts\python.exe -m douban.sync_movies --limit 100
```

确认统计符合预期后，再显式写入：

```powershell
..\.venv-scrapy\Scripts\python.exe -m douban.sync_movies --limit 100 --apply
```

`--limit 0 --apply` 会同步全部电影，属于批量数据库写入，应先备份并确认范围。

评论的 `comments2` 需要 PaddleNLP 情感分析，不由爬虫自动写入。

## 离线验证

不访问豆瓣、不写数据库：

```powershell
..\.venv-scrapy\Scripts\python.exe -m unittest discover -s tests -v
..\.venv-scrapy\Scripts\python.exe -m scrapy list
```

也可使用跨平台入口，例如：

```powershell
..\.venv-scrapy\Scripts\python.exe run.py movie_meta -a limit=20
```

