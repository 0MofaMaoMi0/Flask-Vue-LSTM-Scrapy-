"""Offline tests for the opt-in proxy transport in tmdb_supplement.capture.

No test here performs network I/O: every case stops in validation or inspects
the handlers of a freshly built opener.
"""
import http.client
import unittest
import urllib.request

from catalog.tmdb_supplement import capture
from catalog.tmdb_supplement.capture import (
    CaptureInputError,
    CaptureSecurityError,
    _build_opener,
    _proxy_map,
    make_urllib_fetch,
    urllib_fetch,
)

URL = ('https://api.themoviedb.org/3/movie/278?language=zh-CN'
       '&append_to_response=credits,keywords,release_dates')
HEADERS = {'Accept': 'application/json', 'Authorization': 'Bearer probe', 'User-Agent': 'mi2-test'}
PROXY = 'http://127.0.0.1:12334'


def request(**overrides):
    payload = {'url': URL, 'headers': dict(HEADERS), 'timeout': 15, 'max_bytes': 4096}
    payload.update(overrides)
    return payload


class ProxyMapTests(unittest.TestCase):
    def test_default_map_is_empty(self):
        self.assertEqual(_proxy_map(None), {})

    def test_proxy_map_uses_https_only(self):
        self.assertEqual(_proxy_map(PROXY), {'https': PROXY})

    def test_proxy_map_accepts_https_scheme_and_trailing_slash(self):
        self.assertEqual(_proxy_map('https://proxy.local:8443'), {'https': 'https://proxy.local:8443'})
        self.assertEqual(_proxy_map('http://proxy.local:8080/'), {'https': 'http://proxy.local:8080/'})

    def test_proxy_map_rejects_unsafe_values(self):
        for value in ('', 'socks5://127.0.0.1:1080', 'ftp://127.0.0.1', 'http://127.0.0.1/path',
                      'http://127.0.0.1?x=1', 'http://127.0.0.1#frag', 123, b'http://x'):
            with self.subTest(value=value):
                with self.assertRaises(CaptureInputError) as caught:
                    _proxy_map(value)
                self.assertEqual(caught.exception.code, 'PROXY_URL_INVALID')


class OpenerTests(unittest.TestCase):
    @staticmethod
    def proxies(opener):
        handlers = [h for h in opener.handlers if isinstance(h, urllib.request.ProxyHandler)]
        return handlers[0].proxies if handlers else None

    def test_default_opener_keeps_proxies_disabled(self):
        # An empty ProxyHandler is dropped by build_opener, so no proxy handler
        # exists at all: env and system proxies can never apply.
        self.assertIsNone(self.proxies(_build_opener({})))
        self.assertFalse([h for h in _build_opener({}).handlers
                          if isinstance(h, urllib.request.ProxyHandler)])

    def test_proxy_opener_configures_https_only(self):
        self.assertEqual(self.proxies(_build_opener({'https': PROXY})), {'https': PROXY})

    def test_opener_still_refuses_redirects(self):
        opener = _build_opener({})
        redirect = [h for h in opener.handlers if isinstance(h, urllib.request.HTTPRedirectHandler)]
        self.assertTrue(redirect)
        self.assertIsNone(redirect[0].redirect_request(None, None, 302, 'Found', {}, 'https://elsewhere.example'))


class TransportValidationTests(unittest.TestCase):
    def test_default_transport_still_validates(self):
        with self.assertRaises(CaptureSecurityError) as caught:
            urllib_fetch(request(url='https://api.themoviedb.org/3/movie/278?language=zh-CN'))
        self.assertEqual(caught.exception.code, 'URL_QUERY_NOT_ALLOWED')

    def test_proxy_transport_is_a_distinct_callable(self):
        self.assertIsNot(make_urllib_fetch(None), urllib_fetch)

    def test_proxy_transport_rejects_foreign_host_before_connecting(self):
        for url in ('https://evil.example/3/movie/278?language=zh-CN&append_to_response=credits,keywords,release_dates',
                    'http://api.themoviedb.org/3/movie/278?language=zh-CN&append_to_response=credits,keywords,release_dates',
                    'https://api.themoviedb.org/3/tv/278?language=zh-CN&append_to_response=credits,keywords,release_dates'):
            with self.subTest(url=url):
                with self.assertRaises(CaptureSecurityError):
                    make_urllib_fetch(PROXY)(request(url=url))

    def test_proxy_transport_rejects_header_and_budget_drift(self):
        cases = (
            (request(headers={'Accept': 'application/json'}), 'HEADERS_NOT_ALLOWED'),
            (request(timeout=0), 'TIMEOUT_INVALID'),
            (request(timeout=61.0), 'TIMEOUT_INVALID'),
            (request(max_bytes=capture.MAX_BODY_BYTES + 1), 'BODY_LIMIT_INVALID'),
            (request(max_bytes=0), 'BODY_LIMIT_INVALID'),
        )
        for payload, code in cases:
            with self.subTest(code=code):
                with self.assertRaises(CaptureSecurityError) as caught:
                    make_urllib_fetch(PROXY)(payload)
                self.assertEqual(caught.exception.code, code)

    def test_invalid_proxy_fails_at_construction(self):
        with self.assertRaises(CaptureInputError):
            make_urllib_fetch('socks5://127.0.0.1:1080')


class _FakeResponse:
    def __init__(self, status, body, headers=None, will_close=False):
        self.status = status
        self._body = body
        self._headers = list((headers or {'Content-Type': 'application/json'}).items())
        self.will_close = will_close

    def read(self, size=None):
        data, self._body = self._body[:size], self._body[size:]
        return data

    def getheaders(self):
        return list(self._headers)


class _FakeConnection:
    """Stands in for http.client.HTTPSConnection (no socket, no network)."""

    instances = []
    script = []

    def __init__(self, host, port, timeout=None, context=None):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.context = context
        self.tunnel = None
        self.requests = []
        self.connects = 0
        self.closed = 0
        self.sock = None
        _FakeConnection.instances.append(self)

    def set_tunnel(self, host, port=None):
        self.tunnel = (host, port)

    def connect(self):
        self.connects += 1

    def request(self, method, path, headers=None):
        self.requests.append((method, path, dict(headers or {})))
        action = _FakeConnection.script.pop(0) if _FakeConnection.script else None
        self._action = action

    def getresponse(self):
        action = getattr(self, '_action', None)
        if action == 'raise':
            raise http.client.RemoteDisconnected('closed')
        status, body, will_close = action or (200, b'{"ok":true}', False)
        return _FakeResponse(status, body, will_close=will_close)

    def close(self):
        self.closed += 1


class KeepAliveTransportTests(unittest.TestCase):
    def setUp(self):
        _FakeConnection.instances = []
        _FakeConnection.script = []
        self._real = capture.http.client.HTTPSConnection
        capture.http.client.HTTPSConnection = _FakeConnection

    def tearDown(self):
        capture.http.client.HTTPSConnection = self._real

    def test_requests_share_one_validated_tunnel(self):
        fetch = capture.make_keepalive_fetch(PROXY)
        first = fetch(request())
        second = fetch(request())
        self.assertEqual(first['status'], 200)
        self.assertEqual(second['body'], b'{"ok":true}')
        self.assertEqual(len(_FakeConnection.instances), 1)
        connection = _FakeConnection.instances[0]
        self.assertEqual((connection.host, connection.port), ('127.0.0.1', 12334))
        self.assertEqual(connection.tunnel, ('api.themoviedb.org', 443))
        self.assertEqual([path for _, path, _ in connection.requests][0],
                         '/3/movie/278?language=zh-CN'
                         '&append_to_response=credits,keywords,release_dates')
        self.assertEqual(connection.requests[0][2]['Host'], 'api.themoviedb.org')

    def test_peer_close_reopens_the_tunnel(self):
        _FakeConnection.script = [(200, b'{}', True), (200, b'{}', True)]
        fetch = capture.make_keepalive_fetch(PROXY)
        fetch(request())
        fetch(request())
        self.assertEqual(len(_FakeConnection.instances), 2)
        self.assertEqual(_FakeConnection.instances[0].closed, 1)

    def test_stale_tunnel_is_retried_once(self):
        _FakeConnection.script = ['raise', (200, b'{"ok":1}', False)]
        fetch = capture.make_keepalive_fetch(PROXY)
        result = fetch(request())
        self.assertEqual(result['body'], b'{"ok":1}')
        self.assertEqual(len(_FakeConnection.instances), 2)
        self.assertEqual(_FakeConnection.instances[0].closed, 1)

    def test_second_failure_surfaces_transport_error(self):
        _FakeConnection.script = ['raise', 'raise']
        fetch = capture.make_keepalive_fetch(PROXY)
        with self.assertRaises(capture.CaptureError) as caught:
            fetch(request())
        self.assertEqual(caught.exception.code, 'TRANSPORT_ERROR')
        self.assertEqual(len(_FakeConnection.instances), 2)

    def test_foreign_url_never_opens_a_connection(self):
        fetch = capture.make_keepalive_fetch(PROXY)
        with self.assertRaises(CaptureSecurityError):
            fetch(request(url=URL.replace('api.themoviedb.org', 'evil.example')))
        self.assertEqual(_FakeConnection.instances, [])

    def test_body_cap_closes_the_tunnel(self):
        _FakeConnection.script = [(200, b'x' * 64, False)]
        fetch = capture.make_keepalive_fetch(PROXY)
        with self.assertRaises(capture.ResponseTooLarge):
            fetch(request(max_bytes=8))
        self.assertEqual(_FakeConnection.instances[0].closed, 1)


if __name__ == '__main__':
    unittest.main()