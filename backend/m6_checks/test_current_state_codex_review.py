"""Independent regressions from Codex review; isolated SQLite only."""
import json
from datetime import datetime, timedelta, timezone
from catalog.m6_business import production_country_rows
from catalog.recommendation_data import _load_candidates
from m6_checks.test_current_state_cache import _catalog_engine, _row, _source, _meta, NOW


def test_unmapped_legacy_country_is_preserved():
    legacy=_source(11,1,{'production_countries':[{'iso_3166_1':'CN'}]},NOW)
    legacy['source']='legacy_movies2'
    engine=_catalog_engine([_row(1),_row(2)],[legacy,_source(22,2,{'id':1002},NOW)],mappings={2:1002},metadata=[_meta(2,22)])
    assert [c['iso2'] for c in production_country_rows(engine)[0]['countries']]==['CN']


def test_country_attribution_requires_source_owner():
    engine=_catalog_engine([_row(1),_row(2)],[_source(11,1,{'id':1001,'production_countries':[{'iso_3166_1':'US'}]},NOW),_source(22,2,{'id':1001,'production_countries':[{'iso_3166_1':'GB'}]},NOW)],mappings={1:1001,2:1002},metadata=[_meta(1,11,topics={'countries':{'source_record_pk':22}})])
    assert [c['iso2'] for c in production_country_rows(engine)[0]['countries']]==['US']


def test_wrong_identities_do_not_consume_revenue_candidate_limit():
    sources=[_source(11,1,{'id':1001,'revenue':100},NOW-timedelta(days=20)),_source(99,1,{'id':1001,'popularity':5},NOW)]
    sources += [_source(20+i,1,{'id':999,'revenue':200},NOW-timedelta(days=i+1)) for i in range(9)]
    engine=_catalog_engine([_row(1)],sources,mappings={1:1001},metadata=[_meta(1,99)])
    row=_load_candidates(engine)[0]
    assert row['revenue']==100
    assert datetime.fromisoformat(row['revenue_as_of'].replace('Z','+00:00'))==(NOW-timedelta(days=20)).replace(tzinfo=timezone.utc)


def test_current_metrics_survive_base_history_fallback():
    sources=[_source(11,1,{'id':999},NOW),_source(12,1,{'id':1001,'popularity':27},NOW),_source(13,1,{'id':1001,'popularity':999,'revenue':100},NOW+timedelta(days=1))]
    engine=_catalog_engine([_row(1)],sources,mappings={1:1001},metadata=[_meta(1,11,topics={'metrics':{'source_record_pk':12}})])
    row=_load_candidates(engine)[0]
    assert row['tmdb_popularity']==27
    assert row['revenue']==100


def test_explicit_empty_base_does_not_resurrect_historical_revenue():
    engine=_catalog_engine([_row(1)],[_source(11,1,{'id':1001,'revenue':100},NOW)],mappings={1:1001},metadata=[_meta(1,11,state='EMPTY')])
    assert _load_candidates(engine)[0]['revenue'] is None

def test_caller_cannot_mutate_nested_country_in_cached_snapshot():
    from catalog import public_catalog_cache as cache
    from catalog.recommendation_data import load_candidates
    engine=_catalog_engine([_row(1)],[_source(11,1,{'id':1001,'production_countries':[{'iso_3166_1':'US'}]},NOW)],mappings={1:1001},metadata=[_meta(1,11)])
    cache.invalidate(engine)
    first=load_candidates(engine)
    first[0]['production_countries'][0]['iso2']='ZZ'
    assert load_candidates(engine)[0]['production_countries'][0]['iso2']=='US'


def test_asset_path_swap_changes_watermark_even_without_timestamp_change():
    from sqlalchemy import text
    from catalog.public_catalog_cache import _watermarks
    assets=[dict(asset_id=1,movie_id=1,asset_type='poster',source='tmdb',source_path='/a.jpg',is_primary=True,license_status='allowed',updated_at=NOW),dict(asset_id=2,movie_id=1,asset_type='poster',source='tmdb',source_path='/b.jpg',is_primary=False,license_status='allowed',updated_at=NOW)]
    engine=_catalog_engine([_row(1)],[_source(11,1,{'id':1001},NOW)],mappings={1:1001},metadata=[_meta(1,11)],assets=assets)
    before=_watermarks(engine)
    with engine.begin() as c:c.execute(text("UPDATE movie_assets SET source_path=CASE WHEN asset_id=1 THEN '/b.jpg' ELSE '/a.jpg' END"))
    assert _watermarks(engine)!=before
