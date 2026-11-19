"""Offline tests for the V2 resource-analytics additions (WP-A).

Everything here is a synthetic ``tmdb-supplement-v1`` fixture: no database, no
network, no configuration.  The tests lock the new contract the dashboard binds
to — page 05 depth blocks, the detail search parameters, the 3-country
comparison samples and the quality view — without copying a real catalog count.
"""
import importlib.util
from pathlib import Path

import pytest

MODULE_PATH = (Path(__file__).resolve().parents[1]
               / 'catalog' / 'tmdb_supplement' / 'analytics.py')
_SPEC = importlib.util.spec_from_file_location('tmdb_supplement_analytics_v2', MODULE_PATH)
analytics = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(analytics)

aggregate_movies = analytics.aggregate_movies
aggregate_quality = analytics.aggregate_quality
AnalyticsInputError = analytics.AnalyticsInputError
TOPICS = analytics.TOPICS


# --------------------------------------------------------------- fixtures

def make_document(movie_id, fetched_at='2026-11-01T00:00:00Z', *, tmdb_id=None,
                  title=None, countries=None, genres=None, rating=8.0, votes=100,
                  popularity=5.0, keywords=None, companies=None, languages=None,
                  original_language='zh', credits=None, releases=None,
                  runtime=100, poster='/poster.jpg', overview='synthetic overview',
                  release_date='1994-06-01', status=None):
    facts = {
        'base': {
            'title': title or 'Movie %d' % movie_id,
            'original_title': title or 'Movie %d' % movie_id,
            'release_date': release_date,
            'original_language': original_language,
            'genres': genres if genres is not None else [{'id': 18, 'name': '剧情'}],
            'runtime': runtime,
            'overview': overview,
            'poster_path': poster,
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
    }


def detail_ids(result):
    return [item['movie_id'] for item in result['details']['items']]


# ------------------------------------------------------- page 05 depth blocks

def test_global_depth_blocks_carry_their_own_denominators():
    documents = [
        make_document(1, keywords=[{'id': 11, 'name': '太空'},
                                   {'id': 12, 'name': '未来'}],
                      companies=[{'id': 7, 'name': 'Studio A'},
                                 {'id': 8, 'name': 'Studio B'}],
                      genres=[{'id': 18, 'name': '剧情'},
                              {'id': 878, 'name': '科幻'}],
                      runtime=100),
        make_document(2, keywords=[{'id': 11, 'name': '太空'}],
                      companies=[{'id': 7, 'name': 'Studio A'}],
                      genres=[{'id': 18, 'name': '剧情'}],
                      runtime=0),
        make_document(3, keywords=[], companies=[],
                      genres=[], runtime=None,
                      status={'keywords': 'EMPTY', 'companies': 'NOT_FETCHED'}),
    ]
    result = aggregate_movies(documents, filters={'top': 10})
    global_block = result['global']

    keywords = global_block['top_keywords']
    assert keywords['status'] == 'PRESENT'
    assert keywords['movies_with_keywords'] == 2
    assert keywords['movies_total'] == 3
    assert [(row['keyword_id'], row['movie_count']) for row in keywords['items']] == [
        (11, 2), (12, 1)]

    companies = global_block['top_companies']
    assert companies['movies_with_companies'] == 2
    assert companies['items'][0]['company_id'] == 7
    assert companies['items'][0]['movie_count'] == 2

    credits = global_block['credits']
    assert credits['status'] == 'PRESENT'
    assert credits['movies_with_credits'] == 0

    runtime = global_block['runtime_distribution']
    assert runtime['status'] == 'PARTIAL'
    assert runtime['movies_with_runtime'] == 1
    assert runtime['unknown'] == 2
    assert {row['bucket']: row['count'] for row in runtime['buckets']}['90–119'] == 1

    pairs = global_block['genre_pairs']
    assert pairs['movies_with_genres'] == 2
    assert pairs['movies_with_multiple_genres'] == 1
    assert pairs['pairs'][0]['movie_count'] == 1
    assert set(pairs['pairs'][0]['genres']) == {'剧情', '科幻'}


def test_runtime_zero_and_missing_are_unknown_not_a_bucket():
    result = aggregate_movies([
        make_document(1, runtime=0),
        make_document(2, runtime=None),
        make_document(3, runtime=200),
        make_document(4, runtime=45),
    ])
    runtime = result['global']['runtime_distribution']
    buckets = {row['bucket']: row['count'] for row in runtime['buckets']}
    assert runtime['unknown'] == 2
    assert buckets['0–59'] == 1
    assert buckets['180+'] == 1
    assert sum(buckets.values()) == runtime['movies_with_runtime'] == 2


def test_genre_pairs_without_multi_label_movies_are_reported_not_zeroed():
    result = aggregate_movies([make_document(1, genres=[{'id': 18, 'name': '剧情'}])])
    pairs = result['global']['genre_pairs']
    assert pairs['status'] == 'NO_MULTI_GENRE_MOVIES'
    assert pairs['movies_with_multiple_genres'] == 0
    assert pairs['pairs'] == []

    no_genres = aggregate_movies([make_document(1, genres=[])])
    assert no_genres['global']['genre_pairs']['status'] == 'NO_GENRE_DATA'
    assert no_genres['global']['top_keywords']['status'] == 'PRESENT'


# ------------------------------------------------------------- detail search

def detail_fixture():
    return [
        make_document(1, countries=[{'iso_3166_1': 'CN', 'name': 'China'},
                                    {'iso_3166_1': 'HK', 'name': 'Hong Kong'}],
                      keywords=[{'id': 11, 'name': 'space'}],
                      companies=[{'id': 7, 'name': 'Studio A'}],
                      votes=500, rating=8.5, popularity=9.0,
                      credits=[{'person_id': 1, 'role_type': 'crew',
                                'job': 'Director', 'person_name': 'D'}]),
        make_document(2, countries=[{'iso_3166_1': 'CN', 'name': 'China'}],
                      keywords=[{'id': 12, 'name': 'future'}],
                      companies=[{'id': 8, 'name': 'Studio B'}],
                      votes=10, rating=6.0, popularity=1.0,
                      poster=None, overview=None, runtime=None),
        make_document(3, countries=[{'iso_3166_1': 'US', 'name': 'United States'}],
                      keywords=[], companies=[],
                      votes=None, rating=None, popularity=None,
                      status={'keywords': 'FAILED',
                              'companies': 'NOT_FETCHED',
                              'credits': 'MISSING',
                              'metrics': 'NOT_FETCHED'}),
    ]


def test_details_filters_by_keyword_company_missing_and_overlap():
    documents = detail_fixture()
    by_keyword = aggregate_movies(documents, filters={'keyword': 11})
    assert detail_ids(by_keyword) == [1]
    assert by_keyword['details']['total'] == 1
    # Detail filters never move the global scope.
    assert by_keyword['scope']['movies_matched'] == 3

    by_company = aggregate_movies(documents, filters={'company': 8})
    assert detail_ids(by_company) == [2]

    missing_credits = aggregate_movies(documents, filters={'missing': 'credits'})
    assert detail_ids(missing_credits) == [2, 3]
    assert 'credits' in missing_credits['details']['items'][0]['missing_fields']

    missing_poster = aggregate_movies(documents, filters={'missing': ['poster']})
    assert detail_ids(missing_poster) == [2]

    overlap = aggregate_movies(documents, filters={'overlap': ['CN', 'HK']})
    assert detail_ids(overlap) == [1]
    assert overlap['details']['applied_filters']['overlap'] == ['CN', 'HK']

    both = aggregate_movies(documents, filters={'missing': 'keywords,companies'})
    assert detail_ids(both) == [3]


def test_details_rows_expose_identity_and_missing_reasons():
    result = aggregate_movies(detail_fixture())
    rows = {item['movie_id']: item for item in result['details']['items']}
    assert rows[1]['tmdb_id'] == 10
    assert rows[1]['missing_fields'] == ['releases']
    assert rows[2]['missing_reasons']['poster'] == 'EMPTY'
    assert rows[3]['missing_reasons']['keywords'] == 'FAILED'
    assert rows[3]['missing_reasons']['metrics'] == 'NOT_FETCHED'


def test_details_sort_and_order_put_missing_values_last():
    documents = detail_fixture()
    votes_desc = aggregate_movies(documents, filters={'sort': 'votes', 'order': 'desc'})
    assert detail_ids(votes_desc) == [1, 2, 3]
    votes_asc = aggregate_movies(documents, filters={'sort': 'votes', 'order': 'asc'})
    assert detail_ids(votes_asc) == [2, 1, 3]
    title_asc = aggregate_movies(documents, filters={'sort': 'title', 'order': 'asc'})
    assert detail_ids(title_asc) == [1, 2, 3]
    rating_desc = aggregate_movies(documents, filters={'sort': 'rating', 'order': 'desc'})
    assert detail_ids(rating_desc) == [1, 2, 3]
    year_asc = aggregate_movies(documents, filters={'sort': 'year', 'order': 'asc'})
    assert detail_ids(year_asc) == [1, 2, 3]


def test_details_rejects_invalid_search_parameters():
    documents = detail_fixture()
    for filters in (
            {'sort': 'tmdb_id'},
            {'sort': 'title', 'order': 'up'},
            {'order': 'desc'},
            {'missing': 'trailer'},
            {'missing': 'poster,trailer'},
            {'overlap': ['CN', 'HK', 'US', 'JP']},
            {'keyword': 0},
            {'company': True}):
        with pytest.raises(AnalyticsInputError):
            aggregate_movies(documents, filters=filters)


# ------------------------------------------------------ 3-country comparison

def test_comparison_reports_triple_overlap_and_country_samples():
    documents = [
        make_document(1, countries=[{'iso_3166_1': 'CN', 'name': 'China'},
                                    {'iso_3166_1': 'JP', 'name': 'Japan'},
                                    {'iso_3166_1': 'US', 'name': 'United States'}],
                      votes=500),
        make_document(2, countries=[{'iso_3166_1': 'CN', 'name': 'China'},
                                    {'iso_3166_1': 'JP', 'name': 'Japan'}],
                      votes=10),
        make_document(3, countries=[{'iso_3166_1': 'CN', 'name': 'China'}]),
    ]
    result = aggregate_movies(documents, countries=['CN', 'JP', 'US'])
    comparison = result['comparison']
    assert comparison['union_movie_count'] == 3
    assert comparison['overlap_movie_count'] == 2
    assert comparison['all_countries_intersection_movie_count'] == 1
    assert comparison['all_countries_intersection_movie_ids'] == [1]
    samples = {row['country_iso2']: row for row in comparison['country_samples']}
    assert samples['CN']['movie_count_distinct'] == 3
    assert samples['CN']['rated_movie_count'] == 2
    assert samples['JP']['rated_movie_count'] == 1
    assert samples['US']['rated_movie_count'] == 1
    assert samples['US']['tmdb_avg_rating']['min_votes'] == 50
    pairs = {tuple(row['countries']): row for row in comparison['pairwise']}
    assert pairs[('CN', 'JP')]['intersection_movie_count'] == 2


# --------------------------------------------------------------- quality view

def quality_fixture():
    documents = [
        make_document(1, countries=[{'iso_3166_1': 'CN', 'name': 'China'}],
                      genres=[{'id': 18, 'name': '剧情'}],
                      keywords=[{'id': 11, 'name': 'space'}],
                      companies=[{'id': 7, 'name': 'Studio A'}],
                      credits=[{'person_id': 1, 'role_type': 'crew',
                                'job': 'Director', 'person_name': 'D'}]),
        make_document(2, countries=[{'iso_3166_1': 'US', 'name': 'United States'}],
                      genres=[{'id': 28, 'name': '动作'}],
                      poster=None, overview=None,
                      status={'keywords': 'FAILED'}
                      ),
        make_document(3, countries=[], genres=[{'id': 18, 'name': '剧情'}],
                      keywords=[], companies=[], credits=[],
                      status={'keywords': 'EMPTY', 'companies': 'EMPTY',
                              'credits': 'NOT_FETCHED'}),
    ]
    return documents


def test_quality_counts_five_states_plus_unmapped():
    documents = quality_fixture()
    catalog = {'active_movies': 4, 'tmdb_mapped': 3, 'unmapped': 1}
    data = aggregate_quality(documents, catalog=catalog, denominator='active')
    assert data['contract'] == analytics.QUALITY_CONTRACT
    assert data['denominator']['mode'] == 'active'
    assert data['denominator']['movies'] == 4
    keywords = data['topics']['keywords']
    assert keywords['PRESENT'] == 1
    assert keywords['FAILED'] == 1
    assert keywords['EMPTY'] == 1
    assert keywords['NOT_FETCHED'] == 0
    assert keywords['UNMAPPED'] == 1
    assert keywords['total'] == 4
    assert data['topics']['credits']['NOT_FETCHED'] == 1
    assert data['states']['unmapped'] == 1
    assert data['catalog']['unmapped'] == 1
    assert data['assets']['poster_missing'] == 1
    assert data['assets']['overview_missing'] == 1


def test_quality_mapped_denominator_excludes_unmapped():
    documents = quality_fixture()
    catalog = {'active_movies': 4, 'tmdb_mapped': 3, 'unmapped': 1}
    data = aggregate_quality(documents, catalog=catalog, denominator='mapped')
    assert data['denominator']['movies'] == 3
    assert data['topics']['keywords']['UNMAPPED'] == 0
    assert data['states']['unmapped'] == 0
    categories = {row['category'] for row in data['problems']['items']}
    assert 'UNMAPPED' not in categories


def test_quality_severity_and_problem_list():
    unmapped = [{'movie_id': 90, 'title': 'Unmapped movie', 'release_year': 2001}]
    data = aggregate_quality(quality_fixture(),
                             catalog={'active_movies': 4, 'tmdb_mapped': 3,
                                      'unmapped': 1},
                             top=10, problem_limit=100, unmapped_movies=unmapped)
    countries = {row['country_iso2']: row for row in data['severity']['by_country']}
    assert countries['CN']['movies'] == 1
    assert countries['US']['movies'] == 1
    assert countries['US']['missing_movies'] == 1
    assert countries['US']['top_missing_topics'][0]['topic'] in ('poster', 'overview',
                                                                 'keywords')
    assert data['severity']['unknown_country_movies'] == 1

    problems = data['problems']
    assert problems['total'] >= 1
    rows = problems['items']
    assert any(row['movie_id'] == 90 and row['category'] == 'UNMAPPED'
               for row in rows)
    row_two = [row for row in rows if row['movie_id'] == 2]
    assert row_two and row_two[0]['actionable'] is True
    empty_rows = [row for row in rows if row['movie_id'] == 3 and row['topic'] == 'keywords']
    assert empty_rows[0]['category'] == 'EMPTY'
    assert empty_rows[0]['actionable'] is False


def test_quality_problem_filters_and_pagination():
    documents = quality_fixture()
    catalog = {'active_movies': 4, 'tmdb_mapped': 3, 'unmapped': 1}
    unmapped = [{'movie_id': 90, 'title': 'Unmapped movie', 'release_year': 2001}]
    failed = aggregate_quality(documents, catalog=catalog, topic='keywords',
                               category='FAILED', unmapped_movies=unmapped)
    assert failed['problems']['total'] == 1
    assert failed['problems']['items'][0]['movie_id'] == 2

    first = aggregate_quality(documents, catalog=catalog, problem_limit=1,
                              problem_offset=0, unmapped_movies=unmapped)
    assert first['problems']['returned'] == 1
    assert first['problems']['has_more'] is True
    second = aggregate_quality(documents, catalog=catalog, problem_limit=1,
                               problem_offset=1, unmapped_movies=unmapped)
    assert second['problems']['items'][0]['movie_id'] != first['problems']['items'][0]['movie_id']


def test_quality_rejects_invalid_parameters():
    for kwargs in ({'denominator': 'all'},
                   {'top': 0},
                   {'problem_limit': 101},
                   {'problem_offset': -1},
                   {'topic': 'trailer'},
                   {'category': 'BROKEN'}):
        with pytest.raises(AnalyticsInputError):
            aggregate_quality(quality_fixture(), **kwargs)