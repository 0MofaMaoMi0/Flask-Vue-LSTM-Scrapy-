#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Resolve posters through TMDB using IMDb IDs already stored in MySQL."""

import re
from urllib.parse import quote, urlencode

from douban.database import get_connection
from douban.items import MovieMeta
from douban.runtime_config import (
    get_tmdb_api_read_token,
    get_tmdb_image_size,
    get_tmdb_language,
)
from scrapy import Request, Spider
from scrapy.exceptions import NotConfigured


TMDB_FIND_URL = 'https://api.themoviedb.org/3/find/{}'
# 豆瓣的 movies 表里混有剧集与演出录像，/search/movie 对它们返回空，
# 因此统一走 /search/multi（结果含 movie/tv/person，person 直接跳过）。
TMDB_SEARCH_URL = 'https://api.themoviedb.org/3/search/multi'
TMDB_IMAGE_URL = 'https://image.tmdb.org/t/p/{}/{}'
IMDB_ID_PATTERN = re.compile(r'^tt\d+$')
YEAR_PATTERN = re.compile(r'^\d{4}$')
IMAGE_SIZE_PATTERN = re.compile(r'^(?:w[1-9]\d{1,3}|original)$')


class MovieCoverTmdbSpider(Spider):
    """Download posters from TMDB without requesting Douban image hosts."""

    name = 'movie_cover_tmdb'
    allowed_domains = ['api.themoviedb.org', 'image.tmdb.org']
    custom_settings = {
        'DOWNLOAD_DELAY': 0.5,
        'AUTOTHROTTLE_START_DELAY': 0.5,
        'AUTOTHROTTLE_MAX_DELAY': 10,
        'CONCURRENT_REQUESTS': 2,
        'CONCURRENT_REQUESTS_PER_IP': 1,
        'ROBOTSTXT_OBEY': True,
    }

    def __init__(self, limit=50, movie_ids=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.limit = max(1, int(limit))
        self.movie_ids = [
            value.strip() for value in (movie_ids or '').split(',')
            if value.strip()
        ]
        self.api_token = get_tmdb_api_read_token()
        self.language = get_tmdb_language()
        self.image_size = get_tmdb_image_size()
        if not self.api_token:
            raise NotConfigured(
                'TMDB_API_READ_TOKEN is required for movie_cover_tmdb'
            )
        if not IMAGE_SIZE_PATTERN.fullmatch(self.image_size):
            raise NotConfigured('TMDB_IMAGE_SIZE must be w<number> or original')

    def _load_rows(self):
        connection = get_connection()
        try:
            with connection.cursor() as cursor:
                fields = (
                    'douban_id,imdb_id,name,year,cover,cover_path,cover_hash'
                )
                if self.movie_ids:
                    placeholders = ','.join(['%s'] * len(self.movie_ids))
                    cursor.execute(
                        'SELECT {} FROM movies WHERE douban_id IN ({}) '
                        'ORDER BY id'.format(fields, placeholders),
                        tuple(self.movie_ids),
                    )
                else:
                    cursor.execute(
                        'SELECT {} FROM movies WHERE '
                        "(imdb_id LIKE %s OR "
                        "(name<>'' AND year BETWEEN 1000 AND 9999)) AND "
                        "(cover_status<>'ok' OR cover_path='') "
                        'ORDER BY id LIMIT %s'.format(fields),
                        ('tt%', self.limit),
                    )
                return cursor.fetchall()
        finally:
            connection.close()

    async def start(self):
        for row in self._load_rows():
            request = self._request_for_row(row)
            if request is None:
                self.logger.warning(
                    'Skipping douban_id=%s: no IMDb ID or movie name',
                    row.get('douban_id'),
                )
                continue
            yield request

    def _request_for_row(self, row):
        headers = {
            'Authorization': 'Bearer {}'.format(self.api_token),
            'Accept': 'application/json',
        }
        imdb_id = str(row.get('imdb_id') or '').strip()
        if IMDB_ID_PATTERN.fullmatch(imdb_id):
            query = urlencode({
                'external_source': 'imdb_id',
                'language': self.language,
            })
            return Request(
                '{}?{}'.format(
                    TMDB_FIND_URL.format(quote(imdb_id, safe='')),
                    query,
                ),
                headers=headers,
                callback=self.parse_find,
                errback=self.find_failed,
                cb_kwargs={'row': row},
                dont_filter=True,
            )

        name = str(row.get('name') or '').strip()
        year = str(row.get('year') or '').strip()
        if not name or not YEAR_PATTERN.fullmatch(year):
            return None
        params = {
            'query': name,
            'language': self.language,
            'include_adult': 'false',
            'page': 1,
        }
        return Request(
            '{}?{}'.format(TMDB_SEARCH_URL, urlencode(params)),
            headers=headers,
            callback=self.parse_search,
            errback=self.find_failed,
            cb_kwargs={'row': row},
            dont_filter=True,
        )

    def parse_find(self, response, row):
        payload = response.json()
        result = self._first_poster(payload.get('movie_results', []))
        if result is None:
            self.logger.warning(
                'TMDB has no poster for douban_id=%s imdb_id=%s',
                row.get('douban_id'),
                row.get('imdb_id'),
            )
            return

        yield self._poster_item(row, result['poster_path'])

    def parse_search(self, response, row):
        payload = response.json()
        expected_year = str(row.get('year') or '').strip()
        if not YEAR_PATTERN.fullmatch(expected_year):
            expected_year = ''
        results = payload.get('results', [])
        # 不用 TMDB 的 year 参数：它按上映年份硬过滤，会把年份口径不一致的
        # 条目（如剧集、重映片）整个排除。改为片名必须一致、同年优先。
        result = self._pick_exact_title(results, row.get('name'), expected_year)
        if result is None:
            self.logger.warning(
                'TMDB search has no exact title match for douban_id=%s name=%s '
                '(year=%s, results=%d)',
                row.get('douban_id'),
                row.get('name'),
                expected_year or '-',
                len(results),
            )
            return
        yield self._poster_item(row, result['poster_path'])

    @staticmethod
    def _normalize_title(value):
        text = str(value or '').casefold()
        return re.sub(r'[\s\u3000·・:：_\-—,，.。!！?？()（）\[\]【】《》"\']', '', text)

    @staticmethod
    def _result_titles(movie):
        values = (
            movie.get('title'),
            movie.get('name'),
            movie.get('original_title'),
            movie.get('original_name'),
        )
        return [value for value in values if value]

    @staticmethod
    def _result_date(movie):
        return str(
            movie.get('release_date') or movie.get('first_air_date') or ''
        )

    def _pick_exact_title(self, results, name, expected_year=''):
        key = self._normalize_title(name)
        if not key:
            return None
        matched = []
        for movie in results:
            if movie.get('media_type') == 'person':
                continue
            if not movie.get('poster_path'):
                continue
            titles = {
                self._normalize_title(value)
                for value in self._result_titles(movie)
            }
            if key in titles:
                matched.append(movie)
        if not matched:
            return None
        if expected_year:
            same_year = [
                movie for movie in matched
                if self._result_date(movie).startswith(expected_year)
            ]
            if same_year:
                return same_year[0]
        # 年份口径不同（剧集/重映）时以片名为准，取 TMDB 返回顺序的第一条。
        return matched[0]

    @staticmethod
    def _first_poster(results):
        return next(
            (movie for movie in results if movie.get('poster_path')),
            None,
        )

    def _poster_item(self, row, poster_path):
        poster_path = poster_path.lstrip('/')
        source_url = TMDB_IMAGE_URL.format(self.image_size, poster_path)
        return MovieMeta(
            douban_id=str(row['douban_id']),
            cover=row.get('cover') or '',
            cover_source_url=source_url,
            cover_path=row.get('cover_path') or '',
            cover_hash=row.get('cover_hash') or '',
        )

    def find_failed(self, failure):
        self.logger.warning(
            'TMDB lookup failed for %s: %s',
            failure.request.url,
            failure.getErrorMessage(),
        )
