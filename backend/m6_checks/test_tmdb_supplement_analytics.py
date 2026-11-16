"""Offline fixture tests for the TMDB supplement v1 analytics kernel.

All documents below are synthetic fixtures built for the documented
``tmdb-supplement-v1`` shape.  Nothing here reads a database, the network,
configuration or a real catalog, and no expected count is copied from a real
library; every assertion is derived from the fixture itself.
"""
import importlib.util
import json
from pathlib import Path

import pytest

MODULE_PATH = (Path(__file__).resolve().parents[1]
               / 'catalog' / 'tmdb_supplement' / 'analytics.py')
_SPEC = importlib.util.spec_from_file_location('tmdb_supplement_analytics', MODULE_PATH)
analytics = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(analytics)

aggregate_movies = analytics.aggregate_movies
AnalyticsInputError = analytics.AnalyticsInputError
TOPICS = analytics.TOPICS


# --------------------------------------------------------------- fixtures

def make_document(movie_id, fetched_at='2026-11-01T00:00:00Z', *, tmdb_id=None,
                  title=None, countries=None, genres=None, rating=8.0, votes=100,
                  popularity=5.0, keywords=None, companies=None, languages=None,
                  original_language='zh', credits=None, releases=None,
                  release_date='1994-06-01', status=None):
    facts = {
        'base': {
            'id': tmdb_id or movie_id,
            'title': title or 'Movie %d' % movie_id,
            'original_title': title or 'Movie %d' % movie_id,
            'release_date': release_date,
            'original_language': original_language,
            'genres': genres if genres is not None else [{'id': 18, 'name': '剧情'}],
            'runtime': 100,
            'overview': 'synthetic overview',
            'poster_path': '/poster.jpg',
            'backdrop_path': '/backdrop.jpg',
            'budget': 0,
            'revenue': 0,
        },
        'countries': (countries if countries is not None
                      else [{'iso_3166_1': 'CN', 'name': 'China'}]),
        'companies': (companies if companies is not None
                      else [{'id': 1, 'name': 'Studio', 'origin_country': 'US'}]),
        'spoken_languages': (languages if languages is not None
                             else [{'iso_639_1': 'zh', 'english_name': 'Chinese'}]),
        'keywords': (keywords if keywords is not None
                     else [{'id': 1, 'name': '科幻'}]),
        'releases': releases if releases is not None else [],
        'credits': credits if credits is not None else [],
        'metrics': {'vote_average': rating, 'vote_count': votes,
                    'popularity': popularity},
    }
    field_status = {topic: 'PRESENT' for topic in TOPICS}
    if status:
        field_status.update(status)
    return {
        'schema_version': analytics.SCHEMA_VERSION,
        'movie_id': movie_id,
        'tmdb_id': tmdb_id or movie_id * 10,
        'fetched_at': fetched_at,
        'job_id': 'job-fixture',
        'payload_sha256': ('%064x' % (movie_id * 7 + 1)),
        'field_status': field_status,
        'facts': facts,
        'issues': [],
    }


def issue_codes(result):
    return {issue['code'] for issue in result['issues']}


def deep_row(result, iso2):
    for row in result['country_analysis']['rows']:
        if row['country_iso2'] == iso2:
            return row
    raise AssertionError('missing deep row for ' + iso2)


def country_rows(result):
    return {row['country_iso2']: row for row in result['countries']['rows']}


# ------------------------------------------------- co-production / country

def test_co_production_counts_each_country_and_shares_may_exceed_one():
    documents = [
        make_document(1, countries=[{'iso_3166_1': 'CN', 'name': 'China'},
                                    {'iso_3166_1': 'HK', 'name': 'Hong Kong'}]),
        make_document(2, countries=[{'iso_3166_1': 'CN', 'name': 'China'},
                                    {'iso_3166_1': 'HK', 'name': 'Hong Kong'}]),
    ]
    result = aggregate_movies(documents, countries=['CN', 'HK'])
    rows = country_rows(result)
    assert rows['CN']['movie_count_distinct'] == 2
    assert rows['HK']['movie_count_distinct'] == 2
    assert rows['CN']['share_of_filtered_catalog'] == 1.0
    assert rows['HK']['share_of_filtered_catalog'] == 1.0
    assert (rows['CN']['share_of_filtered_catalog']
            + rows['HK']['share_of_filtered_catalog']) == 2.0
    assert result['countries']['denominator']['movies'] == 2
    comparison = result['comparison']
    assert comparison['union_movie_count'] == 2
    assert comparison['overlap_movie_count'] == 2
    assert comparison['pairwise'][0]['intersection_movie_count'] == 2
    assert comparison['pairwise'][0]['jaccard'] == 1.0


def test_company_origin_country_and_languages_never_become_production_country():
    documents = [
        make_document(
            1,
            countries=[{'iso_3166_1': 'CN', 'name': 'China'}],
            companies=[{'id': 9, 'name': 'US Studio', 'origin_country': 'US'}],
            languages=[{'iso_639_1': 'en', 'english_name': 'English'}],
            original_language='en',
        ),
    ]
    result = aggregate_movies(documents)
    rows = country_rows(result)
    assert sorted(rows) == ['CN']
    assert result['coverage']['countries']['known'] == 1
    # The company/language hints stay visible but are not a country fact.
    assert deep_row(aggregate_movies(documents, countries=['CN']), 'CN')[
        'companies'][0]['origin_country'] == 'US'


def test_unknown_country_is_a_distinct_state_from_known_country():
    documents = [
        make_document(1),
        make_document(2, countries=[]),
    ]
    result = aggregate_movies(documents)
    assert result['states']['countries'] == 'PARTIAL'
    assert result['coverage']['countries'] == {
        'known': 1, 'unknown': 1, 'share_known': 0.5,
        'topic_status': {'NOT_FETCHED': 0, 'MISSING': 0, 'EMPTY': 0,
                         'PRESENT': 2, 'FAILED': 0},
    }
    rows = country_rows(result)
    assert sorted(rows) == ['CN']
    assert rows['CN']['movie_count_distinct'] == 1
    assert rows['CN']['share_of_filtered_catalog'] == 0.5
    assert 'COUNTRY_METADATA_UNKNOWN' in issue_codes(result)

    all_unknown = aggregate_movies([make_document(3, countries=[])])
    assert all_unknown['states']['countries'] == 'NO_COUNTRY_METADATA'
    assert all_unknown['countries']['rows'] == []
    assert all_unknown['global']['movies_with_countries'] == 0
    assert all_unknown['global']['movies_country_unknown'] == 1


# --------------------------------------------------------------- emptiness

def test_no_documents_and_no_matches_are_reported_separately():
    empty = aggregate_movies([])
    assert empty['states']['catalog'] == 'NO_DOCUMENTS'
    assert empty['states']['countries'] == 'NO_DOCUMENTS'
    assert empty['states']['ratings'] == 'NO_DOCUMENTS'
    assert empty['scope']['documents_seen'] == 0
    assert empty['details']['total'] == 0
    assert empty['countries']['rows'] == []
    assert empty['global']['tmdb_avg_rating']['unweighted_mean'] is None
    assert empty['global']['tmdb_avg_rating']['valid_samples'] == 0

    excluded = aggregate_movies([make_document(1)], filters={'country': 'JP'})
    assert excluded['states']['catalog'] == 'NO_MATCHES_IN_SCOPE'
    assert excluded['states']['countries'] == 'NO_MATCHES_IN_SCOPE'
    assert excluded['states']['ratings'] == 'NO_MATCHES_IN_SCOPE'
    assert excluded['scope']['movies_total'] == 1
    assert excluded['scope']['movies_matched'] == 0
    assert excluded['scope']['excluded'] == 1
    assert excluded['details']['total'] == 0
    assert excluded['coverage']['movies'] == 0


# ------------------------------------------------------------------ ratings

def test_rating_gate_keeps_zero_score_and_excludes_low_or_zero_votes():
    documents = [
        make_document(1, rating=0.0, votes=100),   # valid 0.0 score
        make_document(2, rating=9.0, votes=10),    # below default gate
        make_document(3, rating=8.0, votes=0),     # zero votes
        make_document(4, rating=None, votes=None),  # metrics missing
    ]
    result = aggregate_movies(documents)
    summary = result['global']['tmdb_avg_rating']
    assert summary['valid_samples'] == 1
    assert summary['unweighted_mean'] == 0.0
    assert summary['vote_weighted_mean'] == 0.0
    assert summary['total_votes'] == 100
    assert summary['below_min_votes_movies'] == 2
    assert result['coverage']['ratings']['unknown'] == 3
    assert result['states']['ratings'] == 'PARTIAL'
    assert result['global']['top_rated_movies'][0]['movie_id'] == 1
    details = {item['movie_id']: item for item in result['details']['items']}
    assert details[1]['rating_sample_valid'] is True
    assert details[2]['rating_sample_valid'] is False
    assert details[4]['vote_average'] is None
    assert details[4]['rating_sample_valid'] is False

    relaxed = aggregate_movies(documents, min_votes=0)
    relaxed_summary = relaxed['global']['tmdb_avg_rating']
    assert relaxed_summary['valid_samples'] == 3
    assert relaxed_summary['unweighted_mean'] == 5.67
    assert relaxed_summary['vote_weighted_mean'] == 0.82
    assert relaxed_summary['unweighted_mean'] != relaxed_summary['vote_weighted_mean']


def test_movie_without_valid_rating_never_gets_a_fabricated_zero():
    result = aggregate_movies([make_document(1, rating=None, votes=None)])
    assert result['states']['ratings'] == 'NO_VALID_RATING_SAMPLE'
    assert result['global']['tmdb_avg_rating']['unweighted_mean'] is None
    assert result['global']['tmdb_avg_rating']['vote_weighted_mean'] is None
    assert result['details']['items'][0]['vote_average'] is None
    assert result['global']['top_rated_movies'] == []
    assert 'RATING_SAMPLE_INSUFFICIENT' in issue_codes(result)


# ------------------------------------------------------ duplicate documents

def test_newest_successful_fact_wins_and_failures_never_clear_valid_topics():
    old = make_document(7, '2026-10-01T00:00:00Z', countries=[
        {'iso_3166_1': 'CN', 'name': 'China'}], keywords=[{'id': 31, 'name': '旧关键词'}])
    new = make_document(7, '2026-11-01T00:00:00Z', countries=[
        {'iso_3166_1': 'HK', 'name': 'Hong Kong'}],
        status={'keywords': 'MISSING', 'metrics': 'FAILED'})
    result = aggregate_movies([old, new], countries=['HK'])
    assert result['scope']['movies_total'] == 1
    assert result['scope']['documents_used'] == 2
    assert result['scope']['duplicate_documents_merged'] == 1
    assert 'DUPLICATE_MOVIE_DOCUMENTS' in issue_codes(result)
    row = deep_row(result, 'HK')
    assert row['movie_count_distinct'] == 1
    # Newest successful country fact replaces the older one.
    assert 'CN' not in country_rows(result)
    # A MISSING keywords topic cannot erase the older valid keyword.
    assert row['keywords'] == [{'keyword_id': 31, 'name': '旧关键词', 'movie_count': 1}]
    # A FAILED metrics topic cannot erase the older valid rating sample.
    assert row['tmdb_avg_rating']['valid_samples'] == 1
    assert row['tmdb_avg_rating']['unweighted_mean'] == 8.0
    detail = result['details']['items'][0]
    # The effective topic status stays PRESENT because the older valid fact won.
    assert detail['topic_status']['keywords'] == 'PRESENT'
    assert detail['topic_status']['metrics'] == 'PRESENT'
    assert [point['observed_at'] for point in detail['rating_observations']] == [
        '2026-10-01T00:00:00Z']


def test_observed_at_cutoff_uses_only_documents_captured_at_or_before_it():
    old = make_document(7, '2026-10-01T00:00:00Z', countries=[
        {'iso_3166_1': 'CN', 'name': 'China'}])
    new = make_document(7, '2026-11-01T00:00:00Z', countries=[
        {'iso_3166_1': 'HK', 'name': 'Hong Kong'}])
    cutoff = aggregate_movies([old, new], observed_at='2026-10-15T00:00:00Z')
    assert cutoff['scope']['documents_seen'] == 2
    assert cutoff['scope']['documents_skipped_after_observed_at'] == 1
    assert cutoff['scope']['documents_used'] == 1
    assert sorted(country_rows(cutoff)) == ['CN']
    assert cutoff['snapshot']['observed_at'] == '2026-10-15T00:00:00Z'

    too_early = aggregate_movies([old], observed_at='2026-09-01T00:00:00Z')
    assert too_early['states']['catalog'] == 'NO_DOCUMENTS_AT_OBSERVED_AT'
    assert too_early['states']['countries'] == 'NO_DOCUMENTS_AT_OBSERVED_AT'
    assert too_early['scope']['documents_seen'] == 1
    assert too_early['scope']['movies_total'] == 0


# ------------------------------------------------------------- time filters

def test_fact_year_and_capture_time_filters_are_never_mixed():
    document = make_document(5, '2026-11-01T00:00:00Z', release_date='1994-06-01')
    by_decade = aggregate_movies([document], filters={'decade': '1990s'})
    assert by_decade['scope']['movies_matched'] == 1
    assert by_decade['filters']['decades'] == [1990]

    by_year = aggregate_movies([document], filters={'year_from': 2020})
    assert by_year['scope']['movies_matched'] == 0
    assert by_year['scope']['excluded'] == 1

    by_capture = aggregate_movies([document],
                                  filters={'observed_from': '2026-01-01T00:00:00Z'})
    assert by_capture['scope']['movies_matched'] == 1
    none_captured = aggregate_movies([document],
                                     filters={'observed_from': '2027-01-01T00:00:00Z'})
    assert none_captured['scope']['movies_matched'] == 0

    semantics = by_capture['filter_semantics']
    assert semantics['decades']['kind'] == 'fact'
    assert semantics['year_from']['kind'] == 'fact'
    assert semantics['observed_from']['kind'] == 'capture'
    assert semantics['observed_from']['timezone'] == 'UTC'
    assert by_capture['filters']['observed_from'] == '2026-01-01T00:00:00Z'
    assert by_capture['filters']['year_from'] is None


def test_release_year_unknown_does_not_match_year_filters_but_is_counted():
    result = aggregate_movies([make_document(1, release_date='')],
                              filters={'year_from': 1900})
    assert result['scope']['movies_matched'] == 0
    assert result['scope']['excluded_unknown_release_year'] == 1
    assert result['scope']['excluded'] == 0


# ---------------------------------------------------------------- filters

@pytest.mark.parametrize('filters,message', [
    ({'director': 'x'}, 'Unknown filter field'),
    ({'country': 'CN', 'countries': ['CN']}, 'Conflicting filter fields'),
    ({'genre': '剧情', 'genres': ['剧情']}, 'Conflicting filter fields'),
    ({'year_from': True}, 'Invalid filter year_from'),
    ({'year_from': 2001, 'year_to': 1990}, 'Inverted year range'),
    ({'decade': 1995}, 'Invalid filter decade'),
    ({'decade': 'nineties'}, 'Invalid filter decade'),
    ({'country': 'CHN'}, 'Invalid filter country'),
    ({'country_known': 'yes'}, 'Invalid filter country_known'),
    ({'limit': 0}, 'Pagination bounds exceeded'),
    ({'limit': 101}, 'Pagination bounds exceeded'),
    ({'offset': 10001}, 'Pagination bounds exceeded'),
    ({'offset': -1}, 'Pagination bounds exceeded'),
    ({'top': 51}, 'Invalid top size'),
])
def test_invalid_filters_are_rejected(filters, message):
    with pytest.raises(AnalyticsInputError) as caught:
        aggregate_movies([make_document(1)], filters=filters)
    assert message in str(caught.value)


def test_valid_filters_select_genre_country_and_capture_window():
    documents = [
        make_document(1, genres=[{'id': 18, 'name': '剧情'}],
                      countries=[{'iso_3166_1': 'CN', 'name': 'China'}]),
        make_document(2, '2026-12-01T00:00:00Z', genres=[{'id': 35, 'name': '喜剧'}],
                      countries=[{'iso_3166_1': 'US', 'name': 'United States'}]),
    ]
    by_genre = aggregate_movies(documents, filters={'genre': '喜剧'})
    assert [item['movie_id'] for item in by_genre['details']['items']] == [2]
    by_genre_id = aggregate_movies(documents, filters={'genres': [18]})
    assert [item['movie_id'] for item in by_genre_id['details']['items']] == [1]
    by_country = aggregate_movies(documents, filters={'countries': ['us']})
    assert [item['movie_id'] for item in by_country['details']['items']] == [2]
    window = aggregate_movies(documents, filters={
        'observed_to': '2026-11-15T00:00:00Z'})
    assert [item['movie_id'] for item in window['details']['items']] == [1]
    known = aggregate_movies(documents, filters={'country_known': False})
    assert known['details']['total'] == 0


# -------------------------------------------------------------- comparison

def test_three_country_comparison_dedupes_overlap_and_union():
    documents = [
        make_document(1, countries=[{'iso_3166_1': 'CN', 'name': 'China'},
                                    {'iso_3166_1': 'HK', 'name': 'Hong Kong'}]),
        make_document(2, countries=[{'iso_3166_1': 'HK', 'name': 'Hong Kong'},
                                    {'iso_3166_1': 'JP', 'name': 'Japan'}]),
        make_document(3, countries=[{'iso_3166_1': 'CN', 'name': 'China'}]),
    ]
    result = aggregate_movies(documents, countries=['CN', 'HK', 'JP'])
    comparison = result['comparison']
    assert comparison['countries'] == ['CN', 'HK', 'JP']
    assert comparison['union_movie_count'] == 3
    assert comparison['overlap_movie_count'] == 2
    assert comparison['overlap_ratio_of_union'] == 0.6667
    pairs = {(tuple(pair['countries'])): pair for pair in comparison['pairwise']}
    assert pairs[('CN', 'HK')]['intersection_movie_count'] == 1
    assert pairs[('CN', 'HK')]['union_movie_count'] == 3
    assert pairs[('CN', 'HK')]['jaccard'] == 0.3333
    assert pairs[('CN', 'JP')]['intersection_movie_count'] == 0
    assert pairs[('HK', 'JP')]['intersection_movie_count'] == 1
    assert [row['country_iso2'] for row in result['country_analysis']['rows']] == [
        'CN', 'HK', 'JP']
    assert 'rows' not in comparison  # deep rows live once, in country_analysis
    for row in result['country_analysis']['rows']:
        assert row['share_of_filtered_catalog'] is not None

    unknown = aggregate_movies(documents, countries=['KR'])
    assert unknown['country_analysis']['rows'][0]['movie_count_distinct'] == 0
    assert 'COUNTRY_NOT_FOUND' in issue_codes(unknown)

    with pytest.raises(AnalyticsInputError) as caught:
        aggregate_movies(documents, countries=['CN', 'HK', 'JP', 'KR'])
    assert 'At most 3 countries' in str(caught.value)


# ------------------------------------------------------- missing topics

def test_missing_topics_and_credit_gaps_are_explicit():
    document = make_document(1, status={'credits': 'NOT_FETCHED',
                                        'keywords': 'MISSING',
                                        'releases': 'EMPTY'})
    result = aggregate_movies([document], countries=['CN'])
    assert result['coverage']['topics']['credits']['NOT_FETCHED'] == 1
    assert result['coverage']['topics']['keywords']['MISSING'] == 1
    assert result['coverage']['topics']['releases']['EMPTY'] == 1
    row = deep_row(result, 'CN')
    assert row['credits']['status'] == 'NOT_FETCHED'
    assert row['credits']['directors'] == []
    assert row['credits']['cast'] == []
    assert row['keywords'] == []
    assert row['release_window']['status'] == 'NO_RELEASE_DATA'
    assert row['release_window']['median_lag_days'] is None
    codes = issue_codes(result)
    assert {'CREDITS_UNAVAILABLE', 'RELEASES_UNAVAILABLE'} <= codes


def test_credits_use_only_reliable_credit_fields():
    credits = [
        {'role_type': 'cast', 'person_id': 11, 'person_name': 'Actor',
         'character': 'Hero', 'order': 0},
        {'role_type': 'crew', 'person_id': 12, 'person_name': 'Director',
         'job': 'Director', 'department': 'Directing'},
        {'role_type': 'crew', 'person_id': 13, 'person_name': 'Producer',
         'job': 'Producer', 'department': 'Production'},
        {'role_type': 'crew', 'job': 'Writer'},          # no person id
        {'person_id': 14, 'job': None, 'character': None},  # no role marker
    ]
    result = aggregate_movies([make_document(1, credits=credits)],
                              countries=['CN'])
    row = deep_row(result, 'CN')
    assert row['credits']['status'] == 'PRESENT'
    assert row['credits']['movies_with_credits'] == 1
    assert [item['person_id'] for item in row['credits']['directors']] == [12]
    assert [item['person_id'] for item in row['credits']['cast']] == [11]
    assert 'CREDIT_PERSON_MISSING' in issue_codes(result)
    assert 'CREDIT_ROLE_UNKNOWN' in issue_codes(result)


def test_keywords_are_grouped_by_tmdb_id_not_by_name():
    documents = [
        make_document(1, keywords=[{'id': 501, 'name': '科幻'}]),
        make_document(2, keywords=[{'id': 502, 'name': '科幻'}]),
    ]
    result = aggregate_movies(documents, countries=['CN'])
    rows = deep_row(result, 'CN')['keywords']
    assert sorted(row['keyword_id'] for row in rows) == [501, 502]
    assert all(row['movie_count'] == 1 for row in rows)
    assert result['coverage']['keywords']['movies_with_keywords'] == 2


def test_original_language_and_spoken_languages_stay_separate():
    result = aggregate_movies([
        make_document(1, original_language='zh',
                      languages=[{'iso_639_1': 'en', 'english_name': 'English'},
                                 {'iso_639_1': 'fr', 'english_name': 'French'}]),
    ])
    assert result['global']['original_language'] == [{'code': 'zh', 'count': 1}]
    spoken = {row['code'] for row in result['global']['spoken_languages']}
    assert spoken == {'en', 'fr'}
    assert 'zh' not in spoken
    single = aggregate_movies(
        [make_document(1, original_language='zh',
                       languages=[{'iso_639_1': 'en'}])], countries=['CN'])
    assert deep_row(single, 'CN')['languages']['original_language'] == [
        {'code': 'zh', 'count': 1}]


# --------------------------------------------- genre x decade / ranking

def test_genre_by_decade_matrix_and_top_lists_are_bounded():
    documents = [
        make_document(1, release_date='1994-06-01',
                      genres=[{'id': 18, 'name': '剧情'}]),
        make_document(2, release_date='1996-01-01',
                      genres=[{'id': 18, 'name': '剧情'}]),
        make_document(3, release_date='2001-03-01',
                      genres=[{'id': 35, 'name': '喜剧'}]),
    ]
    result = aggregate_movies(documents, countries=['CN'], filters={'top': 1})
    row = deep_row(result, 'CN')
    assert len(row['genre_by_decade']) == 1
    assert row['genre_by_decade'][0]['genre'] == '剧情'
    assert row['genre_by_decade'][0]['decades'] == [
        {'decade': 1990, 'count': 2}]
    assert len(row['top_movies']) == 1
    assert len(result['global']['top_genres']) == 1
    assert result['global']['decade_distribution'] == [
        {'decade': 1990, 'name': '1990年代', 'count': 2},
        {'decade': 2000, 'name': '2000年代', 'count': 1},
    ]


def test_company_ranking_keeps_origin_country_as_attribute_only():
    documents = [
        make_document(1, companies=[{'id': 70, 'name': 'Alpha',
                                     'origin_country': 'US'},
                                    {'id': 71, 'name': 'Beta',
                                     'origin_country': 'JP'}]),
        make_document(2, companies=[{'id': 70, 'name': 'Alpha',
                                     'origin_country': 'US'}]),
    ]
    result = aggregate_movies(documents, countries=['CN'])
    companies = deep_row(result, 'CN')['companies']
    assert companies[0] == {'company_id': 70, 'name': 'Alpha',
                            'origin_country': 'US', 'movie_count': 2}
    assert companies[1]['movie_count'] == 1


def test_release_window_uses_real_release_records_only():
    releases = [
        {'iso_3166_1': 'JP', 'release_dates': [
            {'release_date': '1994-05-01', 'type': 3}]},
        {'iso_3166_1': 'CN', 'release_dates': [
            {'release_date': '1995-01-01', 'type': 3}]},
    ]
    result = aggregate_movies([make_document(1, releases=releases)],
                              countries=['CN'])
    window = deep_row(result, 'CN')['release_window']
    assert window['status'] == 'PRESENT'
    assert window['covered_movies'] == 1
    assert window['median_lag_days'] == 245
    assert window['samples'][0]['country_release_date'] == '1995-01-01'
    assert window['samples'][0]['earliest_release_date'] == '1994-05-01'
    japan = aggregate_movies([make_document(1, releases=releases)],
                             countries=['US'])['country_analysis']['rows'][0]
    assert japan['release_window']['status'] == 'NO_RELEASE_DATA'


# --------------------------------------------------------------- pagination

def test_details_pagination_is_bounded_and_returns_summary_only():
    documents = [make_document(movie_id) for movie_id in range(1, 26)]
    page = aggregate_movies(documents, filters={'limit': 10, 'offset': 20})
    assert page['details']['total'] == 25
    assert page['details']['returned'] == 5
    assert page['details']['offset'] == 20
    assert page['details']['has_more'] is False
    assert [item['movie_id'] for item in page['details']['items']] == [21, 22, 23, 24, 25]
    item = page['details']['items'][0]
    assert 'overview' not in item
    assert 'facts' not in item
    assert 'credits' not in item
    assert len(item['rating_observations']) <= analytics.MAX_DETAIL_OBSERVATIONS
    assert page['countries']['returned'] <= analytics.MAX_COUNTRY_ROWS

    first = aggregate_movies(documents, filters={'limit': 10})
    assert first['details']['has_more'] is True
    assert first['details']['returned'] == 10


def test_country_quick_view_is_capped_and_detail_payload_stays_bounded():
    codes = [first + second for first in 'ABCDEFGHI' for second in 'ABCDEFGHIJK'][:70]
    documents = [
        make_document(index + 1, countries=[{'iso_3166_1': code, 'name': code}])
        for index, code in enumerate(codes)
    ]
    result = aggregate_movies(documents, filters={'top': 5}, countries=['AA', 'AB'])
    assert result['countries']['total'] == 70
    assert result['countries']['returned'] == analytics.MAX_COUNTRY_ROWS
    assert result['countries']['truncated'] is True
    assert len(result['countries']['rows']) == analytics.MAX_COUNTRY_ROWS
    assert result['details']['returned'] == analytics.DEFAULT_DETAIL_LIMIT
    assert len(result['details']['items']) == analytics.DEFAULT_DETAIL_LIMIT
    assert len(json.dumps(result['details'], ensure_ascii=False)) < 200_000
    assert len(json.dumps(result, ensure_ascii=False)) < 900_000


# ------------------------------------------------------------ input errors

def test_invalid_documents_and_timestamps_are_rejected():
    valid = make_document(1)
    broken_schema = dict(valid, schema_version='tmdb-supplement-v2')
    with pytest.raises(AnalyticsInputError):
        aggregate_movies([broken_schema])
    with pytest.raises(AnalyticsInputError):
        aggregate_movies([dict(valid, movie_id=True)])
    with pytest.raises(AnalyticsInputError):
        aggregate_movies([dict(valid, movie_id=0)])
    with pytest.raises(AnalyticsInputError):
        aggregate_movies([dict(valid, fetched_at='2026-11-01T00:00:00')])
    with pytest.raises(AnalyticsInputError):
        aggregate_movies([dict(valid, fetched_at='not-a-date')])
    with pytest.raises(AnalyticsInputError):
        aggregate_movies({'movie_id': 1})
    with pytest.raises(AnalyticsInputError):
        aggregate_movies([valid], min_votes=True)
    with pytest.raises(AnalyticsInputError):
        aggregate_movies([valid], observed_at='2026-11-01T00:00:00')


def test_illegal_values_are_reported_instead_of_becoming_real_facts():
    document = make_document(
        1,
        countries=[{'iso_3166_1': 'USA', 'name': 'United States'}],
        rating=11.0,
        votes=-5,
    )
    result = aggregate_movies([document])
    assert country_rows(result) == {}
    assert result['global']['movies_country_unknown'] == 1
    assert result['global']['tmdb_avg_rating']['valid_samples'] == 0
    assert result['details']['items'][0]['vote_average'] is None
    codes = issue_codes(result)
    assert {'INVALID_COUNTRY_ENTRY', 'INVALID_METRIC'} <= codes
    assert result['states']['countries'] == 'NO_COUNTRY_METADATA'
    assert result['states']['ratings'] == 'NO_VALID_RATING_SAMPLE'


# ------------------------------------------------- contract compatibility

def test_documents_shaped_exactly_like_normalized_v1_are_consumed():
    """Locks the element keys the normalizer actually emits (no guessing)."""
    document = {
        'schema_version': analytics.SCHEMA_VERSION,
        'movie_id': 42,
        'tmdb_id': 4200,
        'fetched_at': '2026-11-16T12:00:00Z',
        'job_id': 'job-42',
        'payload_sha256': 'f' * 64,
        'field_status': {topic: 'PRESENT' for topic in TOPICS},
        'facts': {
            'base': {
                'id': 4200,
                'title': 'Normalized Title',
                'original_title': 'Normalized Title',
                'release_date': '1994-06-01',
                'runtime_minutes': 118,
                'original_language': 'zh',
                'overview': 'text',
                'genres': [{'id': 18, 'name': '剧情'}],
                'imdb_id': None,
                'poster_path': '/p.jpg',
                'backdrop_path': '/b.jpg',
                'adult': False,
                'budget': 0,
                'revenue': 0,
                'budget_known': False,
                'revenue_known': False,
            },
            'countries': [{'iso_3166_1': 'CN', 'name': 'China'}],
            'companies': [{'id': 7, 'name': 'Studio', 'origin_country': 'US',
                           'logo_path': None}],
            'spoken_languages': [{'iso_639_1': 'zh', 'english_name': 'Chinese',
                                  'name': '中文'}],
            'keywords': [{'id': 900, 'name': '主题'}],
            'releases': [{'iso_3166_1': 'CN', 'release_dates': [{
                'type': 3, 'type_name': 'theatrical',
                'release_date': '1994-06-01',
                'release_date_raw': '1994-06-01',
                'release_date_utc': None, 'precision': 'day',
                'certification': 'PG', 'note': None, 'iso_639_1': 'zh',
                'dedupe_key': 'abc',
            }]}],
            'credits': [
                {'credit_id': 'c1', 'person_id': 11, 'name': 'Actor',
                 'original_name': 'Actor', 'role_type': 'cast',
                 'character': 'Hero', 'order': 0, 'job': None,
                 'department': None, 'profile_path': None},
                {'credit_id': 'c2', 'person_id': 12, 'name': 'Director',
                 'original_name': 'Director', 'role_type': 'crew',
                 'character': None, 'order': None, 'job': 'Director',
                 'department': 'Directing', 'profile_path': None},
            ],
            'metrics': {'vote_average': 8.2, 'vote_count': 120,
                        'popularity': 9.5},
        },
        'issues': [],
    }
    result = aggregate_movies([document], countries=['CN'])
    row = deep_row(result, 'CN')
    assert result['coverage']['assets']['runtime'] == 1
    assert result['coverage']['assets']['budget_nonzero'] == 0
    assert result['coverage']['assets']['revenue_nonzero'] == 0
    assert row['top_genres'] == [{'genre': '剧情', 'genre_id': 18, 'count': 1}]
    assert row['keywords'] == [{'keyword_id': 900, 'name': '主题', 'movie_count': 1}]
    assert row['companies'] == [{'company_id': 7, 'name': 'Studio',
                                 'origin_country': 'US', 'movie_count': 1}]
    assert row['credits']['directors'] == [{'person_id': 12, 'name': 'Director',
                                            'movie_count': 1,
                                            'top_movies': ['Normalized Title']}]
    assert row['credits']['cast'] == [{'person_id': 11, 'name': 'Actor',
                                       'movie_count': 1,
                                       'top_movies': ['Normalized Title']}]
    window = row['release_window']
    assert window['status'] == 'PRESENT'
    assert window['median_lag_days'] == 0
    assert window['samples'][0]['country_release_date'] == '1994-06-01'
    assert result['details']['items'][0]['rating_sample_valid'] is True
    assert result['details']['items'][0]['genres'] == ['剧情']


# ------------------------------------------------- identity one-to-one

def test_one_movie_with_two_tmdb_ids_raises_identity_conflict():
    old = make_document(1, '2026-10-01T00:00:00Z', tmdb_id=100)
    new = make_document(1, '2026-11-01T00:00:00Z', tmdb_id=200,
                        status={'base': 'FAILED', 'countries': 'FAILED',
                                'metrics': 'FAILED'})
    with pytest.raises(analytics.IdentityConflictError) as caught:
        aggregate_movies([old, new])
    error = caught.value
    assert isinstance(error, AnalyticsInputError)
    assert error.code == 'TMDB_IDENTITY_CONFLICT'
    assert error.conflicts['movies'] == [{'movie_id': 1, 'tmdb_ids': [100, 200]}]
    assert error.conflicts['movie_conflict_count'] == 1
    assert '人工复核' in str(error)
    # A newest-but-FAILED document must not hide the conflict either.
    with pytest.raises(analytics.IdentityConflictError):
        aggregate_movies([new, old])
    # Identity is checked before filters, so a filtered-out conflict still fails.
    with pytest.raises(analytics.IdentityConflictError):
        aggregate_movies([old, new], filters={'country': 'JP'})


def test_one_tmdb_id_for_two_movies_raises_identity_conflict():
    with pytest.raises(analytics.IdentityConflictError) as caught:
        aggregate_movies([make_document(1, tmdb_id=100),
                          make_document(2, tmdb_id=100)])
    error = caught.value
    assert error.conflicts['tmdb_ids'] == [{'tmdb_id': 100, 'movie_ids': [1, 2]}]
    assert error.conflicts['movie_conflict_count'] == 0
    assert 'tmdb_id=100' in str(error)


def test_legal_repeats_and_unmapped_movies_still_merge():
    repeated = [
        make_document(1, '2026-10-01T00:00:00Z', tmdb_id=100),
        make_document(1, '2026-11-01T00:00:00Z', tmdb_id=100,
                      status={'keywords': 'MISSING'}),
    ]
    merged = aggregate_movies(repeated)
    assert merged['scope']['movies_total'] == 1
    assert merged['scope']['duplicate_documents_merged'] == 1
    assert merged['details']['items'][0]['tmdb_id'] == 100

    unmapped = make_document(2, tmdb_id=100)
    unmapped['tmdb_id'] = None
    result = aggregate_movies([make_document(1, tmdb_id=100), unmapped])
    assert result['scope']['movies_total'] == 2
    assert [item['tmdb_id'] for item in result['details']['items']] == [100, None]


def test_identity_conflict_only_covers_documents_inside_the_snapshot():
    old = make_document(1, '2026-10-01T00:00:00Z', tmdb_id=100)
    new = make_document(1, '2026-11-01T00:00:00Z', tmdb_id=200)
    before = aggregate_movies([old, new], observed_at='2026-10-15T00:00:00Z')
    assert before['details']['items'][0]['tmdb_id'] == 100
    with pytest.raises(analytics.IdentityConflictError):
        aggregate_movies([old, new], observed_at='2026-12-01T00:00:00Z')


# ------------------------------------------------- country pagination

def _country_documents(count):
    codes = [first + second
             for first in 'ABCDEFGHIJKLMNOPQRSTUVWXY'
             for second in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'][:count]
    assert len(codes) == count
    return [make_document(index + 1,
                          countries=[{'iso_3166_1': code, 'name': code}])
            for index, code in enumerate(codes)]


def test_world_map_reads_every_country_across_pages_and_via_compact():
    documents = _country_documents(70)
    first = aggregate_movies(documents)
    countries = first['countries']
    assert countries['total'] == 70
    assert countries['offset'] == 0
    assert countries['limit'] == analytics.MAX_COUNTRY_ROWS
    assert countries['returned'] == analytics.MAX_COUNTRY_ROWS
    assert countries['truncated'] is True
    assert countries['has_more'] is True
    assert countries['next_offset'] == 60

    second = aggregate_movies(documents, filters={
        'country_offset': countries['next_offset']})
    assert second['countries']['returned'] == 10
    assert second['countries']['has_more'] is False
    assert second['countries']['next_offset'] is None
    head = [row['country_iso2'] for row in countries['rows']]
    tail = [row['country_iso2'] for row in second['countries']['rows']]
    assert len(head) == 60 and len(tail) == 10
    assert len(set(head + tail)) == 70

    compact = countries['compact']
    assert compact['total'] == 70
    assert compact['returned'] == 70
    assert compact['truncated'] is False
    assert [row['country_iso2'] for row in compact['items']] == head + tail
    assert set(row['country_iso2'] for row in compact['items']) == set(head + tail)
    assert 'movie_id' not in compact['items'][0]
    assert compact['items'][0]['movie_count_distinct'] == 1
    assert 'top_genres' not in compact['items'][0]

    bounded = aggregate_movies(documents, filters={'top': 2})
    assert bounded['countries']['compact']['total'] == 70
    assert all(len(row['top_genres']) <= 2 for row in bounded['countries']['rows'])
    assert bounded['details']['returned'] == analytics.DEFAULT_DETAIL_LIMIT


def test_country_page_size_is_configurable_and_validated():
    documents = _country_documents(70)
    page = aggregate_movies(documents, filters={'country_limit': 25})
    assert page['countries']['returned'] == 25
    assert page['countries']['total'] == 70
    assert page['countries']['next_offset'] == 25
    assert page['filters']['country_limit'] == 25
    assert page['filter_semantics']['country_offset']['kind'] == 'response'

    middle = aggregate_movies(documents, filters={'country_offset': 25,
                                                  'country_limit': 25})
    assert middle['countries']['returned'] == 25
    assert middle['countries']['offset'] == 25
    assert middle['filters']['country_offset'] == 25
    for filters, message in [
        ({'country_limit': 0}, 'Invalid country_limit'),
        ({'country_limit': 201}, 'Invalid country_limit'),
        ({'country_limit': True}, 'Invalid country_limit'),
        ({'country_offset': -1}, 'Invalid country_offset'),
        ({'country_offset': 1001}, 'Invalid country_offset'),
    ]:
        with pytest.raises(AnalyticsInputError) as caught:
            aggregate_movies(documents, filters=filters)
        assert message in str(caught.value)


# -------------------------------------------------------------- determinism

def test_output_is_deterministic_json_and_declares_its_limited_provenance():
    documents = [
        make_document(1, countries=[{'iso_3166_1': 'CN', 'name': 'China'},
                                    {'iso_3166_1': 'HK', 'name': 'Hong Kong'}]),
        make_document(2, rating=None, votes=None),
    ]
    first = aggregate_movies(documents, countries=['CN', 'HK'],
                             filters={'top': 3})
    second = aggregate_movies(documents, countries=['CN', 'HK'],
                              filters={'top': 3})
    assert first == second
    assert json.loads(json.dumps(first, ensure_ascii=False)) == first
    assert first['contract'] == 'tmdb-supplement-analytics-v1'
    assert first['consumes'] == analytics.SCHEMA_VERSION
    assert any('未连接数据库' in note for note in first['notes'])
    assert any('不插值' in note for note in first['notes'])
    assert set(first['coverage']['topics']) == set(TOPICS)
    for topic in TOPICS:
        assert set(first['coverage']['topics'][topic]) == set(analytics.STATUSES)