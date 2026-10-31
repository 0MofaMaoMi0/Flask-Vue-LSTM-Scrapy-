# -*- coding: utf-8 -*-

from douban.runtime_config import (
    get_douban_user_agent,
    get_poster_public_prefix,
    get_poster_storage_root,
)

# Scrapy settings for douban project
#
# For simplicity, this file contains only settings considered important or
# commonly used. You can find more settings consulting the documentation:
#
#     https://doc.scrapy.org/en/latest/topics/settings.html
#     https://doc.scrapy.org/en/latest/topics/downloader-middleware.html
#     https://doc.scrapy.org/en/latest/topics/spider-middleware.html

BOT_NAME = 'douban'

SPIDER_MODULES = ['douban.spiders']
NEWSPIDER_MODULE = 'douban.spiders'

LOG_LEVEL = 'INFO'
IMAGES_STORE = get_poster_storage_root()
IMAGES_EXPIRES = 90
IMAGES_MIN_WIDTH = 120
IMAGES_MIN_HEIGHT = 160
POSTER_PUBLIC_PREFIX = get_poster_public_prefix()
POSTER_MAX_WIDTH = 360
POSTER_MAX_HEIGHT = 540
POSTER_WEBP_QUALITY = 82
POSTER_MAX_BYTES = 5 * 1024 * 1024

# Use one stable identity. An authenticated browser session should provide its
# matching User-Agent through DOUBAN_USER_AGENT instead of rotating identities.
USER_AGENT = get_douban_user_agent()

# Obey robots.txt rules
ROBOTSTXT_OBEY = True



# POS BFS,NEG DFS,default 0 DFS
DEPTH_PRIORITY = 1
SCHEDULER_DISK_QUEUE = 'scrapy.squeues.PickleFifoDiskQueue'
SCHEDULER_MEMORY_QUEUE = 'scrapy.squeues.FifoMemoryQueue'

DEPTH_LIMIT = 0
DEPTH_STATS_VERBOSE=True

# Download timeout
DOWNLOAD_TIMEOUT = 30

# Configure maximum concurrent requests performed by Scrapy (default: 16)
CONCURRENT_REQUESTS = 2

# Configure a delay for requests for the same website (default: 0)
# See https://doc.scrapy.org/en/latest/topics/settings.html#download-delay
# See also autothrottle settings and docs
DOWNLOAD_DELAY = 10
RANDOMIZE_DOWNLOAD_DELAY = True
# The download delay setting will honor only one of:
# CONCURRENT_REQUESTS_PER_DOMAIN = 16
CONCURRENT_REQUESTS_PER_IP = 1

# Keep Scrapy's cookie handling enabled. The project middleware remains inert
# when neither DOUBAN_COOKIE nor the legacy COOKIE variable is configured.
COOKIES_ENABLED = True
RETRY_ENABLED = True

#SET REDIRECT ENABLE
#REDIRECT_ENABLED = False 
#HTTPERROR_ALLOWED_CODES = [302,] 

RETRY_TIMES = 2
RETRY_HTTP_CODES = [408, 429, 500, 502, 503, 504, 522, 524]

# Disable Telnet Console (enabled by default)
# TELNETCONSOLE_ENABLED = False

# Override the default request headers:
# DEFAULT_REQUEST_HEADERS = {
#   'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
#   'Accept-Language': 'en',
# }

# Enable or disable spider middlewares
# See https://doc.scrapy.org/en/latest/topics/spider-middleware.html
# No project-specific spider middleware is required. The legacy proxy and
# rotating-UA helpers remain disabled and are not imported by normal crawls.
SPIDER_MIDDLEWARES = {}

# Inject the optional environment Cookie only for movie.douban.com. Legacy
# proxy and random-UA middleware stays disabled.
DOWNLOADER_MIDDLEWARES = {
    'douban.middlewares.EnvironmentCookieMiddleware': 650,
}

# Enable or disable extensions
# See https://doc.scrapy.org/en/latest/topics/extensions.html
# EXTENSIONS = {
#    'scrapy.extensions.telnet.TelnetConsole': None,
# }

# Configure item pipelines
# See https://doc.scrapy.org/en/latest/topics/item-pipeline.html
ITEM_PIPELINES = {
    'douban.pipelines.CoverPipeline': 1,
    'douban.pipelines.DoubanPipeline': 300,
}

# Enable and configure the AutoThrottle extension (disabled by default)
# See https://doc.scrapy.org/en/latest/topics/autothrottle.html
AUTOTHROTTLE_ENABLED = True
# The initial download delay
AUTOTHROTTLE_START_DELAY = 10
# The maximum download delay to be set in case of high latencies
AUTOTHROTTLE_MAX_DELAY = 60
# The average number of requests Scrapy should be sending in parallel to
# each remote server
AUTOTHROTTLE_TARGET_CONCURRENCY = 1.0
# Enable showing throttling stats for every response received:
# AUTOTHROTTLE_DEBUG = False

# Enable and configure HTTP caching (disabled by default)
# See https://doc.scrapy.org/en/latest/topics/downloader-middleware.html#httpcache-middleware-settings
# HTTPCACHE_ENABLED = True
# HTTPCACHE_EXPIRATION_SECS = 0
# HTTPCACHE_DIR = 'httpcache'
# HTTPCACHE_IGNORE_HTTP_CODES = []
# HTTPCACHE_STORAGE = 'scrapy.extensions.httpcache.FilesystemCacheStorage'
