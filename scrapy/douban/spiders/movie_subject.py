#!/usr/bin/env python
# -*- coding: utf-8 -*-

import re
from urllib.parse import urlencode

from douban.items import Subject
from scrapy import Request, Spider


SUBJECT_ID_PATTERN = re.compile(r'/subject/(\d+)/')


class MovieSubjectSpider(Spider):
    """Collect movie IDs exposed by Douban's movie explore page."""

    name = 'movie_subject'
    allowed_domains = ['movie.douban.com', 'm.douban.com']
    start_urls = ['https://movie.douban.com/explore']
    recommend_api = 'https://m.douban.com/rexxar/api/v2/movie/recommend'

    def __init__(self, seed_urls=None, max_pages=1, page_size=20,
                 *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.max_pages = max(1, int(max_pages))
        self.page_size = min(20, max(1, int(page_size)))
        self.seen_ids = set()
        if seed_urls:
            self.start_urls = [
                url.strip() for url in seed_urls.split(',') if url.strip()
            ]

    def parse(self, response):
        if response.url.rstrip('/') == 'https://movie.douban.com/explore':
            yield self._recommend_request(page=1)
            return

        subject_ids = set()

        current_match = SUBJECT_ID_PATTERN.search(response.url)
        if current_match:
            subject_ids.add(current_match.group(1))

        for href in response.css('a::attr(href)').getall():
            match = SUBJECT_ID_PATTERN.search(href)
            if match:
                subject_ids.add(match.group(1))

        self.logger.info(
            'Discovered %s movie IDs from %s', len(subject_ids), response.url
        )
        for douban_id in sorted(subject_ids):
            if douban_id not in self.seen_ids:
                self.seen_ids.add(douban_id)
                yield Subject(douban_id=douban_id, type='movie')

    def _recommend_request(self, page):
        params = {
            'refresh': 0,
            'start': (page - 1) * self.page_size,
            'count': self.page_size,
            'selected_categories': '{}',
            'uncollect': 'false',
            'score_range': '0,10',
        }
        return Request(
            '{}?{}'.format(self.recommend_api, urlencode(params)),
            callback=self.parse_recommend,
            meta={
                'page': page,
                'handle_httpstatus_list': [403, 429],
            },
        )

    def parse_recommend(self, response):
        page = response.meta['page']
        if response.status != 200:
            self.logger.warning(
                'Explore API rejected: page=%s status=%s',
                page, response.status,
            )
            return

        try:
            data = response.json()
        except ValueError:
            self.logger.warning('Explore API returned invalid JSON: page=%s', page)
            return

        items = data.get('items') or []
        discovered = 0
        for item in items:
            douban_id = str(item.get('id') or '')
            if (
                item.get('type') == 'movie'
                and douban_id.isdigit()
                and douban_id not in self.seen_ids
            ):
                self.seen_ids.add(douban_id)
                discovered += 1
                yield Subject(douban_id=douban_id, type='movie')

        self.logger.info(
            'Discovered %s new movie IDs from explore API page %s',
            discovered, page,
        )
        if items and page < self.max_pages:
            yield self._recommend_request(page + 1)
