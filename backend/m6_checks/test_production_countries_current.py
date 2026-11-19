"""Production-countries current-state aggregation (dedupe, fallback, denominator)."""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text

from catalog.m6_business import production_country_rows
from m6_checks.test_current_state_cache import NOW, _catalog_engine, _meta, _row, _source


def _engine():
    rows = [_row(1), _row(2), _row(3)]
    sources = [
        _source(11, 1, {"id": 1001, "production_countries": [
            {"iso_3166_1": "US", "name": "United States of America"},
            {"iso_3166_1": "US", "name": "United States of America"},
            {"iso_3166_1": "GB", "name": "United Kingdom"},
        ]}, NOW),
        _source(12, 1, {"id": 1001, "production_countries": [
            {"iso_3166_1": "FR", "name": "France"}]}, NOW - timedelta(days=5)),
        _source(21, 2, {"id": 1002, "production_countries": [
            {"iso_3166_1": "JP", "name": "Japan"}]}, NOW),
    ]
    return _catalog_engine(rows, sources, mappings={1: 1001, 2: 1002, 3: 1003},
                           metadata=[_meta(1, 11)])


def test_current_state_countries_win_over_history():
    items = {item["movie_id"]: item for item in production_country_rows(_engine())}
    assert [country["iso2"] for country in items[1]["countries"]] == ["US", "GB"]


def test_movie_without_metadata_keeps_legacy_record():
    items = {item["movie_id"]: item for item in production_country_rows(_engine())}
    assert [country["iso2"] for country in items[2]["countries"]] == ["JP"]


def test_movie_without_any_source_still_listed():
    items = {item["movie_id"]: item for item in production_country_rows(_engine())}
    assert set(items) == {1, 2, 3}
    assert items[3]["countries"] == []

def test_countries_topic_wins_over_base_payload():
    """Probe 2: the countries capture is authoritative, not the base payload."""
    rows = [_row(1)]
    sources = [
        _source(11, 1, {"id": 1001, "production_countries": [
            {"iso_3166_1": "US", "name": "United States of America"}]}, NOW),
        _source(12, 1, {"id": 1001, "production_countries": [
            {"iso_3166_1": "GB", "name": "United Kingdom"}]}, NOW - timedelta(days=2)),
    ]
    topics = {"base": {"source_record_pk": 11},
              "countries": {"source_record_pk": 12},
              "metrics": {"source_record_pk": 11}}
    engine = _catalog_engine(rows, sources, mappings={1: 1001},
                             metadata=[_meta(1, 11, topics=topics)])
    items = {item["movie_id"]: item for item in production_country_rows(engine)}
    assert [country["iso2"] for country in items[1]["countries"]] == ["GB"]


def test_empty_countries_topic_does_not_fall_back():
    rows = [_row(1)]
    sources = [
        _source(11, 1, {"id": 1001, "production_countries": [
            {"iso_3166_1": "US", "name": "United States of America"}]}, NOW),
        _source(12, 1, {"id": 1001, "production_countries": [
            {"iso_3166_1": "FR", "name": "France"}]}, NOW - timedelta(days=2)),
    ]
    topics = {"base": {"source_record_pk": 11},
              "countries": {"state": "EMPTY", "source_record_pk": 12},
              "metrics": {"source_record_pk": 11}}
    engine = _catalog_engine(rows, sources, mappings={1: 1001},
                             metadata=[_meta(1, 11, topics=topics)])
    items = {item["movie_id"]: item for item in production_country_rows(engine)}
    assert items[1]["countries"] == []
