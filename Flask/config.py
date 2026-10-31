"""Runtime configuration loaded from the project-root .env file."""

import os
import sys
from pathlib import Path
from urllib.parse import quote_plus


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from project_config import optional, required


class Config:
    MYSQL_HOST = required("MYSQL_HOST")
    MYSQL_PORT = int(required("MYSQL_PORT"))
    MYSQL_USER = required("MYSQL_USER")
    MYSQL_PASSWORD = required("MYSQL_PASSWORD")
    MYSQL_DATABASE = required("MYSQL_DATABASE")
    SQLALCHEMY_DATABASE_URI = (
        "mysql+pymysql://{user}:{password}@{host}:{port}/{database}".format(
            user=quote_plus(MYSQL_USER),
            password=quote_plus(MYSQL_PASSWORD),
            host=MYSQL_HOST,
            port=MYSQL_PORT,
            database=MYSQL_DATABASE,
        )
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    JSON_AS_ASCII = False
    SECRET_KEY = required("SECRET_KEY")
    HOST = optional("FLASK_HOST", "0.0.0.0")
    PORT = int(optional("FLASK_PORT", "8080"))
    DEBUG = optional("FLASK_DEBUG", "false").strip().lower() in {"1", "true", "yes", "on"}
    _poster_root = Path(optional("POSTER_STORAGE_ROOT", "data/covers"))
    if not _poster_root.is_absolute():
        _poster_root = PROJECT_ROOT / _poster_root
    POSTER_STORAGE_ROOT = str(_poster_root.resolve())
    # 海报回源代理的磁盘缓存上限（MB）；0 表示完全不落盘，只依赖浏览器缓存。
    POSTER_PROXY_CACHE_MB = float(optional("POSTER_PROXY_CACHE_MB", "0") or 0)
    POSTER_PROXY_USER_AGENT = optional(
        "POSTER_PROXY_USER_AGENT",
        optional("DOUBAN_USER_AGENT", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"),
    )
