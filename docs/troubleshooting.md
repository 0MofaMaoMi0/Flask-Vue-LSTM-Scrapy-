# 已确认的排障要点

## 运行与依赖

- 后端依赖位于 `Flask/requirements.txt`；README 记录了 Paddle CPU 源和 `python-alipay-sdk` 的独立安装原因。未经验证不要升级这些锁定版本。
- Scrapy 有独立的 `.venv-scrapy` 环境，避免与后端的 Paddle/Flask 依赖混用。
- Vue CLI 4 运行在新版 Node.js 时可能触发 OpenSSL 兼容错误。`package.json` 的 `serve`、`build`、`lint` 已设置 `NODE_OPTIONS=--openssl-legacy-provider`，请通过 npm 脚本执行。

## 配置与服务

- 后端启动前 `.env` 必须包含 `MYSQL_HOST`、`MYSQL_PORT`、`MYSQL_USER`、`MYSQL_PASSWORD`、`MYSQL_DATABASE` 和 `SECRET_KEY`；模板文件不能代替真实配置。
- Scrapy 的 `database.py` 不会读取根目录 `.env`。从 `scrapy/` 单独启动时，须确认环境变量已传入；不要为了排障把密码写回源码。
- 情感分析要求 `Flask/deeplearning/vocab.json` 和 `checkpoints/final.pdparams` 同时存在且匹配；先检查路径与依赖，不要直接重新训练。

## 已识别但未改动的风险

- `Flask/test/` 中的历史脚本包含旧式、硬编码数据库连接串，不应作为当前测试或配置样板。
- 爬虫设置当前为 `ROBOTSTXT_OBEY = False`，且 pipeline 直接写 MySQL。任何运行前应先确认目标站点授权、频率和数据库备份。
- `vue/dist/`、运行日志和部分上传文件已存在于工作目录；当前目录没有 Git 元数据，无法判断它们是否应被版本控制。
