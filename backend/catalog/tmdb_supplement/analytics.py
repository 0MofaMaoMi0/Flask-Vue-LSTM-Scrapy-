"""In-memory aggregation kernel for normalized TMDB supplement v1 documents.

Contract: consumes exactly the ``tmdb-supplement-v1`` documents produced by
``catalog.tmdb_supplement.normalize`` (see 协作契约 v1) and is pure: no network,
no configuration, no engine, no database, no clock.  The caller (server-side
admin API) feeds a materialized list of documents and receives a bounded,
JSON-ready payload; the complete per-movie payload is never echoed back.

Aggregation rules that the output keeps explicit instead of implicit:

* Country = TMDB ``production_countries`` only.  Company ``origin_country``,
  ``original_language`` and ``spoken_languages`` are never used to infer one.
* The denominator of ``share_of_filtered_catalog`` is the number of distinct
  movies after filters, including movies whose production country is unknown.
  A co-production is counted once per country, so shares may sum above 1.
* ``movie_id`` is the identity; duplicate documents for one movie are merged
  per topic: the newest *successful* fact wins (PRESENT/EMPTY), while FAILED,
  MISSING and NOT_FETCHED never overwrite an older valid fact.
* TMDB ratings are valid only with ``0 <= vote_average <= 10`` and
  ``vote_count >= min_votes``; 0 is a real score, missing is never 0.
* Fact dates (``release_date`` -> year/decade) and capture dates
  (``fetched_at`` -> ``observed_*``) are separate filter families and are
  reported with their own semantics.
* Trends contain only actually observed metric points; nothing is interpolated
  and no history is invented for dates that were never captured.
* Identity is one-to-one inside a snapshot: one ``movie_id`` may repeat with the
  same ``tmdb_id`` (topics are merged), but two different non-null ``tmdb_id``
  values for one movie, or one ``tmdb_id`` for two movies, raise
  :class:`IdentityConflictError` for manual review instead of silently keeping
  the newest mapping.
* Country quick-view rows are paginated (``country_offset``/``country_limit``,
  plus ``next_offset``) while ``countries.compact`` always lists every
  production country in the filtered scope, so a world map never loses the
  tail; neither form returns the movie list itself.

Depth items that the v1 document contract does not carry (watch providers,
budget/revenue depth analysis, collection membership) are reported as
unavailable rather than fabricated.

Wiring note for the server side (not part of the payload): this kernel maps to
SQL as ``tmdb_movie_countries``/``tmdb_movie_languages``/``tmdb_movie_keywords``
/``tmdb_movie_companies`` grouped by ``movie_id`` with ``COUNT(DISTINCT
movie_id)``, ``tmdb_metric_observations`` joined on ``(movie_id, observed_at)``
for the rating gate, and one ``movies`` join for title/year.  Suggested indexes:
``(country_iso2, movie_id)``, ``(company_id, movie_id)``, ``(keyword_id,
movie_id)``, ``(language_code, movie_id)``, ``(movie_id, observed_at)``,
``(genre_id, movie_id)``.  Fetch the bounded detail page with ``LIMIT/OFFSET``
on ``movie_id``; never select the raw JSON column for the dashboard response.
Keep ``UNIQUE(movie_id)`` on the current-metadata table and ``UNIQUE(tmdb_id)``
on the identity column so the kernel's one-to-one check is also enforced by the
database, and route both conflict shapes to the manual review queue.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from collections.abc import Mapping
from datetime import datetime, timezone

__all__ = [
    'aggregate_movies',
    'AnalyticsInputError',
    'IdentityConflictError',
    'SCHEMA_VERSION',
    'TOPICS',
    'STATUSES',
    'DEFAULT_MIN_VOTES',
    'MAX_COMPARE_COUNTRIES',
]

SCHEMA_VERSION = 'tmdb-supplement-v1'
CONTRACT = 'tmdb-supplement-analytics-v1'

TOPICS = (
    'base', 'countries', 'companies', 'spoken_languages',
    'keywords', 'releases', 'credits', 'metrics',
)
STATUSES = ('NOT_FETCHED', 'MISSING', 'EMPTY', 'PRESENT', 'FAILED')
SUCCESS_STATUSES = frozenset(('PRESENT', 'EMPTY'))

DEFAULT_MIN_VOTES = 50
MAX_COMPARE_COUNTRIES = 3
DEFAULT_TOP = 10
MAX_TOP = 50
MAX_COUNTRY_ROWS = 60
MAX_COUNTRY_LIMIT = 200
MAX_COUNTRY_OFFSET = 1000
MAX_COMPACT_COUNTRIES = 300
MAX_IDENTITY_CONFLICTS = 20
DEFAULT_DETAIL_LIMIT = 20
MAX_DETAIL_LIMIT = 100
MAX_DETAIL_OFFSET = 10000
MAX_OBSERVATIONS = 24
MAX_DETAIL_OBSERVATIONS = 6
MAX_RELEASE_SAMPLES = 20

MIN_YEAR, MAX_YEAR = 1800, 2199

FILTER_KEYS = frozenset((
    'country', 'countries', 'genre', 'genres', 'decade', 'decades',
    'year_from', 'year_to', 'observed_from', 'observed_to', 'country_known',
    'limit', 'offset', 'top', 'country_offset', 'country_limit',
))
_SYNONYMS = (('country', 'countries'), ('genre', 'genres'), ('decade', 'decades'))

_ISO2 = re.compile(r'^[A-Z]{2}$')
_ISO639 = re.compile(r'^[a-z]{2,3}$')
_DATE = re.compile(r'^\d{4}-\d{2}-\d{2}$')

_RATING_BUCKETS = ('0–2', '2–4', '4–6', '6–8', '8–10')
_RELEASE_TYPES = {1: 'premiere', 2: 'limited', 3: 'theatrical', 4: 'digital',
                  5: 'physical', 6: 'tv'}

NOTE_BASIS = (
    '统计为规范化文档的内存聚合，未连接数据库，不代表真实库统计。',
    '国家仅取 TMDB production_countries；公司来源国家、原始语言和对白语言都不用于推断制作国家。',
    '占比分母为同筛选去重电影数（含国家未知影片）；合拍片在每个国家各计一次，各国占比之和可能大于 100%。',
    'TMDB 评分仅统计 0..10 且 vote_count>=min_votes 的样本；0 分是有效值，缺失不补 0，不与站内评分混算。',
    'unweighted_mean 为影片评分的简单平均，vote_weighted_mean 按票数加权；两者口径不同，需分别标注。',
    '上映年来自事实日期 release_date；“已采集”类筛选使用采集时间 fetched_at（UTC），两者不混用。',
    '趋势只包含实际采集到的观测点，不插值，也不为未采集的日期补造历史。',
    'popularity 仅为 TMDB 热度指数，不是播放量或观看人数。',
    '导演/演员只取可信 credit 字段；主题缺失或未采集的电影不进入对应分母，覆盖率中单独显示。',
    '明细为有界摘要，不返回原始 payload、简介全文或完整演职员数组。',
    '主题选择以 field_status 为唯一依据：FAILED/MISSING/NOT_FETCHED 即使附带部分可用值也不作为成功事实，'
    '避免用可疑批次覆盖已有效数据。',
    '同一快照内 movie_id 与非空 tmdb_id 必须一对一：出现歧义直接抛 IdentityConflictError，'
    '不按最新值静默择一，由上游转人工复核。',
    '国家速览分页只影响列表长度；compact 摘要始终包含筛选范围内全部制作国家，不随 top 截断，也不回调电影明细。',
)

_ISSUE_MESSAGES = {
    'INVALID_COUNTRY_ENTRY': '忽略不符合两位 ISO 3166-1 代码的制作国家条目。',
    'INVALID_COMPANY_ENTRY': '忽略缺少正整数 TMDB 公司 ID 的制作公司条目。',
    'INVALID_LANGUAGE_ENTRY': '忽略不符合两位/三位小写 ISO 639 代码的对白语言条目。',
    'INVALID_KEYWORD_ENTRY': '忽略缺少正整数 TMDB 关键词 ID 的关键词条目。',
    'INVALID_RELEASE_RECORD': '忽略日期格式非法的地区上映记录。',
    'INVALID_GENRE_ENTRY': '忽略无法识别 id/名称的类型条目。',
    'INVALID_METRIC': '忽略越界或非数值的 TMDB 指标；不把非法值当作真实统计。',
    'INVALID_FACT_SHAPE': '主题事实形状与 v1 契约不符，按空数据处理。',
    'INVALID_TOPIC_STATUS': '主题状态不是契约固定的五种值，按 NOT_FETCHED 处理。',
    'MISSING_TOPIC_STATUS': '文档 field_status 未声明该主题，按 NOT_FETCHED 处理。',
    'CREDIT_ROLE_UNKNOWN': 'credit 条目既非 cast 也非 crew，未计入导演/演员统计。',
    'CREDIT_PERSON_MISSING': 'credit 条目缺少可辨识 person id，未计入排行。',
    'DUPLICATE_MOVIE_DOCUMENTS': '同一 movie_id 存在多份文档，已按主题选择较新的成功事实。',
    'COUNTRY_METADATA_UNKNOWN': '筛选范围内存在未补齐制作国家的电影；它们计入占比分母但不进入国家集合。',
    'RATING_SAMPLE_INSUFFICIENT': '存在票数低于门槛或指标缺失的电影；它们不计入有效评分样本。',
    'CREDITS_UNAVAILABLE': '演职员主题缺失或未采集，导演/演员深度项标记为不可用。',
    'RELEASES_UNAVAILABLE': '地区上映主题缺失或所选国家无上映记录，时间差不可计算。',
    'COUNTRY_NOT_FOUND': '所选比较国家在筛选范围内没有任何电影，按 0 计数而非缺失。',
    'GENRE_METADATA_MISSING': '部分电影没有类型事实，类型分布覆盖率低于影片数。',
}


class AnalyticsInputError(ValueError):
    """Raised when documents, filters, pagination or the snapshot are invalid."""


class IdentityConflictError(AnalyticsInputError):
    """Raised when movie_id and a non-null tmdb_id are not one-to-one.

    The kernel never picks the newest mapping silently: a conflict means the
    upstream catalog needs manual review, so the whole aggregate is refused
    with a bounded, machine-readable conflict report.
    """

    code = 'TMDB_IDENTITY_CONFLICT'

    def __init__(self, movie_conflicts, tmdb_conflicts):
        self.conflicts = {
            'movies': list(movie_conflicts[:MAX_IDENTITY_CONFLICTS]),
            'tmdb_ids': list(tmdb_conflicts[:MAX_IDENTITY_CONFLICTS]),
            'movie_conflict_count': len(movie_conflicts),
            'tmdb_conflict_count': len(tmdb_conflicts),
        }
        parts = []
        for item in movie_conflicts[:MAX_IDENTITY_CONFLICTS]:
            parts.append('movie_id=%d -> tmdb_id=%s'
                         % (item['movie_id'], item['tmdb_ids']))
        for item in tmdb_conflicts[:MAX_IDENTITY_CONFLICTS]:
            parts.append('tmdb_id=%d -> movie_id=%s'
                         % (item['tmdb_id'], item['movie_ids']))
        message = (
            '%s: 同一快照内 movie_id 与非空 tmdb_id 不是一对一，未做静默合并'
            '（%d 个 movie_id 冲突，%d 个 tmdb_id 冲突），请转人工复核: %s'
            % (self.code, len(movie_conflicts), len(tmdb_conflicts),
               '; '.join(parts))
        )
        super().__init__(message)


# ---------------------------------------------------------------- primitives

def _first(mapping, keys):
    for key in keys:
        value = mapping.get(key)
        if value is not None:
            return value
    return None


def _text(value, limit=300):
    if isinstance(value, str):
        value = value.strip()
        return value[:limit] if value else None
    return None


def _positive_int(value, label):
    if type(value) is not int or value < 1 or value > 9223372036854775807:
        raise AnalyticsInputError('Invalid ' + label)
    return value


def _bounded_year(value):
    if type(value) is not int or not MIN_YEAR <= value <= MAX_YEAR:
        return None
    return value


def _utc_datetime(value, label):
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            raise AnalyticsInputError('Invalid ' + label)
        try:
            parsed = datetime.fromisoformat(text.replace('Z', '+00:00'))
        except ValueError:
            raise AnalyticsInputError('Invalid ' + label) from None
    else:
        raise AnalyticsInputError('Invalid ' + label)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AnalyticsInputError(label + ' must include a timezone')
    return parsed.astimezone(timezone.utc)


def _iso(moment):
    if moment is None:
        return None
    return moment.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def _number(value):
    if type(value) is bool or type(value) not in (int, float):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _year_from(raw):
    year = _bounded_year(raw.get('release_year'))
    if year is not None:
        return year
    date = _text(raw.get('release_date'), 10)
    if date and _DATE.match(date):
        try:
            return _bounded_year(int(date[:4]))
        except ValueError:
            return None
    return None


def _decade(year):
    if type(year) is not int:
        return None
    return (year // 10) * 10


class _IssueLog:
    """Deterministic counter of tolerated data defects; never invents facts."""

    def __init__(self):
        self._counts = Counter()

    def bump(self, code, count=1):
        self._counts[code] += count

    def as_list(self):
        return [
            {'code': code, 'count': int(self._counts[code]),
             'message': _ISSUE_MESSAGES.get(code, '')}
            for code in sorted(self._counts)
        ]


# ------------------------------------------------------- document ingestion

def _document(entry, position, log):
    if not isinstance(entry, Mapping):
        raise AnalyticsInputError('Document %d is not a mapping' % position)
    if entry.get('schema_version') != SCHEMA_VERSION:
        raise AnalyticsInputError(
            'Document %d is not %s' % (position, SCHEMA_VERSION))
    movie_id = _positive_int(entry.get('movie_id'), 'movie_id')
    fetched_at = _utc_datetime(entry.get('fetched_at'), 'fetched_at')
    tmdb_id = entry.get('tmdb_id')
    tmdb_id = None if tmdb_id is None else _positive_int(tmdb_id, 'tmdb_id')
    status = entry.get('field_status')
    facts = entry.get('facts')
    status = {} if status is None else status
    facts = {} if facts is None else facts
    if not isinstance(status, Mapping):
        raise AnalyticsInputError('Invalid field_status for movie %d' % movie_id)
    if not isinstance(facts, Mapping):
        raise AnalyticsInputError('Invalid facts for movie %d' % movie_id)
    return {
        'movie_id': movie_id,
        'tmdb_id': tmdb_id,
        'fetched_at': fetched_at,
        'job_id': _text(entry.get('job_id'), 64),
        'payload_sha256': _text(entry.get('payload_sha256'), 64),
        'facts': facts,
        'topics': {topic: _topic_status(status, topic, log) for topic in TOPICS},
    }


def _topic_status(status, topic, log):
    raw = status.get(topic)
    if raw is None:
        log.bump('MISSING_TOPIC_STATUS')
        return 'NOT_FETCHED'
    if not isinstance(raw, str):
        log.bump('INVALID_TOPIC_STATUS')
        return 'NOT_FETCHED'
    value = raw.strip().upper()
    if value not in STATUSES:
        log.bump('INVALID_TOPIC_STATUS')
        return 'NOT_FETCHED'
    return value


def _document_order(document):
    return (document['fetched_at'], document['payload_sha256'] or '',
            document['job_id'] or '', document['tmdb_id'] or 0)


def _list_fact(raw, log):
    if raw is None:
        return []
    if not isinstance(raw, (list, tuple)):
        log.bump('INVALID_FACT_SHAPE')
        return []
    return list(raw)


def _country_facts(raw, log):
    """Production countries only; anything else is never substituted."""
    found = {}
    for item in _list_fact(raw, log):
        if isinstance(item, Mapping):
            code = _first(item, ('iso_3166_1', 'iso2', 'country_iso2', 'code'))
            name = _first(item, ('name', 'country_name', 'english_name'))
        elif isinstance(item, str):
            code, name = item, None
        else:
            code, name = None, None
        if not isinstance(code, str) or not _ISO2.match(code.strip().upper()):
            log.bump('INVALID_COUNTRY_ENTRY')
            continue
        iso2 = code.strip().upper()
        label = _text(name, 120) or iso2
        if iso2 not in found or found[iso2] == iso2:
            found[iso2] = label
    return found


def _company_facts(raw, log):
    found = {}
    for item in _list_fact(raw, log):
        if not isinstance(item, Mapping):
            log.bump('INVALID_COMPANY_ENTRY')
            continue
        company_id = item.get('company_id', item.get('id'))
        if type(company_id) is not int or company_id < 1:
            log.bump('INVALID_COMPANY_ENTRY')
            continue
        origin = item.get('origin_country')
        origin = origin.strip().upper() if isinstance(origin, str) else None
        if origin is not None and not _ISO2.match(origin):
            origin = None
        found[company_id] = {
            'company_id': company_id,
            'name': _text(item.get('name'), 160) or 'company:%d' % company_id,
            'origin_country': origin,
            'logo_path': _text(item.get('logo_path'), 256),
        }
    return found


def _language_facts(raw, log):
    """Spoken languages, kept strictly apart from original_language."""
    found = {}
    for item in _list_fact(raw, log):
        if not isinstance(item, Mapping):
            log.bump('INVALID_LANGUAGE_ENTRY')
            continue
        code = _first(item, ('iso_639_1', 'iso_639', 'code'))
        code = code.strip().lower() if isinstance(code, str) else ''
        if not _ISO639.match(code):
            log.bump('INVALID_LANGUAGE_ENTRY')
            continue
        found[code] = {
            'code': code,
            'english_name': _text(item.get('english_name'), 120),
            'name': _text(item.get('name'), 120),
        }
    return found


def _keyword_facts(raw, log):
    """Keyword identity is the TMDB id; names are attributes, never keys."""
    found = {}
    for item in _list_fact(raw, log):
        if not isinstance(item, Mapping):
            log.bump('INVALID_KEYWORD_ENTRY')
            continue
        keyword_id = item.get('keyword_id', item.get('id'))
        if type(keyword_id) is not int or keyword_id < 1:
            log.bump('INVALID_KEYWORD_ENTRY')
            continue
        found[keyword_id] = {
            'keyword_id': keyword_id,
            'name': _text(item.get('name'), 160) or 'keyword:%d' % keyword_id,
        }
    return found


def _release_facts(raw, log):
    """Flatten country containers into explicit release records."""
    records = []
    for item in _list_fact(raw, log):
        if not isinstance(item, Mapping):
            log.bump('INVALID_RELEASE_RECORD')
            continue
        country = _first(item, ('iso_3166_1', 'iso2', 'country_iso2', 'code'))
        if not isinstance(country, str) or not _ISO2.match(country.strip().upper()):
            log.bump('INVALID_RELEASE_RECORD')
            continue
        country = country.strip().upper()
        nested = item.get('release_dates')
        entries = nested if isinstance(nested, (list, tuple)) else [item]
        for entry in entries:
            if not isinstance(entry, Mapping):
                log.bump('INVALID_RELEASE_RECORD')
                continue
            date = _text(entry.get('release_date'), 10)
            if not date or not _DATE.match(date):
                log.bump('INVALID_RELEASE_RECORD')
                continue
            try:
                datetime.strptime(date, '%Y-%m-%d')
            except ValueError:
                log.bump('INVALID_RELEASE_RECORD')
                continue
            release_type = entry.get('type')
            release_type = release_type if type(release_type) is int else None
            language = _first(entry, ('iso_639_1', 'language'))
            language = language.strip().lower() if isinstance(language, str) else None
            records.append({
                'country_iso2': country,
                'release_date': date,
                'type': release_type,
                'type_name': _RELEASE_TYPES.get(release_type),
                'certification': _text(entry.get('certification'), 32),
                'note': _text(entry.get('note'), 160),
                'language': language if language and _ISO639.match(language) else None,
            })
    return records


def _credit_facts(raw, log):
    """Only reliable credit fields: role marker, person id, job/character."""
    found = []
    for item in _list_fact(raw, log):
        if not isinstance(item, Mapping):
            log.bump('CREDIT_ROLE_UNKNOWN')
            continue
        person = item.get('person') if isinstance(item.get('person'), Mapping) else {}
        person_id = item.get('person_id', person.get('id'))
        if type(person_id) is not int or person_id < 1:
            log.bump('CREDIT_PERSON_MISSING')
            continue
        role = _first(item, ('role_type', 'credit_type', 'type', 'kind'))
        role = role.strip().lower() if isinstance(role, str) else None
        if role not in ('cast', 'crew'):
            role = ('cast' if item.get('character') or item.get('character_name')
                    else 'crew' if (item.get('job') or item.get('department')) else None)
        if role is None:
            log.bump('CREDIT_ROLE_UNKNOWN')
            continue
        order = _first(item, ('order', 'sort_order'))
        found.append({
            'role': role,
            'person_id': person_id,
            'name': _text(_first(item, ('person_name', 'name'))
                          or person.get('name'), 120),
            'character': _text(_first(item, ('character', 'character_name')), 160),
            'job': _text(item.get('job'), 120),
            'department': _text(item.get('department'), 120),
            'order': order if type(order) is int else None,
        })
    return found


def _metric_facts(raw, log):
    raw = raw if isinstance(raw, Mapping) else {}
    average = raw.get('vote_average')
    count = raw.get('vote_count')
    popularity = raw.get('popularity')

    average_value = None
    if average is not None:
        number = _number(average)
        if number is None or not 0.0 <= number <= 10.0:
            log.bump('INVALID_METRIC')
        else:
            average_value = number
    count_value = None
    if count is not None:
        if type(count) is not int or count < 0:
            log.bump('INVALID_METRIC')
        else:
            count_value = count
    popularity_value = _number(popularity)
    if popularity is not None and popularity_value is None:
        log.bump('INVALID_METRIC')
    return {
        'vote_average': average_value,
        'vote_count': count_value,
        'popularity': popularity_value,
    }


def _base_facts(raw, log):
    raw = raw if isinstance(raw, Mapping) else {}
    genres = []
    seen = set()
    for item in _list_fact(raw.get('genres'), log):
        if isinstance(item, Mapping):
            genre_id = item.get('genre_id', item.get('id'))
            genre_id = genre_id if type(genre_id) is int and genre_id > 0 else None
            name = _text(_first(item, ('name', 'name_zh', 'name_en')), 120)
        elif type(item) is int and item > 0:
            genre_id, name = item, None
        elif isinstance(item, str) and item.strip():
            genre_id, name = None, item.strip()[:120]
        else:
            log.bump('INVALID_GENRE_ENTRY')
            continue
        if genre_id is None and name is None:
            log.bump('INVALID_GENRE_ENTRY')
            continue
        key = ('id', genre_id) if genre_id is not None else ('name', name.casefold())
        if key in seen:
            continue
        seen.add(key)
        genres.append({'genre_id': genre_id, 'name': name})
    runtime = raw.get('runtime_minutes', raw.get('runtime'))
    runtime = runtime if type(runtime) is int and runtime >= 0 else None
    budget = raw.get('budget')
    revenue = raw.get('revenue')
    budget = budget if type(budget) is int and budget >= 0 else None
    revenue = revenue if type(revenue) is int and revenue >= 0 else None
    # Normalizer emits explicit *_known flags; a 0 amount means unknown.
    budget_known = raw.get('budget_known')
    if type(budget_known) is bool:
        budget = budget if budget_known else None
    else:
        budget = budget if budget else None
    revenue_known = raw.get('revenue_known')
    if type(revenue_known) is bool:
        revenue = revenue if revenue_known else None
    else:
        revenue = revenue if revenue else None
    original_language = raw.get('original_language')
    original_language = (original_language.strip().lower()
                         if isinstance(original_language, str) else None)
    if original_language is not None and not _ISO639.match(original_language):
        original_language = None
    return {
        'title': _text(raw.get('title') or raw.get('original_title')),
        'release_year': _year_from(raw),
        'original_language': original_language,
        'genres': genres,
        'runtime': runtime,
        'has_overview': bool(_text(raw.get('overview'), 2000)),
        'has_poster': bool(_text(raw.get('poster_path'), 256)),
        'has_backdrop': bool(_text(raw.get('backdrop_path'), 256)),
        # Budget/revenue 0 means unknown; it is never a verified amount.
        'budget': budget if budget else None,
        'revenue': revenue if revenue else None,
    }


def _merge_movie(movie_id, documents, log):
    """Newest successful fact per topic; failures and gaps never overwrite."""
    topic_status = {}
    topic_observed = {}
    chosen_documents = {}
    for topic in TOPICS:
        normalized = [document['topics'][topic] for document in documents]
        winner = None
        for document, status in zip(documents, normalized):
            if status in SUCCESS_STATUSES:
                winner = (document, status)
                break
        if winner is not None:
            topic_status[topic] = winner[1]
            topic_observed[topic] = winner[0]['fetched_at']
            chosen_documents[topic] = winner[0]
        elif 'FAILED' in normalized:
            topic_status[topic] = 'FAILED'
        elif 'MISSING' in normalized:
            topic_status[topic] = 'MISSING'
        else:
            topic_status[topic] = 'NOT_FETCHED'

    def fact(topic):
        document = chosen_documents.get(topic)
        if document is None:
            return None
        return document['facts'].get(topic)

    base = _base_facts(fact('base'), log)
    countries = _country_facts(fact('countries'), log)
    companies = _company_facts(fact('companies'), log)
    languages = _language_facts(fact('spoken_languages'), log)
    keywords = _keyword_facts(fact('keywords'), log)
    releases = _release_facts(fact('releases'), log)
    credits = _credit_facts(fact('credits'), log)
    metrics = _metric_facts(fact('metrics'), log)

    observations = []
    seen_observations = set()
    for document in documents:
        if document['topics']['metrics'] not in SUCCESS_STATUSES:
            continue
        candidate = _metric_facts(document['facts'].get('metrics'), log)
        if candidate['vote_average'] is None or candidate['vote_count'] is None:
            continue
        observed_at = _iso(document['fetched_at'])
        if observed_at in seen_observations:
            continue
        seen_observations.add(observed_at)
        observations.append({
            'observed_at': observed_at,
            'vote_average': candidate['vote_average'],
            'vote_count': candidate['vote_count'],
            'popularity': candidate['popularity'],
        })
    observations.sort(key=lambda row: row['observed_at'] or '')
    observations = observations[-MAX_OBSERVATIONS:]

    observed_times = [moment for moment in topic_observed.values() if moment is not None]
    return {
        'movie_id': movie_id,
        'tmdb_id': next((document['tmdb_id'] for document in documents
                         if document['tmdb_id']), None),
        'document_count': len(documents),
        'latest_fetched_at': documents[0]['fetched_at'],
        'observed_at': max(observed_times) if observed_times else None,
        'topic_status': topic_status,
        'topic_observed_at': {topic: _iso(moment)
                              for topic, moment in topic_observed.items()},
        'base': base,
        'title': base['title'],
        'release_year': base['release_year'],
        'genres': base['genres'],
        'original_language': base['original_language'],
        'countries': countries,
        'companies': companies,
        'spoken_languages': languages,
        'keywords': keywords,
        'releases': releases,
        'credits': credits,
        'metrics': metrics,
        'observations': observations,
    }


def _identity_conflicts(grouped):
    """One-to-one check on non-null tmdb_id within the snapshot window.

    A movie may legitimately have several documents, and an unmapped movie may
    have no tmdb_id at all.  But one movie cannot carry two TMDB identities,
    and one TMDB identity cannot point at two movies; either case is a review
    item, never a "newest wins" merge.
    """
    movie_conflicts = []
    for movie_id in sorted(grouped):
        tmdb_ids = sorted({document['tmdb_id'] for document in grouped[movie_id]
                           if document['tmdb_id'] is not None})
        if len(tmdb_ids) > 1:
            movie_conflicts.append({'movie_id': movie_id, 'tmdb_ids': tmdb_ids})
    reverse = defaultdict(set)
    for movie_id, documents in grouped.items():
        for document in documents:
            if document['tmdb_id'] is not None:
                reverse[document['tmdb_id']].add(movie_id)
    tmdb_conflicts = [{'tmdb_id': tmdb_id, 'movie_ids': sorted(movie_ids)}
                      for tmdb_id, movie_ids in sorted(reverse.items())
                      if len(movie_ids) > 1]
    return movie_conflicts, tmdb_conflicts


def _load(documents, cutoff, log):
    if documents is None:
        return [], 0, 0, 0
    if isinstance(documents, Mapping) or isinstance(documents, (str, bytes)):
        raise AnalyticsInputError('documents must be an iterable of documents')
    entries = list(documents)
    grouped = defaultdict(list)
    skipped = 0
    for position, entry in enumerate(entries):
        document = _document(entry, position, log)
        if cutoff is not None and document['fetched_at'] > cutoff:
            skipped += 1
            continue
        grouped[document['movie_id']].append(document)
    movie_conflicts, tmdb_conflicts = _identity_conflicts(grouped)
    if movie_conflicts or tmdb_conflicts:
        raise IdentityConflictError(movie_conflicts, tmdb_conflicts)
    movies = []
    duplicates = 0
    for movie_id in sorted(grouped):
        ordered = sorted(grouped[movie_id], key=_document_order, reverse=True)
        duplicates += len(ordered) - 1
        movies.append(_merge_movie(movie_id, ordered, log))
    if duplicates:
        log.bump('DUPLICATE_MOVIE_DOCUMENTS', duplicates)
    return movies, len(entries), duplicates, skipped


# --------------------------------------------------------------- filters

def _iso2_list(value, label):
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        raise AnalyticsInputError('Invalid ' + label)
    codes = []
    for item in value:
        if not isinstance(item, str) or not _ISO2.match(item.strip().upper()):
            raise AnalyticsInputError('Invalid ' + label)
        code = item.strip().upper()
        if code not in codes:
            codes.append(code)
    return codes


def _genre_terms(value):
    if isinstance(value, (str, int)) and type(value) is not bool:
        value = [value]
    if not isinstance(value, (list, tuple)):
        raise AnalyticsInputError('Invalid filter genre')
    ids, names = set(), set()
    for item in value:
        if type(item) is int and item > 0:
            ids.add(item)
        elif isinstance(item, str) and item.strip():
            names.add(item.strip().casefold())
        else:
            raise AnalyticsInputError('Invalid filter genre')
    return ids, names


def _decade_terms(value):
    if isinstance(value, (str, int)) and type(value) is not bool:
        value = [value]
    if not isinstance(value, (list, tuple)):
        raise AnalyticsInputError('Invalid filter decade')
    decades = set()
    for item in value:
        if type(item) is int:
            decade = item
        elif isinstance(item, str):
            text = item.strip().lower()
            if text.endswith('年代'):
                text = text[:-2]
            if text.endswith('s'):
                text = text[:-1]
            if not text.isdigit():
                raise AnalyticsInputError('Invalid filter decade')
            decade = int(text)
        else:
            raise AnalyticsInputError('Invalid filter decade')
        if decade % 10 != 0 or not MIN_YEAR <= decade <= MAX_YEAR:
            raise AnalyticsInputError('Invalid filter decade')
        decades.add(decade)
    return decades


def _resolve_filters(filters):
    if filters is None:
        filters = {}
    if not isinstance(filters, Mapping):
        raise AnalyticsInputError('filters must be a mapping')
    unknown = sorted(set(filters) - FILTER_KEYS)
    if unknown:
        raise AnalyticsInputError(
            'Unknown filter field(s): ' + ', '.join(unknown))
    for short, long in _SYNONYMS:
        if short in filters and long in filters:
            raise AnalyticsInputError(
                'Conflicting filter fields: %s/%s' % (short, long))

    resolved = {
        'year_from': None, 'year_to': None,
        'observed_from': None, 'observed_to': None,
        'country_known': None, 'countries': [],
        'genres': (set(), set()), 'decades': set(),
        'limit': DEFAULT_DETAIL_LIMIT, 'offset': 0, 'top': DEFAULT_TOP,
        'country_limit': MAX_COUNTRY_ROWS, 'country_offset': 0,
    }
    raw_countries = filters.get('countries', filters.get('country'))
    if raw_countries is not None:
        resolved['countries'] = _iso2_list(raw_countries, 'filter country')

    genre_param = filters.get('genres', filters.get('genre'))
    if genre_param is not None:
        resolved['genres'] = _genre_terms(genre_param)

    decade_param = filters.get('decades', filters.get('decade'))
    if decade_param is not None:
        resolved['decades'] = _decade_terms(decade_param)

    for key in ('year_from', 'year_to'):
        if key in filters:
            resolved[key] = _bounded_year(filters[key])
            if resolved[key] is None:
                raise AnalyticsInputError('Invalid filter ' + key)
    if (resolved['year_from'] is not None and resolved['year_to'] is not None
            and resolved['year_from'] > resolved['year_to']):
        raise AnalyticsInputError('Inverted year range')

    for key in ('observed_from', 'observed_to'):
        if key in filters:
            resolved[key] = _utc_datetime(filters[key], 'filter ' + key)
    if (resolved['observed_from'] is not None
            and resolved['observed_to'] is not None
            and resolved['observed_from'] > resolved['observed_to']):
        raise AnalyticsInputError('Inverted observed range')

    if 'country_known' in filters:
        if type(filters['country_known']) is not bool:
            raise AnalyticsInputError('Invalid filter country_known')
        resolved['country_known'] = filters['country_known']

    if 'limit' in filters:
        limit = filters['limit']
        if type(limit) is not int or not 1 <= limit <= MAX_DETAIL_LIMIT:
            raise AnalyticsInputError('Pagination bounds exceeded')
        resolved['limit'] = limit
    if 'offset' in filters:
        offset = filters['offset']
        if type(offset) is not int or not 0 <= offset <= MAX_DETAIL_OFFSET:
            raise AnalyticsInputError('Pagination bounds exceeded')
        resolved['offset'] = offset
    if 'top' in filters:
        top = filters['top']
        if type(top) is not int or not 1 <= top <= MAX_TOP:
            raise AnalyticsInputError('Invalid top size')
        resolved['top'] = top
    if 'country_limit' in filters:
        country_limit = filters['country_limit']
        if type(country_limit) is not int or not 1 <= country_limit <= MAX_COUNTRY_LIMIT:
            raise AnalyticsInputError('Invalid country_limit')
        resolved['country_limit'] = country_limit
    if 'country_offset' in filters:
        country_offset = filters['country_offset']
        if (type(country_offset) is not int
                or not 0 <= country_offset <= MAX_COUNTRY_OFFSET):
            raise AnalyticsInputError('Invalid country_offset')
        resolved['country_offset'] = country_offset
    return resolved


def _genre_tokens(movie):
    ids, names = set(), set()
    for genre in movie['genres']:
        if genre['genre_id'] is not None:
            ids.add(genre['genre_id'])
        if genre['name']:
            names.add(genre['name'].casefold())
    return ids, names


def _in_scope(movie, resolved):
    countries = resolved['countries']
    if countries and not any(iso in movie['countries'] for iso in countries):
        return False
    genre_ids, genre_names = resolved['genres']
    if genre_ids or genre_names:
        ids, names = _genre_tokens(movie)
        if not (ids & genre_ids or names & genre_names):
            return False
    if resolved['decades']:
        decade = _decade(movie['release_year'])
        if decade is None or decade not in resolved['decades']:
            return False
    if resolved['year_from'] is not None or resolved['year_to'] is not None:
        year = movie['release_year']
        if year is None:
            return 'unknown_year'
        if resolved['year_from'] is not None and year < resolved['year_from']:
            return False
        if resolved['year_to'] is not None and year > resolved['year_to']:
            return False
    if resolved['country_known'] is not None:
        if bool(movie['countries']) is not resolved['country_known']:
            return False
    if resolved['observed_from'] is not None or resolved['observed_to'] is not None:
        moment = movie['observed_at']
        if moment is None:
            return 'unknown_observed'
        if resolved['observed_from'] is not None and moment < resolved['observed_from']:
            return False
        if resolved['observed_to'] is not None and moment > resolved['observed_to']:
            return False
    return True


def _select(movies, resolved):
    kept, excluded, unknown_year, unknown_observed = [], 0, 0, 0
    for movie in movies:
        verdict = _in_scope(movie, resolved)
        if verdict is True:
            kept.append(movie)
        elif verdict == 'unknown_year':
            unknown_year += 1
        elif verdict == 'unknown_observed':
            unknown_observed += 1
        else:
            excluded += 1
    return kept, {
        'excluded': excluded,
        'excluded_unknown_release_year': unknown_year,
        'excluded_unknown_observed_at': unknown_observed,
    }


def _filter_semantics():
    return {
        'countries': {'kind': 'fact', 'field': 'facts.countries.iso_3166_1',
                      'note': '仅制作国家，OR 语义，合拍片任一国家命中即可。'},
        'genres': {'kind': 'fact', 'field': 'facts.base.genres',
                   'note': '按类型 id 或名称匹配，OR 语义。'},
        'decades': {'kind': 'fact', 'field': 'facts.base.release_date',
                    'note': '上映年代，OR 语义；无上映日的电影不命中任何年代。'},
        'year_from': {'kind': 'fact', 'field': 'facts.base.release_date',
                      'note': '上映年份下界；无上映日的电影不命中。'},
        'year_to': {'kind': 'fact', 'field': 'facts.base.release_date',
                    'note': '上映年份上界；无上映日的电影不命中。'},
        'observed_from': {'kind': 'capture', 'field': 'fetched_at',
                          'timezone': 'UTC',
                          'note': '采集时间下界，取该电影当前有效事实的最新成功采集时间。'},
        'observed_to': {'kind': 'capture', 'field': 'fetched_at',
                        'timezone': 'UTC',
                        'note': '采集时间上界，与上映日期无关。'},
        'country_known': {'kind': 'fact', 'field': 'facts.countries',
                          'note': 'true 仅含有制作国家的电影，false 仅含国家未知电影。'},
        'country_limit': {'kind': 'response',
                          'note': '国家速览分页大小（1..200，默认 60）；只影响列表长度，不影响任何统计口径。'},
        'country_offset': {'kind': 'response',
                           'note': '国家速览分页偏移（0..1000）；compact 摘要始终完整，不需要分页即可读全。'},
    }


# ------------------------------------------------------------ aggregation

def _rating_sample(movie, min_votes):
    metrics = movie['metrics']
    average, count = metrics['vote_average'], metrics['vote_count']
    if average is None or count is None:
        return None
    if count < min_votes:
        return None
    return average, count


def _rating_summary(movies, min_votes):
    samples = []
    below = 0
    for movie in movies:
        sample = _rating_sample(movie, min_votes)
        if sample is None:
            if (movie['metrics']['vote_average'] is not None
                    and movie['metrics']['vote_count'] is not None):
                below += 1
            continue
        samples.append(sample)
    unweighted = None
    weighted = None
    if samples:
        unweighted = round(sum(average for average, _ in samples) / len(samples), 2)
        votes = sum(count for _, count in samples)
        if votes:
            weighted = round(sum(average * count for average, count in samples) / votes, 2)
    return {
        'source': 'tmdb',
        'scale': '0-10',
        'min_votes': min_votes,
        'valid_samples': len(samples),
        'total_votes': sum(count for _, count in samples),
        'unweighted_mean': unweighted,
        'vote_weighted_mean': weighted,
        'below_min_votes_movies': below,
    }


def _decade_distribution(movies):
    counts = Counter()
    for movie in movies:
        counts[_decade(movie['release_year'])] += 1
    rows = [{'decade': decade, 'name': '%d年代' % decade, 'count': counts[decade]}
            for decade in sorted(key for key in counts if key is not None)]
    if None in counts:
        rows.append({'decade': None, 'name': '年份未知', 'count': counts[None]})
    return rows


def _top_counter(counter, key_name, limit):
    ordered = sorted(counter.items(), key=lambda item: (-item[1], str(item[0])))
    return [{key_name: key, 'count': count} for key, count in ordered[:limit]]


def _genre_key(genre):
    return ('id', genre['genre_id']) if genre['genre_id'] is not None \
        else ('name', genre['name'].casefold())


def _genre_counts(movies):
    counts = Counter()
    labels = {}
    for movie in movies:
        for genre in movie['genres']:
            key = _genre_key(genre)
            labels[key] = (genre['name'] or 'genre:%d' % genre['genre_id'],
                           genre['genre_id'])
            counts[key] += 1
    return counts, labels


def _top_genres(movies, limit):
    counts, labels = _genre_counts(movies)
    ordered = sorted(counts.items(), key=lambda item: (-item[1], labels[item[0]][0]))
    return [{'genre': labels[key][0], 'genre_id': labels[key][1], 'count': count}
            for key, count in ordered[:limit]]


def _genre_by_decade(movies, limit):
    counts, labels = _genre_counts(movies)
    matrix = defaultdict(Counter)
    for movie in movies:
        decade = _decade(movie['release_year'])
        for genre in movie['genres']:
            matrix[_genre_key(genre)][decade] += 1
    ordered = sorted(counts.items(), key=lambda item: (-item[1], labels[item[0]][0]))
    rows = []
    for key, total in ordered[:limit]:
        decades = matrix[key]
        series = [{'decade': decade, 'count': decades[decade]}
                  for decade in sorted(d for d in decades if d is not None)]
        if None in decades:
            series.append({'decade': None, 'count': decades[None]})
        rows.append({'genre': labels[key][0], 'genre_id': labels[key][1],
                     'total': total, 'decades': series})
    return rows


def _language_split(movies, limit):
    original = Counter()
    spoken = Counter()
    for movie in movies:
        if movie['original_language']:
            original[movie['original_language']] += 1
        for code in movie['spoken_languages']:
            spoken[code] += 1
    return {
        'original_language': _top_counter(original, 'code', limit),
        'spoken_languages': _top_counter(spoken, 'code', limit),
        'note': '原始语言来自 movies.original_language；对白语言来自 spoken_languages，两者不合并。',
    }


def _company_rank(movies, limit):
    counts = Counter()
    meta = {}
    for movie in movies:
        for company_id, company in movie['companies'].items():
            counts[company_id] += 1
            meta[company_id] = company
    ordered = sorted(counts.items(), key=lambda item: (-item[1], meta[item[0]]['name']))
    return [{'company_id': company_id, 'name': meta[company_id]['name'],
             'origin_country': meta[company_id]['origin_country'],
             'movie_count': count}
            for company_id, count in ordered[:limit]]


def _keyword_rank(movies, limit):
    counts = Counter()
    names = {}
    for movie in movies:
        for keyword_id, keyword in movie['keywords'].items():
            counts[keyword_id] += 1
            names[keyword_id] = keyword['name']
    ordered = sorted(counts.items(), key=lambda item: (-item[1], names[item[0]]))
    return [{'keyword_id': keyword_id, 'name': names[keyword_id],
             'movie_count': count}
            for keyword_id, count in ordered[:limit]]


def _credit_block(movies, limit):
    status_counts = Counter(movie['topic_status']['credits'] for movie in movies)
    if status_counts.get('PRESENT'):
        status = 'PRESENT'
    elif status_counts.get('EMPTY'):
        status = 'EMPTY'
    elif status_counts.get('FAILED'):
        status = 'FAILED'
    elif status_counts.get('MISSING'):
        status = 'MISSING'
    else:
        status = 'NOT_FETCHED'
    covered = [movie for movie in movies
               if movie['topic_status']['credits'] == 'PRESENT' and movie['credits']]
    directors = Counter()
    director_names = {}
    director_works = defaultdict(list)
    cast = Counter()
    cast_names = {}
    cast_works = defaultdict(list)
    for movie in covered:
        for credit in movie['credits']:
            if credit['role'] == 'crew' and (credit['job'] or '').casefold() == 'director':
                directors[credit['person_id']] += 1
                director_names[credit['person_id']] = (credit['name']
                                                       or 'person:%d' % credit['person_id'])
                director_works[credit['person_id']].append(
                    movie['title'] or 'movie:%d' % movie['movie_id'])
            elif credit['role'] == 'cast':
                cast[credit['person_id']] += 1
                cast_names[credit['person_id']] = (credit['name']
                                                   or 'person:%d' % credit['person_id'])
                cast_works[credit['person_id']].append(
                    movie['title'] or 'movie:%d' % movie['movie_id'])

    def rows(counter, names, works):
        ordered = sorted(counter.items(), key=lambda item: (-item[1], names[item[0]]))
        return [{'person_id': person_id, 'name': names[person_id],
                 'movie_count': count, 'top_movies': works[person_id][:5]}
                for person_id, count in ordered[:limit]]

    return {
        'status': status,
        'topic_status': {name: status_counts.get(name, 0) for name in STATUSES},
        'movies_with_credits': len(covered),
        'directors': rows(directors, director_names, director_works),
        'cast': rows(cast, cast_names, cast_works),
        'note': '仅使用 credit 的 role_type/job/character/order；导演限 crew job=Director。'
                ' 缺演职员数据的电影不进入排行分母，覆盖见 topic_status。',
    }


def _quantiles(values):
    ordered = sorted(values)
    if not ordered:
        return {'samples': 0, 'p25': None, 'p50': None, 'p75': None}
    result = {'samples': len(ordered)}
    for label, point in zip(('p25', 'p50', 'p75'), (0.25, 0.5, 0.75)):
        index = max(0, min(len(ordered) - 1, math.ceil(point * len(ordered)) - 1))
        result[label] = ordered[index]
    return result


def _rating_distribution(movies, min_votes):
    histogram = Counter()
    counts, popularity = [], []
    for movie in movies:
        sample = _rating_sample(movie, min_votes)
        if sample is None:
            continue
        average, count = sample
        counts.append(count)
        if movie['metrics']['popularity'] is not None:
            popularity.append(movie['metrics']['popularity'])
        bucket = '8–10' if average >= 8 else '6–8' if average >= 6 else \
            '4–6' if average >= 4 else '2–4' if average >= 2 else '0–2'
        histogram[bucket] += 1
    return {
        'rating_histogram': [{'name': bucket, 'count': histogram.get(bucket, 0)}
                             for bucket in _RATING_BUCKETS],
        'vote_count_quantiles': _quantiles(counts),
        'popularity_quantiles': _quantiles(popularity),
        'note': '仅统计有效评分样本；popularity 是 TMDB 热度指数，不是观看量。',
    }


def _release_window(movies, iso2):
    covered, samples = 0, []
    for movie in movies:
        records = movie['releases']
        if not records:
            continue
        own = [record['release_date'] for record in records
               if record['country_iso2'] == iso2]
        if not own:
            continue
        covered += 1
        earliest = min(record['release_date'] for record in records)
        own_earliest = min(own)
        lag = (datetime.strptime(own_earliest, '%Y-%m-%d')
               - datetime.strptime(earliest, '%Y-%m-%d')).days
        samples.append({'movie_id': movie['movie_id'],
                        'country_release_date': own_earliest,
                        'earliest_release_date': earliest,
                        'lag_days': lag})
    if not covered:
        return {'status': 'NO_RELEASE_DATA', 'covered_movies': 0,
                'median_lag_days': None, 'samples': [],
                'note': '所选国家没有来源上映记录；不推断发行时间。'}
    samples.sort(key=lambda row: row['movie_id'])
    lags = sorted(sample['lag_days'] for sample in samples)
    index = max(0, min(len(lags) - 1, math.ceil(0.5 * len(lags)) - 1))
    return {'status': 'PRESENT', 'covered_movies': covered,
            'median_lag_days': lags[index], 'samples': samples[:MAX_RELEASE_SAMPLES],
            'note': '时间差为本国最早上映日相对该片全球最早上映日的天数；仅实际记录，不插值。'}


def _movie_row(movie, min_votes):
    observations = movie['observations']
    return {
        'movie_id': movie['movie_id'],
        'tmdb_id': movie['tmdb_id'],
        'title': movie['title'],
        'release_year': movie['release_year'],
        'decade': _decade(movie['release_year']),
        'genres': [genre['name'] or 'genre:%d' % genre['genre_id']
                   for genre in movie['genres'][:6]],
        'countries': sorted(movie['countries'])[:6],
        'country_known': bool(movie['countries']),
        'original_language': movie['original_language'],
        'spoken_languages': sorted(movie['spoken_languages'])[:6],
        'vote_average': movie['metrics']['vote_average'],
        'vote_count': movie['metrics']['vote_count'],
        'popularity': movie['metrics']['popularity'],
        'rating_sample_valid': _rating_sample(movie, min_votes) is not None,
        'rating_observation_count': len(observations),
        'rating_observations': observations[-MAX_DETAIL_OBSERVATIONS:],
        'observed_at': _iso(movie['observed_at']),
        'latest_fetched_at': _iso(movie['latest_fetched_at']),
        'topic_status': dict(movie['topic_status']),
    }


def _details(movies, resolved, min_votes):
    ordered = sorted(movies, key=lambda movie: movie['movie_id'])
    offset, limit = resolved['offset'], resolved['limit']
    page = ordered[offset:offset + limit]
    return {
        'total': len(ordered),
        'limit': limit,
        'offset': offset,
        'returned': len(page),
        'has_more': offset + len(page) < len(ordered),
        'items': [_movie_row(movie, min_votes) for movie in page],
        'note': '分页按 movie_id 升序；仅返回有界摘要，不含原始 payload、简介全文或完整演职员；'
                '评分观测仅列最近 %d 个实际采集点。' % MAX_DETAIL_OBSERVATIONS,
    }


def _top_movies(movies, limit, min_votes):
    voted, rated, popular = [], [], []
    for movie in movies:
        metrics = movie['metrics']
        if metrics['vote_count'] is not None:
            voted.append(movie)
        if metrics['popularity'] is not None:
            popular.append(movie)
        if _rating_sample(movie, min_votes) is not None:
            rated.append(movie)
    voted.sort(key=lambda movie: (-(movie['metrics']['vote_count'] or 0),
                                 movie['movie_id']))
    rated.sort(key=lambda movie: (-(movie['metrics']['vote_average'] or 0),
                                  -(movie['metrics']['vote_count'] or 0),
                                  movie['movie_id']))
    popular.sort(key=lambda movie: (-(movie['metrics']['popularity'] or 0),
                                    movie['movie_id']))

    def summary(movie):
        return {'movie_id': movie['movie_id'], 'tmdb_id': movie['tmdb_id'],
                'title': movie['title'], 'release_year': movie['release_year'],
                'vote_average': movie['metrics']['vote_average'],
                'vote_count': movie['metrics']['vote_count'],
                'popularity': movie['metrics']['popularity']}

    return ([summary(movie) for movie in voted[:limit]],
            [summary(movie) for movie in rated[:limit]],
            [summary(movie) for movie in popular[:limit]])


def _coverage(movies, min_votes):
    total = len(movies)
    known = sum(1 for movie in movies if movie['countries'])
    valid = sum(1 for movie in movies if _rating_sample(movie, min_votes) is not None)
    topic_counts = {topic: {status: 0 for status in STATUSES} for topic in TOPICS}
    for movie in movies:
        for topic in TOPICS:
            topic_counts[topic][movie['topic_status'][topic]] += 1

    def ratio(count):
        return round(count / total, 4) if total else None

    assets = {
        'runtime': sum(1 for movie in movies if movie['base']['runtime'] is not None),
        'overview': sum(1 for movie in movies if movie['base']['has_overview']),
        'poster': sum(1 for movie in movies if movie['base']['has_poster']),
        'backdrop': sum(1 for movie in movies if movie['base']['has_backdrop']),
        'budget_nonzero': sum(1 for movie in movies if movie['base']['budget'] is not None),
        'revenue_nonzero': sum(1 for movie in movies if movie['base']['revenue'] is not None),
        'budget_note': '预算/收入 0 视为未知，不估算利润。',
    }
    return {
        'movies': total,
        'countries': {'known': known, 'unknown': total - known,
                      'share_known': ratio(known),
                      'topic_status': dict(topic_counts['countries'])},
        'ratings': {'valid_samples': valid, 'unknown': total - valid,
                    'share_valid': ratio(valid), 'min_votes': min_votes},
        'genres': {'movies_with_genres':
                   sum(1 for movie in movies if movie['genres'])},
        'keywords': {'movies_with_keywords':
                     sum(1 for movie in movies if movie['keywords'])},
        'companies': {'movies_with_companies':
                      sum(1 for movie in movies if movie['companies'])},
        'credits': {'movies_with_credits':
                    sum(1 for movie in movies if movie['credits'])},
        'releases': {'movies_with_releases':
                     sum(1 for movie in movies if movie['releases'])},
        'topics': topic_counts,
        'assets': assets,
    }


def _states(seen_documents, used_documents, matched, coverage):
    if seen_documents == 0:
        catalog = 'NO_DOCUMENTS'
    elif used_documents == 0:
        catalog = 'NO_DOCUMENTS_AT_OBSERVED_AT'
    elif matched == 0:
        catalog = 'NO_MATCHES_IN_SCOPE'
    else:
        catalog = 'HAS_MOVIES'
    if catalog != 'HAS_MOVIES':
        countries = ratings = catalog
    else:
        countries = ('NO_COUNTRY_METADATA' if coverage['countries']['known'] == 0
                     else 'PARTIAL' if coverage['countries']['unknown']
                     else 'COMPLETE')
        ratings = ('NO_VALID_RATING_SAMPLE' if coverage['ratings']['valid_samples'] == 0
                   else 'PARTIAL' if coverage['ratings']['unknown']
                   else 'COMPLETE')
    return {'catalog': catalog, 'countries': countries, 'ratings': ratings,
            'note': '“无收录”“有电影但国家未知”“有电影无有效评分”为三种独立状态，不互相代替。'}


def _country_rows(movies, min_votes, top):
    index = defaultdict(list)
    names = {}
    for movie in movies:
        for iso2, name in movie['countries'].items():
            index[iso2].append(movie)
            if iso2 not in names or names[iso2] == iso2:
                names[iso2] = name
    total = len(movies)
    rows = []
    for iso2 in sorted(index):
        subset = index[iso2]
        rows.append({
            'country_iso2': iso2,
            'country_name': names.get(iso2, iso2),
            'movie_count_distinct': len(subset),
            'share_of_filtered_catalog': round(len(subset) / total, 4) if total else None,
            'rated_movie_count': sum(1 for movie in subset
                                     if _rating_sample(movie, min_votes) is not None),
            'tmdb_avg_rating': _rating_summary(subset, min_votes),
            'top_genres': _top_genres(subset, min(5, top)),
            'decade_distribution': _decade_distribution(subset),
        })
    rows.sort(key=lambda row: (-row['movie_count_distinct'], row['country_iso2']))
    return rows, names, index


def _deep_country(iso2, name, subset, total_matched, min_votes, top):
    rated = sum(1 for movie in subset if _rating_sample(movie, min_votes) is not None)
    top_voted, top_rated, _ = _top_movies(subset, top, min_votes)
    return {
        'country_iso2': iso2,
        'country_name': name,
        'movie_count_distinct': len(subset),
        'share_of_filtered_catalog': round(len(subset) / total_matched, 4)
        if total_matched else None,
        'rated_movie_count': rated,
        'tmdb_avg_rating': _rating_summary(subset, min_votes),
        'top_genres': _top_genres(subset, top),
        'genre_by_decade': _genre_by_decade(subset, top),
        'decade_distribution': _decade_distribution(subset),
        'languages': _language_split(subset, top),
        'companies': _company_rank(subset, top),
        'keywords': _keyword_rank(subset, top),
        'credits': _credit_block(subset, top),
        'release_window': _release_window(subset, iso2),
        'rating_distribution': _rating_distribution(subset, min_votes),
        'top_movies': top_voted,
        'top_rated_movies': top_rated,
    }


def _countries_block(rows, matched, resolved):
    """Bounded country quick-view page plus an always-complete compact summary."""
    offset = resolved['country_offset']
    limit = resolved['country_limit']
    page = rows[offset:offset + limit]
    more = offset + len(page) < len(rows)
    compact = [
        {'country_iso2': row['country_iso2'],
         'country_name': row['country_name'],
         'movie_count_distinct': row['movie_count_distinct'],
         'share_of_filtered_catalog': row['share_of_filtered_catalog'],
         'rated_movie_count': row['rated_movie_count']}
        for row in rows
    ]
    return {
        'total': len(rows),
        'offset': offset,
        'limit': limit,
        'returned': len(page),
        'has_more': more,
        'next_offset': offset + len(page) if more else None,
        'truncated': more,
        'rows': page,
        'compact': {
            'total': len(rows),
            'returned': min(len(compact), MAX_COMPACT_COUNTRIES),
            'truncated': len(compact) > MAX_COMPACT_COUNTRIES,
            'items': compact[:MAX_COMPACT_COUNTRIES],
            'note': '紧凑全球摘要：按 movie_count 降序包含筛选范围内全部国家（无 top 明细），'
                    '用于地图/世界分布，不需要分页即可读全，也不含电影明细。',
        },
        'denominator': {'movies': len(matched),
                        'note': '占比分母为同筛选去重电影数，含国家未知影片。'},
    }


def _comparison(movies, requested, min_votes, log):
    sets = {}
    for iso2 in requested:
        member = [movie for movie in movies if iso2 in movie['countries']]
        sets[iso2] = {movie['movie_id'] for movie in member}
        if not member:
            log.bump('COUNTRY_NOT_FOUND')
    union = set()
    for members in sets.values():
        union |= members
    multi = [movie for movie in movies
             if sum(1 for iso2 in requested if iso2 in movie['countries']) > 1]
    pairs = []
    for index, first in enumerate(requested):
        for second in requested[index + 1:]:
            overlap = sets[first] & sets[second]
            pair_union = sets[first] | sets[second]
            pairs.append({
                'countries': [first, second],
                'intersection_movie_count': len(overlap),
                'union_movie_count': len(pair_union),
                'jaccard': round(len(overlap) / len(pair_union), 4) if pair_union else None,
                'shared_movie_ids': sorted(overlap)[:20],
            })
    return {
        'countries': requested,
        'union_movie_count': len(union),
        'overlap_movie_count': len(multi),
        'overlap_ratio_of_union': round(len(multi) / len(union), 4) if union else None,
        'pairwise': pairs,
        'note': '最多比较 3 国，同一筛选与同一评分口径；合拍片可同时属于多国，'
                '重合数按 movie_id 去重，并集同样去重。'
                ' 每国深度行与 country_analysis.rows 顺序一致，不在此重复。',
    }


# ---------------------------------------------------------------- entrypoint

def aggregate_movies(documents=None, *, filters=None, countries=None,
                     min_votes=DEFAULT_MIN_VOTES, observed_at=None):
    """Aggregate normalized v1 documents into a bounded analytics payload.

    ``documents`` is any iterable of ``tmdb-supplement-v1`` mappings (a movie
    may appear several times with different ``fetched_at``).  ``filters``
    accepts only the documented keys; unknown keys raise
    :class:`AnalyticsInputError` instead of being silently ignored.
    ``countries`` selects 1..3 ISO 3166-1 codes for the deep/comparison view.
    ``observed_at`` is an optional tz-aware snapshot cutoff applied to
    ``fetched_at``.
    """
    if type(min_votes) is not int or not 0 <= min_votes <= 1000000:
        raise AnalyticsInputError('Invalid min_votes')
    cutoff = _utc_datetime(observed_at, 'observed_at') if observed_at is not None else None
    resolved = _resolve_filters(filters)
    requested = [] if countries is None else _iso2_list(countries, 'countries')
    if len(requested) > MAX_COMPARE_COUNTRIES:
        raise AnalyticsInputError('At most %d countries can be compared'
                                  % MAX_COMPARE_COUNTRIES)
    top = resolved['top']

    log = _IssueLog()
    movies, seen_documents, duplicates, skipped = _load(documents, cutoff, log)
    matched, excluded = _select(movies, resolved)

    coverage = _coverage(matched, min_votes)
    used_documents = sum(movie['document_count'] for movie in movies)
    states = _states(seen_documents, used_documents, len(matched), coverage)

    if coverage['countries']['unknown']:
        log.bump('COUNTRY_METADATA_UNKNOWN', coverage['countries']['unknown'])
    if coverage['ratings']['unknown']:
        log.bump('RATING_SAMPLE_INSUFFICIENT', coverage['ratings']['unknown'])
    if coverage['genres']['movies_with_genres'] < len(matched):
        log.bump('GENRE_METADATA_MISSING',
                 len(matched) - coverage['genres']['movies_with_genres'])
    if matched and not coverage['credits']['movies_with_credits']:
        log.bump('CREDITS_UNAVAILABLE')
    if matched and not coverage['releases']['movies_with_releases']:
        log.bump('RELEASES_UNAVAILABLE')

    rows, names, index = _country_rows(matched, min_votes, top)
    split = _language_split(matched, top)
    top_voted, top_rated, top_popular = _top_movies(matched, top, min_votes)
    global_block = {
        'movie_count_distinct': len(matched),
        'movies_with_countries': coverage['countries']['known'],
        'movies_country_unknown': coverage['countries']['unknown'],
        'rated_movie_count': coverage['ratings']['valid_samples'],
        'tmdb_avg_rating': _rating_summary(matched, min_votes),
        'top_genres': _top_genres(matched, top),
        'decade_distribution': _decade_distribution(matched),
        'original_language': split['original_language'],
        'spoken_languages': split['spoken_languages'],
        'top_movies': top_voted,
        'top_rated_movies': top_rated,
        'top_popular_movies': top_popular,
        'rating_distribution': _rating_distribution(matched, min_votes),
        'assets': coverage['assets'],
    }

    output = {
        'contract': CONTRACT,
        'consumes': SCHEMA_VERSION,
        'filters': {
            'countries': resolved['countries'],
            'genres': {'ids': sorted(resolved['genres'][0]),
                       'names': sorted(resolved['genres'][1])},
            'decades': sorted(resolved['decades']),
            'year_from': resolved['year_from'],
            'year_to': resolved['year_to'],
            'observed_from': _iso(resolved['observed_from']),
            'observed_to': _iso(resolved['observed_to']),
            'country_known': resolved['country_known'],
            'country_limit': resolved['country_limit'],
            'country_offset': resolved['country_offset'],
        },
        'filter_semantics': _filter_semantics(),
        'snapshot': {
            'observed_at': _iso(cutoff),
            'latest_fetched_at': _iso(max((movie['latest_fetched_at']
                                           for movie in matched), default=None)),
            'earliest_fetched_at': _iso(min((movie['observed_at'] for movie in matched
                                             if movie['observed_at'] is not None),
                                            default=None)),
            'note': 'observed_at 为调用方给定的采集截止时间；未给出时不截断。',
        },
        'scope': {
            'documents_seen': seen_documents,
            'documents_skipped_after_observed_at': skipped,
            'documents_used': used_documents,
            'duplicate_documents_merged': duplicates,
            'movies_total': len(movies),
            'movies_matched': len(matched),
            **excluded,
        },
        'coverage': coverage,
        'states': states,
        'global': global_block,
        'countries': _countries_block(rows, matched, resolved),
        'country_analysis': None,
        'comparison': None,
        'details': _details(matched, resolved, min_votes),
        'issues': None,
        'notes': list(NOTE_BASIS),
    }
    if requested:
        selected = []
        for iso2 in requested:
            subset = index.get(iso2, [])
            if not subset:
                log.bump('COUNTRY_NOT_FOUND')
            selected.append(_deep_country(iso2, names.get(iso2, iso2), subset,
                                          len(matched), min_votes, top))
        output['country_analysis'] = {
            'denominator': len(matched),
            'rows': selected,
            'note': '单国深度分析；口径与全球/国家速览一致。',
        }
        if len(requested) > 1:
            output['comparison'] = _comparison(matched, requested, min_votes, log)
    output['issues'] = log.as_list()
    return output