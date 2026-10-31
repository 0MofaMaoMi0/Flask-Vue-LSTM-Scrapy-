import unittest

from scrapy import Request
from scrapy.exceptions import NotConfigured

from douban.middlewares import EnvironmentCookieMiddleware
from douban.runtime_config import parse_cookie_header


class EnvironmentCookieMiddlewareTests(unittest.TestCase):
    def test_cookie_parser_preserves_equals_in_value(self):
        self.assertEqual(
            {'bid': 'abc==', 'ck': 'token'},
            parse_cookie_header('bid=abc==; ck=token'),
        )

    def test_cookie_is_only_added_to_movie_host(self):
        middleware = EnvironmentCookieMiddleware('bid=test; ck=token')
        movie_request = Request('https://movie.douban.com/subject/1292052/')
        image_request = Request('https://img1.doubanio.com/view/photo/test.jpg')

        middleware.process_request(movie_request, None)
        middleware.process_request(image_request, None)

        self.assertEqual({'bid': 'test', 'ck': 'token'}, movie_request.cookies)
        self.assertEqual({}, image_request.cookies)

    def test_non_http_request_is_ignored(self):
        middleware = EnvironmentCookieMiddleware('bid=test')
        request = Request('data:text/plain,1292052')

        middleware.process_request(request, None)

        self.assertEqual({}, request.cookies)

    def test_explicit_request_cookie_wins(self):
        middleware = EnvironmentCookieMiddleware('bid=environment')
        request = Request(
            'https://movie.douban.com/',
            cookies={'bid': 'request'},
        )

        middleware.process_request(request, None)

        self.assertEqual('request', request.cookies['bid'])

    def test_empty_cookie_disables_middleware(self):
        with self.assertRaises(NotConfigured):
            EnvironmentCookieMiddleware('')


if __name__ == '__main__':
    unittest.main()
