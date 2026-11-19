"""Synthetic tests for the poster proxy MIME/cache behavior.

No network, database, application factory, or project configuration is used.
The only project import is media_api, whose module import performs no I/O.
"""
import hashlib
from pathlib import Path
from unittest.mock import patch

import pytest
from flask import Flask

import media_api


JPEG = b'\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01'
PNG = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR'
GIF = b'GIF89a\x01\x00\x01\x00\x80\x00\x00'
WEBP = b'RIFF\x04\x00\x00\x00WEBPVP8 '
NOT_WEBP_RIFF = b'RIFF\x04\x00\x00\x00WAVEfmt '


class FakeResponse:
    def __init__(self, data, content_type='image/jpeg'):
        self._data = data
        self.headers = {'Content-Type': content_type}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size=-1):
        return self._data if size is None or size < 0 else self._data[:size]


def fake_upstream(payload, content_type='image/jpeg'):
    return patch.object(
        media_api.urllib.request,
        'urlopen',
        side_effect=lambda request, timeout: FakeResponse(payload, content_type),
    )


def no_upstream():
    return patch.object(
        media_api.urllib.request,
        'urlopen',
        side_effect=AssertionError('upstream must not be called for cache hits'),
    )


def cache_path(cache_root, url):
    digest = hashlib.sha256(url.encode('utf-8')).hexdigest()
    return cache_root / digest[:2] / (digest + '.img')


@pytest.fixture
def app(tmp_path):
    application = Flask(__name__)
    application.config.update(TESTING=True, MEDIA_CACHE_DIR=str(tmp_path / 'cache'))
    media_api.install(application)
    return application


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def cache_root(app):
    return Path(app.config['MEDIA_CACHE_DIR'])


@pytest.mark.parametrize(
    ('name', 'expected_type', 'payload'),
    [
        ('jpeg', 'image/jpeg', JPEG),
        ('png', 'image/png', PNG),
        ('gif', 'image/gif', GIF),
        ('webp', 'image/webp', WEBP),
    ],
)
def test_cold_and_warm_responses_use_the_same_canonical_type(
    client, cache_root, name, expected_type, payload
):
    url = f'https://image.tmdb.org/t/p/w342/{name}-poster.bin'
    target = cache_path(cache_root, url)

    with fake_upstream(payload) as network:
        cold = client.get('/api/media/poster', query_string={'u': url})

    assert cold.status_code == 200
    assert cold.mimetype == expected_type
    assert cold.headers['Cache-Control'] == 'public, max-age=86400'
    assert cold.data == payload
    assert target.read_bytes() == payload
    assert network.call_count == 1

    with no_upstream() as network:
        warm = client.get('/api/media/poster', query_string={'u': url})

    assert warm.status_code == 200
    assert warm.mimetype == expected_type
    assert warm.headers['Cache-Control'] == 'public, max-age=86400'
    assert warm.data == payload
    assert target.read_bytes() == payload
    network.assert_not_called()


def test_riff_container_without_webp_tag_is_rejected_and_not_cached(client, cache_root):
    url = 'https://image.tmdb.org/t/p/w342/not-webp.wav'
    with fake_upstream(NOT_WEBP_RIFF):
        response = client.get('/api/media/poster', query_string={'u': url})

    assert response.status_code == 415
    assert response.get_json()['code'] == 'NOT_AN_IMAGE'
    assert not cache_path(cache_root, url).exists()


def test_corrupt_cache_entry_is_rejected_without_rewriting(client, cache_root):
    url = 'https://image.tmdb.org/t/p/w342/corrupt.jpg'
    target = cache_path(cache_root, url)
    target.parent.mkdir(parents=True)
    corrupt = b'<html>not an image</html>'
    target.write_bytes(corrupt)

    with no_upstream() as network:
        response = client.get('/api/media/poster', query_string={'u': url})

    assert response.status_code == 415
    assert response.get_json()['code'] == 'NOT_AN_IMAGE'
    assert target.read_bytes() == corrupt
    network.assert_not_called()


def test_empty_cache_entry_is_rejected_without_rewriting(client, cache_root):
    url = 'https://image.tmdb.org/t/p/w342/empty.jpg'
    target = cache_path(cache_root, url)
    target.parent.mkdir(parents=True)
    target.write_bytes(b'')

    with no_upstream():
        response = client.get('/api/media/poster', query_string={'u': url})

    assert response.status_code == 415
    assert target.read_bytes() == b''


def test_oversized_cache_entry_is_rejected_without_rewriting(client, cache_root, monkeypatch):
    monkeypatch.setattr(media_api, 'MAX_BYTES', 16)
    url = 'https://image.tmdb.org/t/p/w342/oversized.jpg'
    target = cache_path(cache_root, url)
    target.parent.mkdir(parents=True)
    oversized = JPEG + b'x' * 17
    target.write_bytes(oversized)

    with no_upstream() as network:
        response = client.get('/api/media/poster', query_string={'u': url})

    assert response.status_code == 413
    assert response.get_json()['code'] == 'TOO_LARGE'
    assert target.read_bytes() == oversized
    network.assert_not_called()


def test_oversized_upstream_payload_is_rejected_and_not_cached(client, cache_root, monkeypatch):
    monkeypatch.setattr(media_api, 'MAX_BYTES', 16)
    url = 'https://image.tmdb.org/t/p/w342/oversized-upstream.jpg'
    with fake_upstream(JPEG + b'x' * 17):
        response = client.get('/api/media/poster', query_string={'u': url})

    assert response.status_code == 413
    assert response.get_json()['code'] == 'TOO_LARGE'
    assert not cache_path(cache_root, url).exists()


def test_invalid_upstream_bytes_are_rejected_and_not_cached(client, cache_root):
    url = 'https://image.tmdb.org/t/p/w342/invalid.jpg'
    with fake_upstream(b'not an image'):
        response = client.get('/api/media/poster', query_string={'u': url})

    assert response.status_code == 415
    assert response.get_json()['code'] == 'NOT_AN_IMAGE'
    assert not cache_path(cache_root, url).exists()


def test_non_image_upstream_content_type_is_rejected_and_not_cached(client, cache_root):
    url = 'https://image.tmdb.org/t/p/w342/text.jpg'
    with fake_upstream(JPEG, content_type='text/html'):
        response = client.get('/api/media/poster', query_string={'u': url})

    assert response.status_code == 415
    assert response.get_json()['code'] == 'NOT_AN_IMAGE'
    assert not cache_path(cache_root, url).exists()


@pytest.mark.parametrize(
    ('query', 'expected_code'),
    [
        ('', 'INVALID_INPUT'),
        ('https://evil.example/poster.jpg', 'HOST_NOT_ALLOWED'),
        ('ftp://image.tmdb.org/poster.jpg', 'HOST_NOT_ALLOWED'),
    ],
)
def test_original_allow_list_and_basic_validation_still_apply(client, query, expected_code):
    with no_upstream() as network:
        response = client.get('/api/media/poster', query_string={'u': query})

    assert response.status_code == 400
    assert response.get_json()['code'] == expected_code
    network.assert_not_called()