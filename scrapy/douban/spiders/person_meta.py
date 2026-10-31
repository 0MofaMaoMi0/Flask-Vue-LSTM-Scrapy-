#!/usr/bin/env python
# -*- coding: utf-8 -*-

from douban import validator
from douban.database import get_connection
from douban.items import PersonMeta
from scrapy import Request, Spider


class PersonMetaSpider(Spider):
    """Collect celebrity metadata for people referenced by movie credits."""

    name = 'person_item'
    allowed_domains = ['movie.douban.com']
    handled_statuses = [301, 302, 303, 307, 308, 403, 404, 429]

    def __init__(self, limit=100, person_ids=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.limit = max(0, int(limit))
        self.person_ids = [
            value.strip() for value in (person_ids or '').split(',')
            if value.strip()
        ]

    def _load_people(self):
        if self.person_ids:
            return [(person_id, '') for person_id in self.person_ids]

        sql = (
            'SELECT po.person_id, po.name FROM person_obj AS po '
            'WHERE NOT EXISTS ('
            '  SELECT 1 FROM person AS p WHERE p.person_id=po.person_id'
            ') ORDER BY po.person_id DESC'
        )
        params = ()
        if self.limit:
            sql += ' LIMIT %s'
            params = (self.limit,)

        connection = get_connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute(sql, params)
                return [
                    (str(row['person_id']), row.get('name') or '')
                    for row in cursor.fetchall()
                ]
        finally:
            connection.close()

    @staticmethod
    def _request_meta(person_id, fallback_name):
        return {
            'person_id': person_id,
            'fallback_name': fallback_name,
            'dont_redirect': True,
            'handle_httpstatus_list': PersonMetaSpider.handled_statuses,
        }

    async def start(self):
        for person_id, fallback_name in self._load_people():
            url = 'https://movie.douban.com/celebrity/{}/'.format(person_id)
            yield Request(url, meta=self._request_meta(person_id, fallback_name))

    @staticmethod
    def _info_value(response, label):
        values = response.xpath(
            '//div[contains(@class,"info")]//li['
            'span[normalize-space()="{}"]]/text()'.format(label)
        ).getall()
        text = ' '.join(value.strip() for value in values if value.strip())
        return text.lstrip(':：').strip()

    def parse(self, response):
        person_id = response.meta['person_id']
        if response.status != 200:
            self.logger.warning(
                'Person page rejected: person=%s status=%s url=%s',
                person_id, response.status, response.url,
            )
            return

        name = response.xpath(
            'normalize-space(string(//div[@id="content"]//h1[1]))'
        ).get(default='').strip()
        name = name or response.meta.get('fallback_name', '').strip()
        if not name:
            self.logger.warning('Missing person name: %s', response.url)
            return

        birth_raw = self._info_value(response, '出生日期')
        birth = validator.str_to_date(validator.match_date(birth_raw))
        biography = response.xpath(
            'normalize-space(string(//div[@id="intro"]'
            '//span[contains(@class,"all")]))'
        ).get(default='').strip()
        if not biography:
            biography = response.xpath(
                'normalize-space(string(//div[@id="intro"]'
                '//span[contains(@class,"short")]))'
            ).get(default='').strip()

        yield PersonMeta(
            person_id=person_id,
            name=name,
            sex=self._info_value(response, '性别'),
            birth=birth or birth_raw,
            birthplace=self._info_value(response, '出生地'),
            biography=validator.truncate_text(biography),
            profession=self._info_value(response, '职业'),
            constellatory=self._info_value(response, '星座'),
            name_zh=self._info_value(response, '更多中文名'),
            name_en=self._info_value(response, '更多外文名'),
        )
