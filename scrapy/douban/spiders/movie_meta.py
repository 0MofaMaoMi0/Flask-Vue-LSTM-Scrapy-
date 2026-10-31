#!/usr/bin/env python
# -*- coding: utf-8 -*-

import re

from douban import util, validator
from douban.database import get_connection
from douban.items import MovieMeta
from scrapy import Request, Spider


class MovieMetaSpider(Spider):
    """Collect structured metadata for movie IDs stored in subjects."""

    name = 'movie_meta'
    allowed_domains = ['movie.douban.com']
    handled_statuses = [301, 302, 303, 307, 308, 403, 404, 429]

    def __init__(self, limit=100, movie_ids=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.limit = max(0, int(limit))
        self.movie_ids = [
            value.strip() for value in (movie_ids or '').split(',')
            if value.strip()
        ]

    def _load_movie_ids(self):
        if self.movie_ids:
            return self.movie_ids

        sql = (
            'SELECT s.douban_id FROM subjects AS s '
            'WHERE s.type=%s AND NOT EXISTS ('
            '  SELECT 1 FROM movies AS m WHERE m.douban_id=s.douban_id'
            ') ORDER BY s.douban_id DESC'
        )
        params = ['movie']
        if self.limit:
            sql += ' LIMIT %s'
            params.append(self.limit)

        connection = get_connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute(sql, tuple(params))
                return [str(row['douban_id']) for row in cursor.fetchall()]
        finally:
            connection.close()

    @staticmethod
    def _request_meta(douban_id):
        return {
            'douban_id': douban_id,
            'dont_redirect': True,
            'handle_httpstatus_list': MovieMetaSpider.handled_statuses,
        }

    async def start(self):
        for douban_id in self._load_movie_ids():
            url = 'https://movie.douban.com/subject/{}/'.format(douban_id)
            yield Request(url, meta=self._request_meta(douban_id))

    @staticmethod
    def _info_text(response, label):
        values = response.xpath(
            '//div[@id="info"]/span[normalize-space()="{}"]'
            '/following-sibling::text()[1]'.format(label)
        ).getall()
        return ' '.join(value.strip() for value in values if value.strip())

    @staticmethod
    def _person_credits(response, relation):
        names = response.xpath(
            '//a[@rel="{}"]/text()'.format(relation)
        ).getall()
        hrefs = response.xpath(
            '//a[@rel="{}"]/@href'.format(relation)
        ).getall()
        ids = []
        for name, href in zip(names, hrefs):
            match = re.search(r'/(?:celebrity|person)/(\d+)/', href)
            if match:
                ids.append('{}:{}'.format(name.strip(), match.group(1)))
        return names, ids

    def parse(self, response):
        douban_id = response.meta['douban_id']
        if response.status != 200:
            self.logger.warning(
                'Movie page rejected: movie=%s status=%s url=%s',
                douban_id, response.status, response.url,
            )
            return

        title = response.xpath('normalize-space(//title)').get(default='')
        name = re.sub(r'\s*\(豆瓣\)\s*$', '', title).strip()
        if not name:
            self.logger.warning('Missing movie title: %s', response.url)
            return

        directors, director_ids = self._person_credits(response, 'v:directedBy')
        actors, actor_ids = self._person_credits(response, 'v:starring')

        cover = response.xpath('//img[@rel="v:image"]/@src').get(default='')
        if 'default' in cover:
            cover = ''

        year_text = response.css('span.year::text').get(default='')
        release_date = ''
        for value in response.xpath(
            '//span[@property="v:initialReleaseDate"]/@content'
        ).getall():
            release_date = validator.str_to_date(validator.match_date(value))
            if release_date:
                break

        imdb_href = response.xpath(
            '//div[@id="info"]/span[normalize-space()="IMDb:"]'
            '/following-sibling::a[1]/@href | '
            '//a[preceding-sibling::span[normalize-space()="IMDb链接:"]]/@href'
        ).get(default='')
        imdb_match = re.search(r'(tt\d+)', imdb_href)

        storyline = response.xpath(
            'normalize-space(string(//span[contains(@class,"all")'
            ' and contains(@class,"hidden")][1]))'
        ).get(default='').strip()
        if not storyline:
            storyline = response.xpath(
                'normalize-space(string(//span[@property="v:summary"][1]))'
            ).get(default='').strip()

        item = MovieMeta(
            douban_id=douban_id,
            type='tv' if response.xpath(
                '//div[@id="info"]/span[normalize-space()="集数:"]'
            ) else 'movie',
            cover=cover,
            name=name,
            slug=util.shorturl(douban_id),
            year=validator.match_year(year_text),
            directors=validator.process_slash_str('/'.join(directors)),
            actors=validator.process_slash_str('/'.join(actors)),
            genres='/'.join(
                value.strip() for value in response.xpath(
                    '//span[@property="v:genre"]/text()'
                ).getall() if value.strip()
            ),
            official_site=validator.process_url(
                response.xpath(
                    '//div[@id="info"]/span[normalize-space()="官方网站:"]'
                    '/following-sibling::a[1]/@href'
                ).get(default='')
            ),
            regions=self._info_text(response, '制片国家/地区:'),
            languages=self._info_text(response, '语言:'),
            mins=response.xpath(
                '//span[@property="v:runtime"]/@content'
            ).get(default='0') or '0',
            alias=validator.truncate_text(validator.process_slash_str(
                self._info_text(response, '又名:')
            )),
            imdb_id=imdb_match.group(1) if imdb_match else '',
            douban_score=response.xpath(
                'normalize-space(//strong[@property="v:average"])'
            ).get(default='0') or '0',
            douban_votes=response.xpath(
                'normalize-space(//span[@property="v:votes"])'
            ).get(default='0') or '0',
            tags=validator.truncate_text('/'.join(
                value.strip() for value in response.css(
                    'div.tags-body a::text'
                ).getall() if value.strip()
            )),
            storyline=storyline,
            actor_ids=validator.truncate_text('|'.join(actor_ids)),
            director_ids=validator.truncate_text('|'.join(director_ids)),
        )
        if release_date:
            item['release_date'] = release_date
        return item
