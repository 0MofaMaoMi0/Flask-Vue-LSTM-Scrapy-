import unittest
import json
import os
from unittest.mock import patch

from douban.items import Comment, MovieMeta, PersonMeta, Subject
from douban.spiders.movie_comment import MovieCommentSpider
from douban.spiders.movie_meta import MovieMetaSpider
from douban.spiders.movie_subject import MovieSubjectSpider
from douban.spiders.movie_cover_tmdb import MovieCoverTmdbSpider
from douban.spiders.person_meta import PersonMetaSpider
from scrapy import Request
from scrapy.exceptions import NotConfigured
from scrapy.http import HtmlResponse, TextResponse


def make_response(url, html, meta=None):
    request = Request(url, meta=meta or {})
    return HtmlResponse(
        url=url,
        body=html.encode('utf-8'),
        encoding='utf-8',
        request=request,
    )


class SpiderParsingTests(unittest.TestCase):
    def test_tmdb_cover_spider_requires_api_token(self):
        with patch.dict(os.environ, {'TMDB_API_READ_TOKEN': ''}):
            with self.assertRaises(NotConfigured):
                MovieCoverTmdbSpider()

    def test_tmdb_cover_spider_resolves_imdb_poster(self):
        with patch.dict(os.environ, {
            'TMDB_API_READ_TOKEN': 'test-token',
            'TMDB_LANGUAGE': 'zh-CN',
            'TMDB_IMAGE_SIZE': 'w500',
        }):
            spider = MovieCoverTmdbSpider()
        request = Request('https://api.themoviedb.org/3/find/tt0111161')
        response = TextResponse(
            url=request.url,
            body=json.dumps({
                'movie_results': [
                    {'id': 278, 'poster_path': '/poster-test.jpg'},
                ],
            }).encode('utf-8'),
            encoding='utf-8',
            request=request,
        )
        row = {
            'douban_id': '1292052',
            'imdb_id': 'tt0111161',
            'name': '肖申克的救赎',
            'year': 1994,
            'cover': 'https://img.example/douban.jpg',
            'cover_path': '',
            'cover_hash': '',
        }

        items = list(spider.parse_find(response, row))

        self.assertEqual(1, len(items))
        self.assertEqual(
            'https://image.tmdb.org/t/p/w500/poster-test.jpg',
            items[0]['cover_source_url'],
        )
        self.assertEqual('1292052', items[0]['douban_id'])

    def test_tmdb_cover_spider_falls_back_to_name_and_year(self):
        with patch.dict(os.environ, {
            'TMDB_API_READ_TOKEN': 'test-token',
            'TMDB_LANGUAGE': 'zh-CN',
            'TMDB_IMAGE_SIZE': 'w500',
        }):
            spider = MovieCoverTmdbSpider()
        row = {
            'douban_id': '1292052',
            'imdb_id': '',
            'name': '肖申克的救赎',
            'year': 1994,
            'cover': 'https://img.example/douban.jpg',
            'cover_path': '',
            'cover_hash': '',
        }

        request = spider._request_for_row(row)

        self.assertIn('/3/search/multi?', request.url)
        self.assertIn('query=', request.url)
        self.assertNotIn('year=', request.url)
        self.assertNotIn('test-token', request.url)
        self.assertEqual(b'Bearer test-token', request.headers['Authorization'])

        response = TextResponse(
            url=request.url,
            body=json.dumps({
                'results': [
                    {
                        'id': 278,
                        'title': '肖申克的救赎',
                        'release_date': '1994-09-23',
                        'poster_path': '/poster-test.jpg',
                    },
                ],
            }).encode('utf-8'),
            encoding='utf-8',
            request=request,
        )
        items = list(spider.parse_search(response, row))

        self.assertEqual(1, len(items))
        self.assertEqual(
            'https://image.tmdb.org/t/p/w500/poster-test.jpg',
            items[0]['cover_source_url'],
        )

        row['year'] = 0
        self.assertIsNone(spider._request_for_row(row))

    def test_tmdb_cover_spider_rejects_same_year_different_title(self):
        with patch.dict(os.environ, {
            'TMDB_API_READ_TOKEN': 'test-token',
            'TMDB_LANGUAGE': 'zh-CN',
            'TMDB_IMAGE_SIZE': 'w500',
        }):
            spider = MovieCoverTmdbSpider()
        row = {
            'douban_id': '6087553',
            'imdb_id': '',
            'name': '泰坦尼克号',
            'year': 2012,
            'cover': 'https://img.example/douban.jpg',
            'cover_path': '',
            'cover_hash': '',
        }
        request = spider._request_for_row(row)
        response = TextResponse(
            url=request.url,
            body=json.dumps({
                'results': [
                    {
                        'id': 12345,
                        'title': '泰坦尼克号：结案',
                        'release_date': '2012-04-05',
                        'poster_path': '/wrong-poster.jpg',
                    },
                ],
            }).encode('utf-8'),
            encoding='utf-8',
            request=request,
        )

        self.assertEqual([], list(spider.parse_search(response, row)))

    def test_tmdb_cover_spider_prefers_same_year_when_title_matches(self):
        with patch.dict(os.environ, {'TMDB_API_READ_TOKEN': 'test-token'}):
            spider = MovieCoverTmdbSpider()
        row = {
            'douban_id': '1292722',
            'imdb_id': '',
            'name': '泰坦尼克号',
            'year': 1997,
            'cover': '',
            'cover_path': '',
            'cover_hash': '',
        }
        request = spider._request_for_row(row)
        response = TextResponse(
            url=request.url,
            body=json.dumps({
                'results': [
                    {
                        'id': 1,
                        'title': '泰坦尼克号',
                        'release_date': '1943-01-01',
                        'poster_path': '/old.jpg',
                    },
                    {
                        'id': 2,
                        'title': '泰坦尼克号',
                        'release_date': '1997-12-18',
                        'poster_path': '/new.jpg',
                    },
                ],
            }).encode('utf-8'),
            encoding='utf-8',
            request=request,
        )

        items = list(spider.parse_search(response, row))

        self.assertEqual(1, len(items))
        self.assertEqual(
            'https://image.tmdb.org/t/p/w500/new.jpg',
            items[0]['cover_source_url'],
        )

    def test_subject_spider_starts_explore_api_with_bounded_page_size(self):
        response = make_response(
            'https://movie.douban.com/explore',
            '<div id="app"></div>',
        )

        results = list(MovieSubjectSpider(max_pages=2, page_size=50).parse(response))

        self.assertEqual(1, len(results))
        self.assertIsInstance(results[0], Request)
        self.assertIn('/rexxar/api/v2/movie/recommend?', results[0].url)
        self.assertIn('count=20', results[0].url)
        self.assertEqual(1, results[0].meta['page'])

    def test_subject_spider_filters_api_items_and_requests_next_page(self):
        spider = MovieSubjectSpider(max_pages=2)
        request = Request(
            spider.recommend_api,
            meta={'page': 1},
        )
        response = TextResponse(
            url=spider.recommend_api,
            body=json.dumps({
                'items': [
                    {'id': '10001', 'type': 'movie'},
                    {'id': '20001', 'type': 'playlist'},
                    {'id': '10001', 'type': 'movie'},
                    {'id': 'invalid', 'type': 'movie'},
                ],
            }).encode('utf-8'),
            encoding='utf-8',
            request=request,
        )

        results = list(spider.parse_recommend(response))

        items = [result for result in results if isinstance(result, Subject)]
        requests = [result for result in results if isinstance(result, Request)]
        self.assertEqual(['10001'], [item['douban_id'] for item in items])
        self.assertEqual(1, len(requests))
        self.assertEqual(2, requests[0].meta['page'])

    def test_subject_spider_extracts_unique_ids_without_following_links(self):
        response = make_response(
            'https://movie.douban.com/cinema/nowplaying/beijing/',
            '''
            <a href="/subject/10001/">A</a>
            <a href="https://movie.douban.com/subject/10001/">duplicate</a>
            <a href="/subject/10002/">B</a>
            ''',
        )

        items = list(MovieSubjectSpider().parse(response))

        self.assertEqual(['10001', '10002'], [item['douban_id'] for item in items])
        self.assertTrue(all(isinstance(item, Subject) for item in items))

    def test_movie_meta_spider_extracts_core_fields_and_foreign_names(self):
        response = make_response(
            'https://movie.douban.com/subject/1292052/',
            '''
            <html><head><title>The Shawshank Redemption (豆瓣)</title></head>
            <body>
              <div id="mainpic"><img rel="v:image" src="https://img.test/cover.jpg"></div>
              <div id="info">
                <span class="year">(1994)</span>
                <a rel="v:directedBy" href="/celebrity/1047973/">Frank Darabont</a>
                <a rel="v:starring" href="/celebrity/1054521/">Tim Robbins</a>
                <span>制片国家/地区:</span> 美国
                <span>语言:</span> 英语
                <span>又名:</span> 月黑高飞 / 刺激1995
                <span>IMDb链接:</span><a href="https://www.imdb.com/title/tt0111161/">IMDb</a>
              </div>
              <span property="v:genre">剧情</span>
              <span property="v:runtime" content="142"></span>
              <span property="v:initialReleaseDate" content="1994-09-10"></span>
              <strong property="v:average">9.7</strong>
              <span property="v:votes">3000000</span>
              <span property="v:summary">A story.</span>
              <div class="tags-body"><a>经典</a></div>
            </body></html>
            ''',
            {'douban_id': '1292052'},
        )

        item = MovieMetaSpider().parse(response)

        self.assertIsInstance(item, MovieMeta)
        self.assertEqual('The Shawshank Redemption', item['name'])
        self.assertEqual('Frank Darabont', item['directors'])
        self.assertEqual('Tim Robbins', item['actors'])
        self.assertEqual('Frank Darabont:1047973', item['director_ids'])
        self.assertEqual('Tim Robbins:1054521', item['actor_ids'])
        self.assertEqual('1994-09-10', item['release_date'])
        self.assertEqual('tt0111161', item['imdb_id'])

    def test_comment_spider_normalizes_rating_and_handles_class_spacing(self):
        response = make_response(
            'https://movie.douban.com/subject/1292052/comments?status=P',
            '''
            <div class="comment-item extra" data-cid="9001">
              <div class="avatar"><a title="测试用户" href="/people/u1/">
                <img src="https://img.test/u1.jpg"></a></div>
              <div class="comment"><h3><span class="comment-info">
                <span class="allstar50 rating"></span>
                <span class="comment-time" title="2026-10-15 10:20:30"></span>
              </span><span class="votes">12</span></h3>
              <p><span class="short">非常好看</span></p></div>
            </div>
            <a class="next" href="?start=20&amp;status=P">后页</a>
            ''',
            {'douban_id': '1292052', 'page': 1},
        )
        spider = MovieCommentSpider(max_pages=1)

        results = list(spider.parse(response))

        self.assertEqual(1, len(results))
        item = results[0]
        self.assertIsInstance(item, Comment)
        self.assertEqual('9001', item['douban_comment_id'])
        self.assertEqual('5', item['rating'])
        self.assertEqual(12, item['votes'])
        self.assertEqual('非常好看', item['content'])

    def test_person_spider_always_supplies_name(self):
        response = make_response(
            'https://movie.douban.com/celebrity/1054521/',
            '''
            <div id="content"><h1>Tim Robbins</h1></div>
            <div class="info"><ul>
              <li><span>性别</span>: 男</li>
              <li><span>出生日期</span>: 1958-10-16</li>
              <li><span>出生地</span>: 美国</li>
              <li><span>职业</span>: 演员 / 导演</li>
            </ul></div>
            <div id="intro"><span class="all hidden">人物简介</span></div>
            ''',
            {'person_id': '1054521', 'fallback_name': ''},
        )

        results = list(PersonMetaSpider().parse(response))

        self.assertEqual(1, len(results))
        item = results[0]
        self.assertIsInstance(item, PersonMeta)
        self.assertEqual('Tim Robbins', item['name'])
        self.assertEqual('1958-10-16', item['birth'])
        self.assertEqual('人物简介', item['biography'])


if __name__ == '__main__':
    unittest.main()

