/**
 * WP-C render smoke test.
 *
 * There is no local Flask backend in this session, so this script answers the
 * admin APIs with a clearly synthetic fixture. It exists ONLY to prove the new
 * AdminView V2 components mount, fetch, switch views and render without runtime
 * errors. The screenshots are labelled `smoke-*` and must never be cited as
 * real production statistics.
 *
 * Usage: node tools/test-admin-v2-smoke.mjs <output-dir>
 */
import fs from 'node:fs'
import path from 'node:path'
import {chromium} from '@playwright/test'

const out = process.argv[2]
if (!out) throw new Error('output directory required')
if (/^[cC]:/.test(out)) throw new Error('refusing to write to C:')
fs.mkdirSync(out, {recursive: true})
const base = 'http://127.0.0.1:5174'

const genre = (name, count) => ({name, genre: name, genre_id: null, count})
const decade = (value, count) => ({decade: value, name: value + '年代', count})
const movie = (id, title, year, rating, votes, popularity = 20) => ({
  movie_id: id, tmdb_id: 10000 + id, title, release_year: year,
  vote_average: rating, vote_count: votes, popularity,
})
const ratingSummary = (samples, mean) => ({
  source: 'tmdb', scale: '0-10', min_votes: 50, valid_samples: samples,
  total_votes: samples * 300, unweighted_mean: mean, vote_weighted_mean: mean - .2,
  below_min_votes_movies: 12,
})
const detail = (id, title, country) => ({
  movie_id: id, tmdb_id: 10000 + id, title, release_year: 1990 + (id % 30),
  decade: 1990 + Math.floor((id % 30) / 10) * 10, genres: ['Drama', 'Animation'],
  countries: [country], country_known: true, original_language: 'en',
  spoken_languages: ['en', 'ja'], vote_average: 7.2, vote_count: 320,
  popularity: 18 + id / 10, rating_sample_valid: true, rating_observation_count: 3,
  observed_at: '2026-11-17T02:00:00Z', latest_fetched_at: '2026-11-17T02:00:00Z',
  topic_status: {base: 'PRESENT', countries: 'PRESENT', keywords: 'MISSING', credits: 'PRESENT'},
  missing_fields: id % 3 === 0 ? ['keywords'] : id % 3 === 1 ? ['poster', 'overview'] : [],
  missing_reasons: id % 3 === 0 ? {keywords: 'FAILED'} : id % 3 === 1 ? {poster: 'EMPTY', overview: 'NOT_FETCHED'} : {},
})
const details = Array.from({length: 20}, (_, i) => detail(1000 + i, '样例影片 ' + (i + 1), i % 2 ? 'JP' : 'CN'))
const analyticsCalls = []
const qualityCalls = []
function baseDetails(url) {
  const params = new URL(url).searchParams
  const overlap = params.get('overlap')
  const keyword = params.get('keyword')
  const company = params.get('company')
  const missing = params.get('missing')
  const total = overlap ? 3 : keyword ? 2 : company ? 2 : missing ? 4 : 1883
  return {
    total, limit: 20, offset: Number(params.get('offset') || 0), returned: Math.min(20, total), has_more: total > 20,
    total_before_detail_filters: 1883,
    applied_filters: {keyword: keyword ? Number(keyword) : null, company: company ? Number(company) : null, missing: missing ? missing.split(',') : [], sort: params.get('sort'), order: params.get('order'), overlap: overlap ? overlap.split(',') : []},
    items: details.slice(0, Math.min(total, 20)),
  }
}

const deep = (code, name, count, rating) => ({
  country_iso2: code, country_name: name, movie_count_distinct: count,
  share_of_filtered_catalog: count / 1883, rated_movie_count: Math.round(count * .62),
  tmdb_avg_rating: ratingSummary(Math.round(count * .62), rating),
  top_genres: [genre('Drama', Math.round(count * .5)), genre('Animation', Math.round(count * .4)), genre('Action', Math.round(count * .3)), genre('Comedy', Math.round(count * .2)), genre('Documentary', Math.round(count * .1))],
  genre_by_decade: [
    {genre: 'Drama', genre_id: null, total: 120, decades: [decade(1990, 20), decade(2000, 40), decade(2010, 35), decade(2020, 25)]},
    {genre: 'Animation', genre_id: null, total: 90, decades: [decade(1990, 10), decade(2000, 30), decade(2010, 30), decade(2020, 20)]},
    {genre: 'Action', genre_id: null, total: 60, decades: [decade(2000, 20), decade(2010, 25), decade(2020, 15)]},
  ],
  decade_distribution: [decade(1970, 6), decade(1980, 12), decade(1990, 28), decade(2000, 52), decade(2010, 60), decade(2020, 30)],
  languages: {
    original_language: [{code: 'ja', count: Math.round(count * .8)}, {code: 'en', count: Math.round(count * .15)}],
    spoken_languages: [{code: 'ja', count: Math.round(count * .76)}, {code: 'en', count: Math.round(count * .2)}],
  },
  companies: [{company_id: 1, name: '样例制作公司 A', origin_country: 'JP', movie_count: 22}, {company_id: 2, name: '样例制作公司 B', origin_country: 'JP', movie_count: 14}],
  keywords: [{keyword_id: 1, name: '童年', movie_count: 18}, {keyword_id: 2, name: '城市', movie_count: 12}, {keyword_id: 3, name: '幻想', movie_count: 9}],
  credits: {
    status: 'PRESENT', topic_status: {PRESENT: count}, movies_with_credits: Math.round(count * .9),
    directors: [{person_id: 1, name: '样例导演', movie_count: 12, top_movies: ['样例影片 1', '样例影片 2']}],
    cast: [{person_id: 2, name: '样例演员', movie_count: 20, top_movies: ['样例影片 3']}],
  },
  release_window: {
    status: 'PRESENT', covered_movies: Math.round(count * .3), median_lag_days: 45,
    samples: [{movie_id: 1001, country_release_date: '2020-05-01', earliest_release_date: '2020-03-01', lag_days: 61}],
  },
  rating_distribution: {
    rating_histogram: [{name: '0–2', count: 4}, {name: '2–4', count: 16}, {name: '4–6', count: 60}, {name: '6–8', count: 150}, {name: '8–10', count: 40}],
    vote_count_quantiles: {samples: 500, p25: 120, p50: 400, p75: 1500},
    popularity_quantiles: {samples: 500, p25: 5.2, p50: 12.4, p75: 30.1},
  },
  top_movies: [movie(1001, '样例高分影片', 2015, 8.6, 2200, 55), movie(1002, '样例热门影片', 2018, 7.9, 1800, 80), movie(1003, '样例经典影片', 1998, 8.2, 3200, 30), movie(1004, '样例动画影片', 2004, 7.5, 900, 18), movie(1005, '样例纪录影片', 2021, 7.1, 120, 6)],
  top_rated_movies: [movie(1001, '样例高分影片', 2015, 8.6, 2200, 55), movie(1003, '样例经典影片', 1998, 8.2, 3200, 30), movie(1002, '样例热门影片', 2018, 7.9, 1800, 80)],
})

const countryRows = [
  {code: 'US', name: 'United States of America', count: 620, rating: 7.1},
  {code: 'JP', name: 'Japan', count: 268, rating: 7.4},
  {code: 'GB', name: 'United Kingdom', count: 240, rating: 7.0},
  {code: 'FR', name: 'France', count: 220, rating: 6.9},
  {code: 'CN', name: 'China', count: 186, rating: 6.6},
  {code: 'DE', name: 'Germany', count: 140, rating: 7.0},
  {code: 'KR', name: 'South Korea', count: 120, rating: 7.2},
  {code: 'IN', name: 'India', count: 96, rating: 6.4},
  {code: 'IT', name: 'Italy', count: 80, rating: 6.8},
  {code: 'ES', name: 'Spain', count: 62, rating: 6.7},
]
const compactRow = row => ({country_iso2: row.code, country_name: row.name, movie_count_distinct: row.count, share_of_filtered_catalog: row.count / 1883, rated_movie_count: Math.round(row.count * .62)})
const countryRow = row => ({...compactRow(row), tmdb_avg_rating: ratingSummary(Math.round(row.count * .62), row.rating), top_genres: deep(row.code, row.name, row.count, row.rating).top_genres, decade_distribution: deep(row.code, row.name, row.count, row.rating).decade_distribution})

const globalBlock = {
  movie_count_distinct: 1883, movies_with_countries: 1800, movies_country_unknown: 83,
  rated_movie_count: 1167, tmdb_avg_rating: ratingSummary(1167, 6.9),
  top_genres: [genre('Drama', 980), genre('Comedy', 620), genre('Action', 510), genre('Thriller', 430), genre('Romance', 380), genre('Animation', 240), genre('Documentary', 180), genre('Horror', 150)],
  decade_distribution: [decade(1950, 12), decade(1960, 20), decade(1970, 40), decade(1980, 90), decade(1990, 210), decade(2000, 420), decade(2010, 560), decade(2020, 310)],
  original_language: [{code: 'en', count: 900}, {code: 'ja', count: 260}, {code: 'fr', count: 150}, {code: 'zh', count: 120}],
  spoken_languages: [{code: 'en', count: 1020}, {code: 'ja', count: 240}, {code: 'fr', count: 160}, {code: 'zh', count: 110}],
  top_movies: [movie(2001, '样例全库热门', 2019, 8.1, 5200, 120), movie(2002, '样例全库经典', 1994, 8.9, 6100, 60), movie(2003, '样例全库新片', 2024, 7.4, 800, 95)],
  top_rated_movies: [movie(2002, '样例全库经典', 1994, 8.9, 6100, 60), movie(2001, '样例全库热门', 2019, 8.1, 5200, 120)],
  top_popular_movies: [movie(2001, '样例全库热门', 2019, 8.1, 5200, 120), movie(2003, '样例全库新片', 2024, 7.4, 800, 95)],
  rating_distribution: {
    rating_histogram: [{name: '0–2', count: 20}, {name: '2–4', count: 80}, {name: '4–6', count: 340}, {name: '6–8', count: 560}, {name: '8–10', count: 167}],
    vote_count_quantiles: {samples: 1167, p25: 90, p50: 320, p75: 1100},
    popularity_quantiles: {samples: 1167, p25: 4.2, p50: 11.2, p75: 26.5},
  },
  assets: {poster: 1700, overview: 1500, backdrop: 1600, runtime: 1800, budget: 900, revenue: 900},
  top_keywords: {
    status: 'PRESENT', movies_total: 1883, movies_with_keywords: 1500,
    items: [{keyword_id: 1, name: '童年', movie_count: 42}, {keyword_id: 2, name: '城市', movie_count: 31}, {keyword_id: 3, name: '幻想', movie_count: 24}],
  },
  top_companies: {
    status: 'PRESENT', movies_total: 1883, movies_with_companies: 1400,
    items: [{company_id: 1, name: '样例制作公司 A', origin_country: 'JP', movie_count: 28}, {company_id: 2, name: '样例制作公司 B', origin_country: 'US', movie_count: 19}],
  },
  credits: {
    status: 'PRESENT', movies_with_credits: 1700, topic_status: {PRESENT: 1700, FAILED: 183},
    directors: [{person_id: 1, name: '样例导演', movie_count: 12, top_movies: ['样例影片 1']}],
    cast: [{person_id: 2, name: '样例演员', movie_count: 20, top_movies: ['样例影片 3']}],
  },
  runtime_distribution: {
    status: 'PARTIAL', movies_total: 1883, movies_with_runtime: 1750, unknown: 133,
    buckets: [
      {bucket: '0–59', min_minutes: 1, max_minutes: 59, count: 60},
      {bucket: '60–89', min_minutes: 60, max_minutes: 89, count: 280},
      {bucket: '90–119', min_minutes: 90, max_minutes: 119, count: 700},
      {bucket: '120–149', min_minutes: 120, max_minutes: 149, count: 480},
      {bucket: '150–179', min_minutes: 150, max_minutes: 179, count: 190},
      {bucket: '180+', min_minutes: 180, max_minutes: null, count: 40},
    ],
  },
  genre_pairs: {
    status: 'PRESENT', movies_total: 1883, movies_with_genres: 1800, movies_with_multiple_genres: 1200,
    pairs: [{genres: ['Drama', 'Romance'], movie_count: 320}, {genres: ['Action', 'Thriller'], movie_count: 240}, {genres: ['Comedy', 'Drama'], movie_count: 180}],
    denominator: {movies_with_multiple_genres: 1200},
  },
}

const coverageBlock = {
  movies: 1883,
  countries: {known: 1800, unknown: 83, share_known: .956, topic_status: {PRESENT: 1800, MISSING: 83}},
  ratings: {valid_samples: 1167, unknown: 716, share_valid: .62, min_votes: 50},
  genres: {movies_with_genres: 1883}, keywords: {movies_with_keywords: 1500},
  companies: {movies_with_companies: 1600}, credits: {movies_with_credits: 1700},
  releases: {movies_with_releases: 800},
  topics: {base: {PRESENT: 1883}, countries: {PRESENT: 1800, MISSING: 83}, credits: {PRESENT: 1700, FAILED: 183}},
  assets: {poster: 1700, overview: 1500, backdrop: 1600, runtime: 1800, budget: 900, revenue: 900},
}
function analytics(url) {
  const params = new URL(url).searchParams
  const requested = params.get('country')
  const compare = params.get('countries')
  const base = {
    contract: 'tmdb-supplement-analytics-v1', filters: {min_votes: 50},
    snapshot: {observed_at: '2026-11-17T03:00:00Z', latest_fetched_at: '2026-11-17T03:00:00Z', earliest_fetched_at: '2026-10-01T00:00:00Z'},
    scope: {
      documents_seen: 1883, documents_skipped_after_observed_at: 0, documents_used: 1883, duplicate_documents_merged: 0, movies_total: 1883, movies_matched: 1883,
      catalog: {active_movies: 1997, tmdb_mapped: 1883, unmapped: 114, source: 'movies + tmdb_movie_metadata', as_of: '2026-11-17T03:00:00Z', status: 'AVAILABLE'},
      cache: {hit: true, status: 'HIT', loaded_at: '2026-11-17T03:01:00Z', watermark: {document_count: 1883, max_fetched_at: '2026-11-17T02:59:00Z', max_updated_at: '2026-11-17T02:58:00Z'}, ttl_seconds: 600, age_seconds: 120, load_count: 4},
      repository: {documents_loaded: 1883, cap: 20000, truncated: false},
    },
    coverage: coverageBlock,
    states: {catalog: 'HAS_MOVIES', countries: 'PARTIAL', ratings: 'PARTIAL'},
    global: globalBlock,
    countries: {
      total: countryRows.length, offset: 0, limit: 200, returned: countryRows.length, has_more: false, next_offset: null, truncated: false,
      rows: countryRows.map(countryRow),
      compact: {total: countryRows.length, returned: countryRows.length, truncated: false, items: countryRows.map(compactRow)},
      denominator: {movies: 1883},
    },
    country_analysis: null, comparison: null,
    details: baseDetails(url),
    issues: [], notes: ['样例数据仅用于前端渲染冒烟。'],
  }
  if (requested) {
    const picked = countryRows.find(row => row.code === requested) || {code: requested, name: requested, count: 12, rating: 6.5}
    base.country_analysis = {denominator: 1883, rows: [deep(picked.code, picked.name, picked.count, picked.rating)]}
  }
  if (compare) {
    const codes = compare.split(',').slice(0, 3)
    const picked = codes.map(code => countryRows.find(row => row.code === code) || {code, name: code, count: 20, rating: 6.4})
    base.country_analysis = {denominator: 1883, rows: picked.map(row => deep(row.code, row.name, row.count, row.rating))}
    base.comparison = {
      countries: codes, union_movie_count: 640, overlap_movie_count: 58, overlap_ratio_of_union: .09,
      pairwise: codes.slice(0, 2).map((code, index) => ({
        countries: [code, codes[index + 1] || code], intersection_movie_count: 34,
        union_movie_count: 420, jaccard: .08,
        shared_movie_ids: [1001, 1002, 1003, 1004],
      })),
      country_samples: codes.map((code, index) => ({
        country_iso2: code, movie_count_distinct: 320 - index * 30, rated_movie_count: 210 - index * 20,
        tmdb_avg_rating: {valid_samples: 210 - index * 20, unweighted_mean: 6.9 - index * .1, simple_mean: 6.9 - index * .1, min_votes: 50, note: '简单平均'},
      })),
      all_countries_intersection_movie_count: 11,
      all_countries_intersection_movie_ids: [1001, 1002, 1003, 1004, 1005, 1006, 1007, 1008, 1009, 1010, 1011],
      note: '样例数据。',
    }
  }
  return base
}

const dashboard = {
  as_of: '2026-11-18T13:05:00Z',
  window: {days: 30, from: '2026-10-19T00:00:00Z', to: '2026-11-18T13:05:00Z', timezone: 'UTC'},
  catalog: {
    total: 1997, tmdb_mapped: 1883, countries: countryRows.map(row => ({iso2: row.code, count: row.count})),
    countries_covered_movies: 1800, countries_unknown_movies: 197,
    genres: [genre('剧情', 980), genre('喜剧', 620), genre('动作', 510), genre('动画', 240)],
    decades: [decade(1990, 210), decade(2000, 420), decade(2010, 560), decade(2020, 310)],
    ratings: [{name: '6–8', count: 560}, {name: '8–10', count: 167}],
    rating_source: 'douban', quality: {tmdb_mapped: 1883, posters: 1700, cast: 1700, cast_photos: 1200, overview: 1500},
  },
  behavior: {
    events: 1280, active_users: 42,
    daily: Array.from({length: 30}, (_, i) => ({date: '2026-10-' + String(i + 19).padStart(2, '0'), count: 20 + (i % 7) * 5})),
    kinds: [{name: '收藏', kind: 'favorite', count: 400}], top_movies: [{movie_id: 2001, title: '样例全库热门', count: 42}],
    current: {watched: 900, favorite: 400, want: 300, rated: 200},
  },
  users: {accounts_active: 128, new_in_window: 9, sessions_valid: 60},
  ai: {status: 'AVAILABLE', calls: 30, real_calls: 22, mock_calls: 5, unknown_provenance_calls: 3, real_input_tokens: 12000, real_output_tokens: 8000, real_unknown_calls: 2, known_input_tokens: 12000, known_output_tokens: 8000, unknown_calls: 2, models: [{name: 'example', count: 22}], amount: null, amount_status: 'UNKNOWN'},
  recommendation: {
    status: 'available', source: 'recommendation_events', sample_size: 3200, as_of: '2026-11-18T13:05:00Z',
    impressions: 3200, clicks: 240, ctr: .075, accounts: 37,
    channels: [{channel: 'home', impressions: 2100, clicks: 180, ctr: .0857}, {channel: 'recommendations', impressions: 1100, clicks: 60, ctr: .0545}],
    daily: [], unattributed_clicks: 2, reason: null,
  },
  system: {
    status: 'available', source: 'dashboard', sample_size: 1280, as_of: '2026-11-18T13:05:00Z',
    generated_at: {catalog: '2026-11-18T13:00:00Z', behavior: '2026-11-18T13:04:00Z'},
    process_started_at: '2026-11-18T08:00:00Z', note: '样例。', ai_failures: 4, ai_failure_status: 'instrumented',
  },
  notes: ['样例数据仅用于前端渲染冒烟。'],
}
const feedback = {
  impressions: 3200, clicks: 240, ctr: .075,
  channels: [{channel: 'home', impressions: 2100, clicks: 180, ctr: .0857}, {channel: 'recommendations', impressions: 1100, clicks: 60, ctr: .0545}],
  ranking: {status: 'OK', k: 10, precision_at_k: .21, recall_at_k: .12, ndcg_at_k: .33, users: 18, batches: 40},
  as_of: '2026-11-18T13:05:00Z', window: {days: 30},
}

const quality = {
  contract: 'tmdb-supplement-quality-v1',
  catalog: {active_movies: 1997, tmdb_mapped: 1883, unmapped: 114, mapped_documents_loaded: 1883, source: 'movies + tmdb_movie_metadata', as_of: '2026-11-17T03:00:00Z'},
  denominator: {mode: 'active', movies: 1997, note: '在架口径包含未关联影片。'},
  states: {statuses: ['PRESENT', 'EMPTY', 'MISSING', 'NOT_FETCHED', 'FAILED'], unmapped: 114, note: 'EMPTY 不等于失败。'},
  topics: {
    base: {PRESENT: 1883, EMPTY: 0, MISSING: 0, NOT_FETCHED: 0, FAILED: 0, UNMAPPED: 114, total: 1997, counted_movies: 1997, present_ratio: .943},
    countries: {PRESENT: 1700, EMPTY: 120, MISSING: 40, NOT_FETCHED: 20, FAILED: 3, UNMAPPED: 114, total: 1997, counted_movies: 1997, present_ratio: .851},
    keywords: {PRESENT: 1500, EMPTY: 200, MISSING: 100, NOT_FETCHED: 70, FAILED: 13, UNMAPPED: 114, total: 1997, counted_movies: 1997, present_ratio: .751},
    poster: {PRESENT: 1700, EMPTY: 80, MISSING: 60, NOT_FETCHED: 30, FAILED: 13, UNMAPPED: 114, total: 1997, counted_movies: 1997, present_ratio: .851},
    overview: {PRESENT: 1500, EMPTY: 300, MISSING: 60, NOT_FETCHED: 20, FAILED: 3, UNMAPPED: 114, total: 1997, counted_movies: 1997, present_ratio: .751},
    credits: {PRESENT: 1600, EMPTY: 0, MISSING: 200, NOT_FETCHED: 70, FAILED: 13, UNMAPPED: 114, total: 1997, counted_movies: 1997, present_ratio: .801},
  },
  assets: {movies: 1997, movies_with_base: 1883, poster_present: 1700, poster_missing: 297, overview_present: 1500, overview_missing: 497, backdrop_present: 1600, note: '含未关联。'},
  severity: {
    definition: '缺失率 = 缺任一关键主题的影片 / 该组影片。',
    by_country: [{name: 'JP', country_iso2: 'JP', movies: 420, missing_movies: 84, missing_ratio: .2, top_missing_topics: [{topic: 'keywords', movies: 40}, {topic: 'overview', movies: 30}]}],
    by_genre: [{name: '剧情', genre_key: '剧情', movies: 900, missing_movies: 120, missing_ratio: .133, top_missing_topics: [{topic: 'credits', movies: 60}]}],
    by_decade: [{name: '2010s', decade: 2010, movies: 560, missing_movies: 70, missing_ratio: .125, top_missing_topics: [{topic: 'keywords', movies: 40}]}],
    unknown_country_movies: 197, note: '国家未知单独列出。',
  },
  problems: {
    total: 42, limit: 20, offset: 0, returned: 2, has_more: true, topic_filter: null, category_filter: null,
    items: [
      {movie_id: 9001, tmdb_id: 901, title: '样例缺失影片', release_year: 2012, topic: 'keywords', category: 'FAILED', actionable: true, latest_fetched_at: '2026-11-17T02:00:00Z', topic_observed_at: '2026-11-17T02:00:00Z', source: 'tmdb_supplement'},
      {movie_id: 9002, tmdb_id: 902, title: '样例空值影片', release_year: 2018, topic: 'overview', category: 'EMPTY', actionable: false, latest_fetched_at: '2026-11-17T02:00:00Z', topic_observed_at: '2026-11-17T02:00:00Z', source: 'tmdb_supplement'},
    ],
  },
  as_of: '2026-11-17T03:00:00Z', issues: [], notes: ['样例质量数据。'],
}
const batches = {
  total: 2, limit: 10, offset: 0, returned: 2, has_more: false, statuses: ['pending', 'running', 'succeeded', 'partial', 'failed'], source: 'tmdb_supplement_batch',
  items: [
    {batch_id: 31, source: 'tmdb_supplement', mode: 'incremental', status: 'succeeded', started_at: '2026-11-17T01:00:00Z', finished_at: '2026-11-17T01:20:00Z', reported_counts: {target_documents: 400, success_count: 392, failed_count: 3, skipped_count: 5}, counts_source: 'reported', item_status: {inserted: 200, updated: 192, skipped: 5, review: 0, error: 3, total: 400}, error_codes: [], error_summary: null, missing_fields: [], note: '样例批次。'},
    {batch_id: 30, source: 'tmdb_supplement', mode: 'backfill', status: 'partial', started_at: '2026-11-16T22:00:00Z', finished_at: '2026-11-16T23:10:00Z', reported_counts: {target_documents: 500, success_count: 430, failed_count: 40, skipped_count: 30}, counts_source: 'item_status', item_status: {inserted: 300, updated: 130, skipped: 30, review: 0, error: 40, total: 500}, error_codes: [{error_code: 'UPSTREAM_TIMEOUT', count: 30}, {error_code: 'RATE_LIMITED', count: 10}], error_summary: '上游超时', missing_fields: [], note: '样例批次。'},
  ],
}
const authUser = {id: 2, username: 'deepseek', is_admin: true}
function envelope(data) { return {code: 'OK', data} }

const browser = await chromium.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  headless: true,
})
const context = await browser.newContext({viewport: {width: 1440, height: 1000}, deviceScaleFactor: 1})
const page = await context.newPage()
const errors = [], checks = [], layout = []
page.on('pageerror', error => errors.push(error.message))
page.on('console', message => {
  if (message.type() !== 'error') return
  const text = message.text()
  // Missing posters and media are expected by fixture design; real request
  // failures still surface as console errors.
  if (text.includes('Failed to load resource')) return
  errors.push('console: ' + text)
})

await page.route('**/api/**', async route => {
  const url = route.request().url()
  if (!new URL(url).pathname.startsWith('/api/')) return route.continue()
  const json = data => route.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify(data)})
  if (url.includes('/api/v1/auth/csrf')) return json({code: 0, data: {csrf_token: 'smoke'}})
  if (url.includes('/api/v1/auth/me')) return json({code: 0, data: authUser})
  if (url.includes('/api/v1/profile') || url.includes('/api/v1/account')) return json({code: 0, data: {user_id: 2, avatar: '', version: 1}})
  if (url.includes('/api/admin/tmdb-supplement/analytics')) { analyticsCalls.push(url); return json(envelope(analytics(url))) }
  if (url.includes('/api/admin/tmdb-supplement/coverage')) return json(envelope({documents: 1883, topics: coverageBlock.topics, statuses: ['PRESENT', 'EMPTY', 'MISSING', 'NOT_FETCHED', 'FAILED'], observed_at: '2026-11-17T03:00:00Z'}))
  if (url.includes('/api/admin/tmdb-supplement/missing')) return json(envelope({limit: 50, offset: 0, returned: 2, has_more: false, items: [{movie_id: 9001, tmdb_id: 901, topic: 'keywords', status: 'FAILED', attempt_at: '2026-11-17T02:00:00Z', error_code: 'UPSTREAM_TIMEOUT'}, {movie_id: 9002, tmdb_id: 902, topic: 'credits', status: 'NOT_FETCHED', attempt_at: null, error_code: null}]}))
  if (url.includes('/api/admin/tmdb-supplement/quality')) {
    qualityCalls.push(url)
    const params = new URL(url).searchParams
    return json(envelope({...quality, denominator: {...quality.denominator, mode: params.get('denominator') === 'mapped' ? 'mapped' : 'active', movies: params.get('denominator') === 'mapped' ? 1883 : 1997}}))
  }
  if (url.includes('/api/admin/tmdb-supplement/batches')) {
    const status = new URL(url).searchParams.get('status')
    return json(envelope(status ? {...batches, items: batches.items.filter(item => item.status === status), total: batches.items.filter(item => item.status === status).length} : batches))
  }
  if (url.includes('/api/admin/recommendation-feedback')) return json(envelope(feedback))
  if (url.includes('/api/admin/dashboard')) return json(envelope(dashboard))
  if (url.includes('/api/admin/import-audit')) return json(envelope({items: [], total: 0, page: 1, size: 20}))
  if (url.includes('/api/admin/imports/quality/runs')) return json(envelope({status: 'COMPLETED', total: 0, completed: 0, items: [], error_code: null}))
  if (url.includes('/api/admin/imports/quality')) return json(envelope({counts: {active: 1997, mapped: 1883, identity_gaps: 12, pending: 3, reliable_tmdb_rating: 1167, known_revenue: 900, refresh_candidates: 40}, scope_digest: 'smoke'}))
  if (url.includes('/api/admin/imports')) return json(envelope({items: [], total: 0}))
  if (url.includes('/api/admin/events')) return json(envelope({items: [{at: '2026-11-18T12:00:00Z', user: 'deepseek', title: '样例影片', kind: '收藏', action: 'set'}]}))
  if (url.includes('/api/admin/users')) return json(envelope({items: [{id: 1, username: 'zhang', status: 'active', states: 12, watched: 5, last_event: '2026-11-18T12:00:00Z', created_at: '2026-01-01T00:00:00Z'}]}))
  if (url.includes('/api/admin/health')) return json(envelope({db_time: '2026-11-18T13:05:00Z', movies: 1997, m7_configured: true, admin: 'deepseek', config: {schema: 'mi2', activity_schema: 'mi2_activity'}}))
  if (url.includes('/api/admin/catalog')) return json(envelope({items: [{movie_id: 1001, title: '样例影片 1', year: 2015, rating: 8.6, genres: ['剧情'], countries: ['JP'], credits: 8, assets: 3}]}))
  if (url.includes('/api/catalog/movies/')) return route.fulfill({status: 404, contentType: 'application/json', body: '{"code":"NOT_FOUND","data":null}'})
  return route.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify(envelope({}))})
})

async function overflowSnapshot() {
  return page.evaluate(() => {
    const width = innerWidth
    const offenders = [...document.querySelectorAll('.admin-page *')]
      .filter(el => {
        const box = el.getBoundingClientRect()
        return box.width > 0 && (box.right > width + 1 || box.left < -1)
      })
      .slice(0, 6)
      .map(el => el.className && typeof el.className === 'string' ? el.className.split(' ')[0] : el.tagName)
    return {
      width,
      pageOverflow: document.documentElement.scrollWidth > innerWidth + 1,
      clipped: document.body.scrollWidth > innerWidth + 1,
      offenders,
    }
  })
}
async function open(query, readySelector, shot, expect = []) {
  await page.goto(base + '/admin' + query, {waitUntil: 'domcontentloaded'})
  try {
    await page.waitForSelector(readySelector, {timeout: 20000})
  } catch (error) {
    await page.screenshot({path: path.join(out, 'debug-' + shot), fullPage: true})
    console.log('DEBUG url=' + page.url())
    console.log('DEBUG text=' + (await page.locator('body').innerText()).slice(0, 600))
    console.log('DEBUG errors=' + JSON.stringify(errors))
    throw error
  }
  await page.waitForTimeout(700)
  const body = await page.locator('body').innerText()
  for (const snippet of expect) {
    if (!body.includes(snippet)) throw new Error(shot + ' missing expected copy: ' + snippet)
  }
  const tiny = await page.evaluate(() => {
    const found = []
    for (const element of document.querySelectorAll('.admin-page *')) {
      if (element.children.length || !element.textContent.trim()) continue
      const style = getComputedStyle(element)
      if (style.visibility === 'hidden' || style.display === 'none') continue
      const size = parseFloat(style.fontSize)
      if (size < 10) found.push({cls: String(element.className).split(' ')[0], size, text: element.textContent.trim().slice(0, 18)})
    }
    return found.slice(0, 12)
  })
  await page.screenshot({path: path.join(out, shot), fullPage: true})
  checks.push({shot, tinyText: tiny})
  layout.push({shot, wide: await overflowSnapshot()})
  await page.setViewportSize({width: 390, height: 844})
  await page.waitForTimeout(300)
  layout.push({shot, narrow: await overflowSnapshot()})
  await page.screenshot({path: path.join(out, shot.replace('.png', '-390.png')), fullPage: true})
  await page.setViewportSize({width: 1440, height: 1000})
  await page.waitForTimeout(200)
}

await open('', '.admin-shell', 'smoke-01-overview.png', ['电影资产', '用户', '推荐', 'AI 服务', '数据状态', '文档缓存 HIT', '不参与 TMDB 国家与评分统计'])
await open('?view=quick&country=JP', '.quick-drawer', 'smoke-02-quick-jp.png', ['日本', '类型 TOP5', '年代分布', '代表影片 TOP5', '资料覆盖与缺失', '深入分析该国家'])
await open('?view=deep&country=JP', '#deep-a', 'smoke-03-deep-jp.png', ['A. 国家总体', 'B. 类型 × 年代交叉矩阵', 'C. 口碑与热度', 'D. 主题与产业', 'E. 发行资料', 'F. 电影明细', '一键重置', '明细条件（仅 F）', '缺失任一项'])
await open('?view=compare&compare=CN,JP,US', '#compare-overlap', 'smoke-04-compare.png', ['重合与规模', '不是各国数量相加', '全组共同重合', 'country_samples', '国家指标对照', '选择国家'])
await open('?tab=catalog', '#assets-title', 'smoke-05-catalog.png', ['数据质量诊断', '主题覆盖矩阵', '问题清单', '最近补采批次', '操作审计 →', '样例缺失影片', '非问题（上游空值）', '内容主题与资产覆盖', '资产概况'])
await open('?tab=recommend', '#admin-recommend-title', 'smoke-06-recommend.png', ['推荐效果', '排序指标', '曝光'])

const interactions = []
await page.goto(base + '/admin', {waitUntil: 'domcontentloaded'})
await page.waitForSelector('.admin-shell')
await page.getByRole('button', {name: '电影库治理', exact: true}).click()
await page.waitForFunction(() => location.search.includes('tab=catalog'))
await page.waitForSelector('#assets-title')
interactions.push('tab click writes tab=catalog to URL')

await page.goto(base + '/admin?view=quick&country=JP', {waitUntil: 'domcontentloaded'})
await page.waitForSelector('.quick-drawer')
await page.getByRole('button', {name: /深入分析该国家/}).click()
await page.waitForSelector('#deep-a')
if (!page.url().includes('view=deep') || !page.url().includes('country=JP')) throw new Error('quick-to-deep URL not synced: ' + page.url())
interactions.push('quick drawer deep action writes view=deep&country=JP')

await page.goto(base + '/admin?view=compare&compare=CN,JP,US', {waitUntil: 'domcontentloaded'})
await page.waitForSelector('#compare-overlap')
await page.locator('.adm-panel').filter({hasText: '重合与规模'}).locator('tbody .link-btn').first().click()
await page.waitForSelector('.overlap-detail')
if (!(await page.locator('.overlap-detail').innerText()).includes('重合明细')) throw new Error('overlap detail did not load')
if (!analyticsCalls.some(url => url.includes('overlap=CN%2CJP') || url.includes('overlap=CN,JP'))) throw new Error('overlap request missing: ' + analyticsCalls.slice(-2).join(' | '))
interactions.push('pairwise overlap opens paginated backend details')

await page.goto(base + '/admin?view=deep&country=JP', {waitUntil: 'domcontentloaded'})
await page.waitForSelector('#deep-a')
await Promise.all([
  page.waitForResponse(response => response.url().includes('/analytics') && response.url().includes('keyword=1')),
  page.locator('section:has(#deep-d) .adm-bar.clickable').first().click(),
])
await page.waitForSelector('.detail-chip')
if (!(await page.locator('.detail-filters').innerText()).includes('关键词：童年')) throw new Error('keyword drill chip missing')
interactions.push('keyword bar drives details keyword filter')

await page.goto(base + '/admin?tab=catalog', {waitUntil: 'domcontentloaded'})
await page.waitForSelector('.quality-view')
await Promise.all([
  page.waitForResponse(response => response.url().includes('/quality') && response.url().includes('denominator=mapped')),
  page.locator('.adm-seg button', {hasText: 'mapped'}).click(),
])
await Promise.all([
  page.waitForResponse(response => response.url().includes('/quality') && response.url().includes('category=EMPTY')),
  page.locator('.quality-view .problem-filters select').nth(1).selectOption('EMPTY'),
])
await page.waitForSelector('.category-tag.cat-empty')
if (!(await page.locator('.quality-view').innerText()).includes('非问题（上游空值）')) throw new Error('EMPTY actionable distinction missing')
const qualityText = await page.locator('.quality-view').innerText()
if (!qualityText.includes('UNMAPPED')) throw new Error('UNMAPPED column missing from quality matrix')
if (!qualityText.includes('已完成')) throw new Error('batch status label missing')
if (!qualityCalls.some(url => url.includes('denominator=mapped'))) throw new Error('mapped denominator request missing')
interactions.push('quality denominator and EMPTY category filters hit /quality')

const report = {ok: errors.length === 0, screenshots: checks, interactions, layout, errors}
fs.writeFileSync(path.join(out, 'smoke-report.json'), JSON.stringify(report, null, 2))
console.log(JSON.stringify(report, null, 2))
await browser.close()
if (errors.length) process.exitCode = 1
const overflow = layout.filter(entry => entry.wide?.pageOverflow || entry.narrow?.pageOverflow)
if (overflow.length) {
  console.error('horizontal overflow: ' + JSON.stringify(overflow))
  process.exitCode = 1
}