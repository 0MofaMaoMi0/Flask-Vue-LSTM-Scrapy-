#!/usr/bin/env python
# -*- coding: utf-8 -*-

import re

from douban import validator
from douban.database import get_connection
from douban.items import Comment
from scrapy import Request, Spider


RATING_PATTERN = re.compile(r'allstar(\d+)')


class MovieCommentSpider(Spider):
    """Collect a bounded number of public short-comment pages per movie."""

    name = 'movie_comment'
    allowed_domains = ['movie.douban.com']
    handled_statuses = [301, 302, 303, 307, 308, 403, 404, 429]

    def __init__(self, limit=50, max_pages=11, movie_ids=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.limit = max(0, int(limit))
        self.max_pages = max(1, int(max_pages))
        self.movie_ids = [
            value.strip() for value in (movie_ids or '').split(',')
            if value.strip()
        ]

    def _load_movie_ids(self):
        if self.movie_ids:
            return self.movie_ids

        sql = (
            'SELECT m.douban_id FROM movies AS m '
            'LEFT JOIN ('
            '  SELECT douban_id, COUNT(*) AS comment_count '
            '  FROM comments GROUP BY douban_id'
            ') AS c ON c.douban_id=m.douban_id '
            'WHERE COALESCE(c.comment_count, 0) <= 20 '
            'ORDER BY m.douban_id DESC'
        )
        params = ()
        if self.limit:
            sql += ' LIMIT %s'
            params = (self.limit,)

        connection = get_connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute(sql, params)
                return [str(row['douban_id']) for row in cursor.fetchall()]
        finally:
            connection.close()

    @staticmethod
    def _request_meta(douban_id, page):
        return {
            'douban_id': douban_id,
            'page': page,
            'dont_redirect': True,
            'handle_httpstatus_list': MovieCommentSpider.handled_statuses,
        }

    async def start(self):
        for douban_id in self._load_movie_ids():
            url = (
                'https://movie.douban.com/subject/{}/comments?status=P'
                .format(douban_id)
            )
            yield Request(url, meta=self._request_meta(douban_id, 1))

    @staticmethod
    def _normalize_rating(classes):
        match = RATING_PATTERN.search(classes or '')
        return str(int(match.group(1)) // 10) if match else ''

    @staticmethod
    def _first(values, default=''):
        return next((value.strip() for value in values if value.strip()), default)

    def parse(self, response):
        douban_id = response.meta['douban_id']
        page = response.meta['page']
        if response.status != 200:
            self.logger.warning(
                'Comment page rejected: movie=%s page=%s status=%s url=%s',
                douban_id, page, response.status, response.url,
            )
            return

        nodes = response.css('div.comment-item')
        if not nodes:
            self.logger.warning(
                'No comment nodes found: movie=%s page=%s url=%s',
                douban_id, page, response.url,
            )

        for node in nodes:
            comment_id = (
                node.attrib.get('data-cid')
                or node.xpath('.//input/@value').get(default='')
            ).strip()
            content = node.xpath(
                'normalize-space(string(.//span[contains(@class,"short")]))'
            ).get(default='').strip()
            if not comment_id or not content:
                self.logger.debug(
                    'Skip incomplete comment: movie=%s comment_id=%s',
                    douban_id, comment_id,
                )
                continue

            nickname = self._first(
                node.css('div.avatar a::attr(title)').getall()
                + node.css('span.comment-info a::text').getall()
            )
            user_url = self._first(
                node.css('div.avatar a::attr(href)').getall()
                + node.css('span.comment-info a::attr(href)').getall()
            )
            avatar = self._first(node.css('div.avatar img::attr(src)').getall())
            votes_text = self._first(node.css('span.votes::text').getall(), '0')
            rating_class = self._first(
                node.css('span[class*="allstar"]::attr(class)').getall()
            )
            comment_time = self._first(
                node.css('span.comment-time::attr(title)').getall()
                + node.css('span.comment-time::text').getall()
            )

            try:
                votes = int(votes_text)
            except ValueError:
                votes = 0

            comment = Comment(
                douban_id=douban_id,
                douban_comment_id=comment_id,
                douban_user_nickname=validator.truncate_text(nickname),
                douban_user_avatar=validator.truncate_text(avatar),
                douban_user_url=validator.truncate_text(user_url),
                content=content,
                votes=votes,
                rating=self._normalize_rating(rating_class),
            )
            if comment_time:
                comment['comment_time'] = comment_time
            yield comment

        next_href = response.css('a.next::attr(href)').get()
        if next_href and page < self.max_pages:
            yield Request(
                response.urljoin(next_href),
                meta=self._request_meta(douban_id, page + 1),
            )

