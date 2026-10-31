#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from douban.database import get_connection
from douban.items import MovieMeta
from scrapy import Request, Spider


class MovieCoverSpider(Spider):
    """Repair missing or failed movie poster files without refetching pages."""

    name = 'movie_cover'

    def __init__(self, limit=100, movie_ids=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.limit = max(1, int(limit))
        self.movie_ids = [
            value.strip() for value in (movie_ids or '').split(',')
            if value.strip()
        ]

    def _load_rows(self):
        connection = get_connection()
        try:
            with connection.cursor() as cursor:
                fields = (
                    'douban_id,cover,cover_source_url,cover_path,cover_hash'
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
                        "(cover_source_url<>'' OR cover LIKE %s) AND "
                        "(cover_status<>'ok' OR cover_path='') "
                        'ORDER BY id LIMIT %s'.format(fields),
                        ('http%', self.limit),
                    )
                return cursor.fetchall()
        finally:
            connection.close()

    async def start(self):
        for row in self._load_rows():
            yield Request(
                'data:text/plain,{}'.format(row['douban_id']),
                callback=self.parse_row,
                cb_kwargs={'row': row},
                dont_filter=True,
            )

    def parse_row(self, response, row):
        source_url = row.get('cover_source_url') or row.get('cover') or ''
        if not source_url.startswith(('http://', 'https://')):
            return
        yield MovieMeta(
            douban_id=str(row['douban_id']),
            cover=row.get('cover') or '',
            cover_source_url=source_url,
            cover_path=row.get('cover_path') or '',
            cover_hash=row.get('cover_hash') or '',
        )
