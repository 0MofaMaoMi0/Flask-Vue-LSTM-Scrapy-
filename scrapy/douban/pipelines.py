#!/usr/bin/env python
# -*- coding: utf-8 -*-

import hashlib
from datetime import datetime, timezone
from io import BytesIO
from urllib.parse import quote, urlsplit

import douban.database as db
from douban.items import BookMeta, Comment, MovieMeta, PersonMeta, Subject
from scrapy import Request
from scrapy.exceptions import DropItem
from scrapy.pipelines.images import ImagesPipeline
from scrapy.utils.python import to_bytes


class DoubanPipeline:
    """Persist crawler items with one connection owned by the spider run."""

    ITEM_TARGETS = {
        Subject: ('subjects', 'douban_id', False),
        MovieMeta: ('movies', 'douban_id', True),
        BookMeta: ('books', 'douban_id', True),
        Comment: ('comments', 'douban_comment_id', False),
        PersonMeta: ('person', 'person_id', True),
    }
    INSERT_DEFAULTS = {
        # Some migrated databases lost the defaults declared in douban.sql.
        # Supply them only for new rows so updates keep existing app ratings.
        'movies': {'avg_score': 0.0, 'imdb_score': 0.0},
    }

    def open_spider(self, spider):
        self.connection = db.get_connection()
        self.cursor = self.connection.cursor()

    def close_spider(self, spider):
        cursor = getattr(self, 'cursor', None)
        connection = getattr(self, 'connection', None)
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()

    @staticmethod
    def _clean_value(value):
        return value.strip() if isinstance(value, str) else value

    def _exists(self, table, key, value):
        sql = 'SELECT id FROM `{}` WHERE `{}`=%s LIMIT 1'.format(table, key)
        self.cursor.execute(sql, (value,))
        return self.cursor.fetchone() is not None

    def _insert(self, table, item):
        data = dict(item)
        for field, value in self.INSERT_DEFAULTS.get(table, {}).items():
            data.setdefault(field, value)
        fields = list(data)
        field_sql = ','.join('`{}`'.format(field) for field in fields)
        placeholders = ','.join(['%s'] * len(fields))
        values = tuple(self._clean_value(data[field]) for field in fields)
        sql = 'INSERT INTO `{}` ({}) VALUES ({})'.format(
            table, field_sql, placeholders
        )
        self.cursor.execute(sql, values)

    def _update(self, table, key, item):
        data = dict(item)
        key_value = self._clean_value(data.pop(key))
        if not data:
            return
        fields = list(data)
        assignments = ','.join('`{}`=%s'.format(field) for field in fields)
        values = tuple(self._clean_value(data[field]) for field in fields)
        sql = 'UPDATE `{}` SET {} WHERE `{}`=%s'.format(
            table, assignments, key
        )
        self.cursor.execute(sql, values + (key_value,))

    def process_item(self, item, spider):
        target = next(
            (config for item_type, config in self.ITEM_TARGETS.items()
             if isinstance(item, item_type)),
            None,
        )
        if target is None:
            return item

        table, key, update_existing = target
        key_value = item.get(key)
        if key_value in (None, ''):
            raise DropItem('{} is required for {}'.format(key, table))

        try:
            exists = self._exists(table, key, key_value)
            if exists and update_existing:
                self._update(table, key, item)
            elif not exists:
                self._insert(table, item)
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            spider.logger.exception(
                'Failed to persist %s with %s=%s', table, key, key_value
            )
            raise
        return item


class CoverPipeline(ImagesPipeline):
    """Cache movie posters as bounded WebP files before database persistence."""

    @classmethod
    def from_crawler(cls, crawler):
        pipeline = super().from_crawler(crawler)
        settings = crawler.settings
        pipeline.public_prefix = settings.get(
            'POSTER_PUBLIC_PREFIX', '/api/media/covers'
        ).rstrip('/')
        pipeline.poster_size = (
            settings.getint('POSTER_MAX_WIDTH', 360),
            settings.getint('POSTER_MAX_HEIGHT', 540),
        )
        pipeline.webp_quality = settings.getint('POSTER_WEBP_QUALITY', 82)
        pipeline.max_bytes = settings.getint(
            'POSTER_MAX_BYTES', 5 * 1024 * 1024
        )
        return pipeline

    def process_item(self, item, spider):
        if not isinstance(item, MovieMeta):
            return item
        source_url = item.get('cover_source_url') or item.get('cover') or ''
        if source_url.startswith(('http://', 'https://')):
            item['cover_source_url'] = source_url
        return super().process_item(item, spider)

    def file_path(self, request, response=None, info=None, *, item=None):
        image_guid = hashlib.sha256(to_bytes(request.url)).hexdigest()
        return '{}/{}/{}.webp'.format(
            image_guid[:2], image_guid[2:4], image_guid,
        )

    def get_media_requests(self, item, info):
        source_url = item.get('cover_source_url') or item.get('cover') or ''
        if not source_url.startswith(('http://', 'https://')):
            return []
        douban_id = item.get('douban_id', '')
        hostname = (urlsplit(source_url).hostname or '').lower()
        if hostname == 'image.tmdb.org':
            referer = 'https://www.themoviedb.org/'
        else:
            referer = 'https://movie.douban.com/subject/{}/'.format(douban_id)
        return [Request(
            source_url,
            headers={'Referer': referer},
            errback=self._poster_download_failed,
            meta={
                'allow_offsite': True,
                'download_maxsize': self.max_bytes,
                'download_warnsize': self.max_bytes,
            },
            dont_filter=True,
        )]

    @staticmethod
    def _poster_download_failed(failure):
        return {
            'poster_error': failure.getErrorMessage()[:255],
        }

    def media_failed(self, failure, request, info):
        message = failure.getErrorMessage() or 'poster download failed'
        info.spider.logger.warning(
            'Poster download skipped for %s: %s', request.url, message
        )
        return {'poster_error': message[:255]}

    def convert_image(self, image, size=None, *, response_body):
        from PIL import ImageOps

        image = ImageOps.exif_transpose(image)
        if image.mode in ('RGBA', 'LA', 'P'):
            foreground = image.convert('RGBA')
            background = self._Image.new('RGBA', foreground.size, 'white')
            background.alpha_composite(foreground)
            image = background.convert('RGB')
        elif image.mode != 'RGB':
            image = image.convert('RGB')

        image.thumbnail(self.poster_size, self._Image.Resampling.LANCZOS)
        buffer = BytesIO()
        image.save(
            buffer,
            'WEBP',
            quality=self.webp_quality,
            method=6,
        )
        return image, buffer

    def image_downloaded(self, response, request, info, *, item=None):
        checksum = None
        for path, image, buffer in self.get_images(
            response, request, info, item=item
        ):
            payload = buffer.getvalue()
            if checksum is None:
                checksum = hashlib.md5(
                    payload, usedforsecurity=False
                ).hexdigest()
            buffer.seek(0)
            width, height = image.size
            self.store.persist_file(
                path,
                buffer,
                info,
                meta={'width': width, 'height': height},
                headers={'Content-Type': 'image/webp'},
            )
        if checksum is None:
            raise ValueError('Poster conversion produced no image')
        return checksum

    def item_completed(self, results, item, info):
        item['cover_checked_at'] = datetime.now(timezone.utc).replace(tzinfo=None)
        successful = [
            result for ok, result in results
            if ok and isinstance(result, dict) and result.get('path')
        ]
        if successful:
            result = successful[0]
            path = result['path'].replace('\\', '/')
            checksum = result.get('checksum') or ''
            item['cover_path'] = path
            item['cover_hash'] = checksum
            item['cover_status'] = 'ok'
            item['cover_error'] = ''
            item['cover'] = '{}/{}?v={}'.format(
                self.public_prefix,
                quote(path, safe='/'),
                checksum[:12],
            )
            return item

        source_url = item.get('cover_source_url') or ''
        previous_path = item.get('cover_path') or ''
        item['cover_status'] = 'stale' if previous_path else (
            'failed' if source_url else 'missing'
        )
        failures = [value for ok, value in results if not ok]
        failures.extend(
            result.get('poster_error', '')
            for ok, result in results
            if ok and isinstance(result, dict) and result.get('poster_error')
        )
        message = ''
        if failures:
            failure = failures[0]
            message = getattr(
                failure, 'getErrorMessage', lambda: str(failure)
            )()
        item['cover_error'] = (message or 'poster download failed')[:255]
        if previous_path:
            previous_hash = item.get('cover_hash') or ''
            version = '?v={}'.format(previous_hash[:12]) if previous_hash else ''
            item['cover'] = '{}/{}{}'.format(
                self.public_prefix,
                quote(previous_path.replace('\\', '/'), safe='/'),
                version,
            )
        else:
            item['cover'] = source_url
        return item
