"""Shared read-only recommendation channels. No model calls or catalog mutations."""
import hashlib,json,math,os,threading,time
from collections import Counter, OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import nullcontext
from heapq import nlargest
from pathlib import Path
from datetime import datetime,timedelta,timezone
from weakref import WeakKeyDictionary
from sqlalchemy import and_, case, func, inspect, literal, or_, select
from .recommendation_pipeline import rank_movies
from .recommendation_data import (_load_candidates, archived_scalar_map, attach_backdrops,
                                  finalize, load_candidates_readonly, sql_feature_projection,
                                  sql_scalar_projection, valid_number)
from .models import CatalogBase
from . import public_catalog_cache
from assistant.conditions import genre_groups,matches,validate_filters

VERSION='boards-m10-v2'
CHANNELS={
 'popular':'热门推荐','trending_day':'今日热榜','trending_week':'本周热榜',
 'daily':'每日为你推荐','weekly':'每周精选','top_rated':'高分口碑',
 'revenue':'票房精选','because':'因为你喜欢……','watchlist':'想看清单精选','personal':'为你推荐'}
PUBLIC={'popular','trending_day','trending_week'}
DESCRIPTIONS={
 'popular':'TMDB Popular 官方榜单，展示原始名次与采集时间。',
 'trending_day':'TMDB 今日趋势，反映平台关注变化，不等于日票房。',
 'trending_week':'TMDB 本周趋势，保留官方来源排名。',
 'daily':'结合你的偏好、可靠评分和热门候选，同一天保持相对稳定。',
 'weekly':'在个人偏好之外探索更多类型与年代，同周保持相对稳定。',
 'top_rated':'结合 TMDB 评分与投票人数，采用贝叶斯平滑，抑制少量投票的高分。',
 'revenue':'按库内有效累计收入排序，单位美元；不是实时或期间票房。',
 'because':'从你的收藏和4星以上评分出发，寻找同导演、同演员或类型相近的电影。',
 'watchlist':'只从你的想看清单挑选，结合类型、时长等条件决定今晚看什么。',
 'personal':'根据你的收藏、想看与评分推荐。'}
DATA=Path(__file__).with_name('data')
_lock=threading.Lock()

def period_for(channel,now):
    local=now.astimezone(timezone(timedelta(hours=8)))
    date=local.date()
    return (date-timedelta(days=date.weekday())).isoformat() if channel=='weekly' else date.isoformat()

def _period_key(user, channel, period, options):
    return hashlib.sha256(json.dumps([VERSION,str(user),channel,period,options],
                                     sort_keys=True,separators=(',',':')).encode()).hexdigest()


def _period_options(filters, keyword, min_votes, weights):
    return dict(filters={key:value for key,value in filters.items() if value not in (None,[],False)},
                keyword=keyword, min_votes=min_votes, weights=weights)


def _read_period_ids(target, period):
    if target.is_symlink() or target.parent.is_symlink():raise ValueError('Unsafe cache')
    if not target.is_file():return []
    doc=json.loads(target.read_text(encoding='utf-8'))
    if doc.get('version')!=VERSION or doc.get('period')!=period:return []
    ids=doc.get('ids',[])
    if type(ids) is not list or any(type(mid) is not int or mid<1 for mid in ids):raise ValueError('Invalid period cache')
    return ids

def persisted_order(folder,user,channel,period,filters,ordered):
    """Cache only IDs, never credentials or behavior payloads; fail explicitly if unwritable."""
    if folder is None:return ordered,'DETERMINISTIC_ONLY'
    key=_period_key(user,channel,period,filters)
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True,mode=0o700)
    if folder.is_symlink():raise ValueError('Unsafe cache')
    target=folder/(key+'.json')
    with _lock:
        # Kernel lock also serializes different WSGI processes on the Linux host.
        lock=folder/(key+'.lock')
        with lock.open('a+b') as stream:
            os.chmod(lock,0o600)
            try:
                import fcntl
                fcntl.flock(stream.fileno(),fcntl.LOCK_EX)
            except ImportError:pass
            if target.is_symlink() or lock.is_symlink():raise ValueError('Unsafe cache')
            ordered=list(dict.fromkeys(_read_period_ids(target,period)+ordered))
            tmp=folder/(key+'.'+str(os.getpid())+'.tmp')
            tmp.write_text(json.dumps({'version':VERSION,'period':period,'ids':ordered}),encoding='utf-8');os.chmod(tmp,0o600);os.replace(tmp,target)
    return ordered,'PERSISTED_PERIOD'

def public_snapshot(channel,*,online=True):
    from .tmdb_connection import current
    context=current()
    from .board_snapshots import read
    scheduled=read(channel)
    if context:
        service,user,binding=context
        latest=service.snapshot(channel,user if online else None,binding)
        if latest:
            if scheduled and datetime.fromisoformat(scheduled['captured_at'])>datetime.fromisoformat(latest['captured_at']):return scheduled
            return latest
    if scheduled:return scheduled
    path=DATA/(channel+'.json')
    if not path.is_file():return None
    try:
        doc=json.loads(path.read_text(encoding='utf-8'))
        stamp=datetime.fromisoformat(doc['captured_at'])
        if doc.get('schema')!=1 or doc.get('channel')!=channel or not isinstance(doc.get('items'),list) or stamp.tzinfo is None:return None
    except (ValueError,KeyError,TypeError):return None
    return doc

def context_stamp():
    now=datetime.now(timezone.utc)
    return [VERSION,period_for('daily',now),period_for('weekly',now),[(c,(public_snapshot(c,online=False) or {}).get('captured_at')) for c in sorted(PUBLIC)]]

def external_movie(row,genres):
    if type(row.get('id')) is not int or row['id']<1 or row.get('adult') is True:return None
    date=row.get('release_date') or ''
    if not isinstance(row.get('title'),str) or not row['title']:return None
    path=row.get('poster_path');poster=None
    import re
    from urllib.parse import quote
    if isinstance(path,str) and re.fullmatch(r'/[A-Za-z0-9_-]+\.(?:jpg|png|webp)',path,re.I):poster='/api/media/poster?u='+quote('https://image.tmdb.org/t/p/w342'+path,safe='')
    votes=row.get('vote_count');score=row.get('vote_average')
    return {'movie_id':None,'tmdb_id':str(row['id']),'title':row['title'],'original_title':row.get('original_title'),
        'release_year':int(date[:4]) if len(date)>=4 and date[:4].isdigit() else None,'release_date':date,
        'runtime_minutes':None,'original_language':row.get('original_language'),'genres':[genres[g] for g in row.get('genre_ids',[]) if g in genres],
        'poster':poster,'overview':str(row.get('overview') or '')[:2000],
        'tmdb_rating':score if valid_number(score,10) and type(votes) is int and votes>0 else None,'vote_count':votes if type(votes) is int and votes>=0 else None,
        'tmdb_popularity':row.get('popularity') if valid_number(row.get('popularity')) else None}

def board(movies,state,user,channel='personal',*,limit=12,filters=None,keyword='',min_votes=None,scope='all',cache_dir=None,now=None,weights=None,trusted_filters=False):
    filters, now, weights = _board_arguments(channel, limit, filters, keyword, min_votes,
                                            scope, now, weights, trusted_filters)
    return _board(movies, state, user, channel, limit=limit, filters=filters, keyword=keyword,
                  min_votes=min_votes, scope=scope, cache_dir=cache_dir, now=now, weights=weights)


def _board_arguments(channel, limit, filters, keyword, min_votes, scope, now, weights, trusted_filters):
    if channel not in CHANNELS or type(limit) is not int or not 1<=limit<=50 or scope not in ('all','local'):raise ValueError('Channel arguments')
    if type(keyword) is not str or len(keyword)>100:raise ValueError('Keyword')
    if min_votes is not None and (type(min_votes) is not int or not 0<=min_votes<=10000000):raise ValueError('Votes')
    filters=dict(filters or {}) if trusted_filters else validate_filters(filters or {});now=now or datetime.now(timezone.utc)
    weights={'favorite':3.,'want':2.,'rating':1.} if weights is None else weights
    if set(weights)!={'favorite','want','rating'} or any(type(v) not in (int,float) or not math.isfinite(v) or not 0<=v<=10 for v in weights.values()):raise ValueError('Weights')
    if now.tzinfo is None:raise ValueError('Aware time required')
    return filters, now, weights


_UNREAD_SNAPSHOT = object()


def _board(movies, state, user, channel, *, limit, filters, keyword, min_votes, scope,
           cache_dir, now, weights, snapshot=_UNREAD_SNAPSHOT, rating_mean=None):
    active=[m for m in movies if m.get('catalog_status')=='active' and m.get('is_adult') is not True]
    byid={m['movie_id']:m for m in active};bytmdb={str(m['tmdb_id']):m for m in active if m.get('tmdb_id')}
    personal=channel in ('daily','weekly','because','personal')
    def allowed(m):
        s=state.get(str(m.get('movie_id')),{})
        return matches(m,filters,s) and (not personal or not s.get('watched') or filters.get('unwatched') is False) and (not keyword or keyword.casefold() in (m['title']+' '+(m.get('original_title') or '')).casefold()) and (min_votes is None or (m.get('vote_count') is not None and m['vote_count']>=min_votes))
    result={'channel':channel,'title':CHANNELS[channel],'description':DESCRIPTIONS[channel],'algorithm_version':VERSION,'scope':'LOCAL_CATALOG','catalog_total':len(active),
        'as_of':now.isoformat(),'period':None,'timezone':'Asia/Shanghai','paid_model_tokens':0,'items':[],
        'popularity_covered':sum(m.get('tmdb_popularity') is not None for m in active),'status':'OK','limitations':[]}
    result['mode']='MYSQL_CATALOG_POPULAR' if channel=='popular' else 'MYSQL_AUTHENTICATED_RECOMMENDATION' if channel=='personal' else 'MYSQL_RECOMMENDATION_CHANNEL'
    ranked=[];reasons={};scores={};source_ranks={};matching_count=None;ranking_evidence={}
    if channel in PUBLIC:
        # Browsing uses published snapshots; explicit TMDB refresh and scheduled
        # jobs update them without tying a catalog GET to external network work.
        if snapshot is _UNREAD_SNAPSHOT:snapshot=public_snapshot(channel,online=False)
        if snapshot is None:return dict(result,status='UNAVAILABLE',source='TMDB_OFFICIAL_SNAPSHOT',limitations=['尚未取得该频道官方快照，不以其他榜单替代。'])
        captured=datetime.fromisoformat(snapshot['captured_at']);age=(now-captured).total_seconds()
        result.update(source='TMDB_OFFICIAL_SNAPSHOT',source_as_of=snapshot['captured_at'],scope='LOCAL_SUBSET_OF_TMDB_BOARD' if scope=='local' else 'TMDB_BOARD_LOCAL_AND_EXTERNAL',snapshot_id=hashlib.sha256(json.dumps(snapshot,sort_keys=True).encode()).hexdigest()[:20],status='STALE' if age>21600 else 'OK')
        if age>21600:result['limitations'].append('上游榜单快照已超过6小时，当前显示缓存及其采集时间。')
        genres={g['id']:g['name'] for g in snapshot.get('genres',[])}
        for raw in snapshot['items']:
            local=bytmdb.get(str(raw.get('id')));external=external_movie(raw,genres)
            m=dict(local) if local else external
            if m and external:
                for field in ('tmdb_rating','vote_count','tmdb_popularity'):m[field]=external.get(field)
                m.update(feature_source='TMDB_OFFICIAL_LIST',feature_as_of=snapshot['captured_at'],popularity_observed_at=snapshot['captured_at'])
            if m is None or (scope=='local' and m.get('movie_id') is None) or not allowed(m):continue
            ranked.append(m);source_ranks[str(m.get('tmdb_id'))]=raw.get('source_rank')
            reasons[str(m.get('tmdb_id'))]=[CHANNELS[channel]+' · TMDB 官方来源排名']
        result['limitations'].append('仅展示已采集的官方榜单页；筛选后保留原始名次，库外影片未自动入库。')
    else:
        eligible=[m for m in active if allowed(m)]
        result.update(source='LOCAL_DATABASE_AND_VERIFIED_TMDB_FEATURES',source_as_of=max((m.get('feature_as_of') or '' for m in eligible),default='') or None)
        if channel in ('personal','daily','weekly','watchlist'):
            if channel=='watchlist':eligible=[m for m in eligible if state.get(str(m['movie_id']),{}).get('want') and not state.get(str(m['movie_id']),{}).get('watched')]
            matching_count=len(eligible)
            from assistant.conditions import FILTER_KEYS
            policy={k:v for k,v in filters.items() if k in FILTER_KEYS or k in ('_genre_groups','_year_groups')}
            ranking=rank_movies(eligible,state,profile_movies=active,limit=50,weights=weights,filters=policy,allow_watched=filters.get('unwatched') is False)
            ranking_evidence={r['movie_id']:r for r in ranking['items']}
            ranked=[byid[r['movie_id']] for r in ranking['items']]
            reasons={str(r['movie_id']):list(r['reasons']) for r in ranking['items']};scores={str(r['movie_id']):r['score'] for r in ranking['items']}
            if channel in ('daily','weekly'):
                period=period_for(channel,now);result['period']=period
                for m in ranked:
                    n=m.get('vote_count') or 0
                    if m.get('tmdb_rating') is not None and n>0:
                        bonus=.2*(n*m['tmdb_rating']+100*6.5)/(n+100)/10
                        scores[str(m['movie_id'])]+=bonus
                        detail=ranking_evidence[m['movie_id']]
                        detail['components']['board_rating_prior']=bonus
                        detail['reason_codes']=sorted(set(detail['reason_codes']+['PERIOD_RATING_PRIOR']))
                        detail['evidence'].append({'source':'TMDB_DATABASE_SNAPSHOT','field':'tmdb_rating','value':m['tmdb_rating'],'vote_count':n,'contribution':bonus})
                        reasons[str(m['movie_id'])].append('结合有投票依据的TMDB口碑')
                def variety(m):
                    h=hashlib.sha256((VERSION+'|'+str(user)+'|'+channel+'|'+period+'|'+str(m['movie_id'])).encode()).digest()
                    return int.from_bytes(h[:8],'big')/2**64
                remaining=list(ranked);ranked=[];counts={};decades={}
                while remaining:
                    def score(m):
                        penalty=.08*sum(counts.get(g,0) for g in m.get('genres',[]))
                        if channel=='weekly':penalty+=.15*decades.get((m.get('release_year') or 0)//10,0)
                        return scores[str(m['movie_id'])]+(.22 if channel=='weekly' else .06)*variety(m)-penalty
                    chosen=max(remaining,key=lambda m:(score(m),-m['movie_id']));ranked.append(chosen);remaining.remove(chosen)
                    for genre in chosen.get('genres',[]):counts[genre]=counts.get(genre,0)+1
                    decade=(chosen.get('release_year') or 0)//10;decades[decade]=decades.get(decade,0)+1
                options=_period_options(filters,keyword,min_votes,weights)
                order,cache_status=persisted_order(cache_dir,user,channel,period,options,[m['movie_id'] for m in ranked])
                # Cached IDs are always checked against current visibility, hard filters and negative feedback.
                valid={m['movie_id']:m for m in eligible};ranked=[valid[mid] for mid in order if mid in valid]
                result['cache_status']=cache_status
                for m in ranked:reasons.setdefault(str(m['movie_id']),[]).append(('每周类型与年代探索' if channel=='weekly' else '每日个人片单')+' · '+period)
            if channel=='watchlist':
                for m in ranked:reasons[str(m['movie_id'])]=['来自你的想看清单']+reasons.get(str(m['movie_id']),[])
            if not any(s.get('favorite') or s.get('want') or s.get('rating',0)>=4 for s in state.values()):result['limitations'].append('偏好信息不足，当前使用可用热度与多样性冷启动。')
        elif channel=='top_rated':
            threshold=100 if min_votes is None else max(1,min_votes)
            eligible=[m for m in eligible if m.get('tmdb_rating') is not None and (m.get('vote_count') or 0)>=threshold]
            mean=rating_mean if rating_mean is not None else sum(m['tmdb_rating'] for m in eligible)/len(eligible) if eligible else 0
            for m in eligible:
                n=m['vote_count'];scores[str(m['movie_id'])]=(n*m['tmdb_rating']+100*mean)/(n+100);reasons[str(m['movie_id'])]=['TMDB评分及票数 · 贝叶斯平滑（先验权重100票）']
            ranked=sorted(eligible,key=lambda m:(-scores[str(m['movie_id'])],-m['vote_count'],m['movie_id']))
            result['min_votes']=threshold
        elif channel=='revenue':
            ranked=sorted((m for m in eligible if m.get('revenue') is not None and m['revenue']>0),key=lambda m:(-m['revenue'],m['movie_id']))
            result['source_as_of']=max((m.get('revenue_as_of') or m.get('feature_as_of') or '' for m in ranked),default='') or None
            for m in ranked:reasons[str(m['movie_id'])]=['TMDB记录的有效累计收入，非期间票房']
            result['limitations'].append('只覆盖库内有有效收入资料的影片，不代表完整全球票房榜；不是日/周票房。')
        elif channel=='because':
            seeds=[m for m in active if state.get(str(m['movie_id']),{}).get('favorite') or state.get(str(m['movie_id']),{}).get('rating',0)>=4]
            seedids={m['movie_id'] for m in seeds}
            for m in eligible:
                if m['movie_id'] in seedids:continue
                best=None
                for seed in seeds:
                    directors=set(m.get('director_ids',[]))&set(seed.get('director_ids',[]));actors=set(m.get('actor_ids',[]))&set(seed.get('actor_ids',[]));genres=set(m.get('genres',[]))&set(seed.get('genres',[]))
                    value=3*bool(directors)+2*bool(actors)+len(genres)/max(1,len(set(m.get('genres',[]))|set(seed.get('genres',[]))))
                    if value>0 and (best is None or value>best[0]):best=(value,seed,directors,actors,genres)
                if best:
                    value,seed,directors,actors,genres=best;scores[str(m['movie_id'])]=value
                    reasons[str(m['movie_id'])]=['因为你喜欢《'+seed['title']+'》：'+('同导演' if directors else '同演员' if actors else '类型相近')]
                    ranked.append(m)
            ranked.sort(key=lambda m:(-scores[str(m['movie_id'])],-(m.get('catalog_popularity') or 0),m['movie_id']))
            if not seeds:result['limitations'].append('尚无收藏或4星及以上评分，不能生成“因为你喜欢”。')
    result['candidate_count']=len(ranked) if matching_count is None else matching_count
    result['ranked_window_count']=len(ranked)
    for index,m in enumerate(ranked[:limit],1):
        local=m.get('movie_id') is not None;key=str(m['movie_id']) if local and channel not in PUBLIC else str(m.get('tmdb_id'))
        item={k:m.get(k) for k in ('movie_id','tmdb_id','title','original_title','release_year','runtime_minutes','original_language','genres','poster','poster_fallback','tmdb_rating','vote_count','revenue','revenue_as_of','revenue_source','tmdb_popularity','feature_source','feature_as_of','popularity_observed_at')}
        item.update(key=('local:'+str(m['movie_id'])) if local else 'tmdb:'+str(m['tmdb_id']),in_catalog=local,rank=index,source_rank=source_ranks.get(str(m.get('tmdb_id'))),reasons=reasons.get(key,[]),score=scores.get(str(m.get('movie_id'))),tmdb_url='https://www.themoviedb.org/movie/'+str(m['tmdb_id']) if m.get('tmdb_id') else None,verification='LOCAL_DATABASE' if local else 'TMDB_OFFICIAL_LIST')
        evidence=ranking_evidence.get(m.get('movie_id'))
        if evidence:
            item.update({k:evidence[k] for k in ('reason_codes','evidence','components','recommendation_version')},score_basis='RAW_BEFORE_DIVERSITY')
        else:
            item.update(reason_codes=['CHANNEL_'+channel.upper()],evidence=[{'source':result['source'],'field':'channel','value':channel,'as_of':result.get('source_as_of')}],recommendation_version=VERSION)
        result['items'].append(item)
    if not result['items'] and result['status']=='OK':result['status']='NO_DATA'
    return result

_TABLES = CatalogBase.metadata.tables
_MOVIES = _TABLES['movies']
_GENRES = _TABLES['genres']
_GENRE_LINKS = _TABLES['movie_genres']
_CREDITS = _TABLES['movie_credits']
_EXTERNAL = _TABLES['movie_external_ids']
_RATINGS = _TABLES['movie_ratings_summary']
_PERSONAL = frozenset(('daily', 'weekly', 'personal', 'because'))
_PERIOD = frozenset(('daily', 'weekly'))
MAX_RECALL = 500
MAX_SEEDS = 60


def _active_ids():
    return select(_MOVIES.c.movie_id).where(_MOVIES.c.catalog_status == 'active',
                                            _MOVIES.c.is_adult.is_not(True))


def _state_ids(state, predicate):
    return {int(mid) for mid, behavior in state.items() if predicate(behavior)}


def _genre_match(movie_id, expression):
    return (select(literal(1)).select_from(_GENRE_LINKS.join(_GENRES,
            _GENRES.c.genre_id == _GENRE_LINKS.c.genre_id))
            .where(_GENRE_LINKS.c.movie_id == movie_id, expression).correlate_except(_GENRE_LINKS, _GENRES).exists())


def _genre_contains(movie_id, labels):
    return _genre_match(movie_id, or_(*(func.instr(_GENRES.c.name_zh, label) > 0 for label in labels)))


def _eligible_ids(state, channel, filters, keyword):
    statement = _active_ids()
    year = _MOVIES.c.release_year
    if filters.get('year_from') is not None:
        statement = statement.where(year >= filters['year_from'])
    if filters.get('year_to') is not None:
        statement = statement.where(year <= filters['year_to'])
    if filters.get('runtime_max') is not None:
        statement = statement.where(_MOVIES.c.runtime_minutes <= filters['runtime_max'])
    if filters.get('years_any') is not None:
        statement = statement.where(year.in_(filters['years_any']))
    year_groups = filters.get('_year_groups', [filters['year_ranges_any']] if filters.get('year_ranges_any') else [])
    for group in year_groups:
        statement = statement.where(or_(literal(False), *(year.between(low, high) for low, high in group)))
    if filters.get('excluded_year_ranges'):
        statement = statement.where(~or_(*(year.between(low, high) for low, high in filters['excluded_year_ranges'])))
    if filters.get('language'):
        statement = statement.where(_MOVIES.c.original_language == filters['language'])
    for group in genre_groups(filters):
        statement = statement.where(_genre_contains(_MOVIES.c.movie_id, group) if group else literal(False))
    if filters.get('excluded_genres'):
        statement = statement.where(~_genre_contains(_MOVIES.c.movie_id, filters['excluded_genres']))
    if keyword:
        names = _MOVIES.c.title + literal(' ') + func.coalesce(_MOVIES.c.original_title, '')
        statement = statement.where(func.instr(func.lower(names), keyword.casefold()) > 0)
    exclude_watched = filters.get('unwatched') or (channel in _PERSONAL and filters.get('unwatched') is not False)
    excluded = _state_ids(state, lambda item: item.get('not_interested') or (exclude_watched and item.get('watched')))
    if excluded:
        statement = statement.where(_MOVIES.c.movie_id.not_in(sorted(excluded)))
    return statement


def _credit_match(movie_id, person_ids, *, directors):
    if not person_ids:
        return literal(False)
    roles = (_CREDITS.c.role_type == 'crew', _CREDITS.c.job == 'Director') if directors else (
        _CREDITS.c.role_type == 'cast', _CREDITS.c.sort_order < 10)
    return (select(literal(1)).where(_CREDITS.c.movie_id == movie_id,
            _CREDITS.c.person_id.in_(sorted(person_ids)), *roles).correlate_except(_CREDITS).exists())


def _relations(movie_id, seeds):
    directors = {person for seed in seeds for person in seed['director_ids']}
    actors = {person for seed in seeds for person in seed['actor_ids']}
    names = {genre for seed in seeds for genre in seed['genres']}
    return (_credit_match(movie_id, directors, directors=True),
            _credit_match(movie_id, actors, directors=False),
            _genre_match(movie_id, _GENRES.c.name_zh.in_(sorted(names))) if names else literal(False))


def _seed_weight(behavior, weights):
    if behavior.get('not_interested'):
        return 0
    rating = behavior.get('rating')
    rating_weight = weights['rating'] * max(0, rating - 3) if valid_number(rating, 5) else 0
    return weights['favorite'] * bool(behavior.get('favorite')) + weights['want'] * bool(behavior.get('want')) + rating_weight


def _profile_ids(state, channel, weights, small):
    if channel == 'because':
        return _state_ids(state, lambda item: item.get('favorite') or item.get('rating', 0) >= 4)
    positive, negative = [], []
    for mid, behavior in state.items():
        weight = _seed_weight(behavior, weights)
        rating = behavior.get('rating')
        if weight:
            positive.append((weight, int(mid)))
        if behavior.get('not_interested') or (valid_number(rating, 5) and rating < 3):
            negative.append((2 if behavior.get('not_interested') else min(2, 3 - rating), int(mid)))
    if small or len(positive) + len(negative) <= MAX_SEEDS:
        return {mid for _, mid in positive + negative}
    positive_count = min(len(positive), MAX_SEEDS - min(len(negative), MAX_SEEDS // 3))
    return {mid for _, mid in nlargest(positive_count, positive) + nlargest(MAX_SEEDS - positive_count, negative)}


_DEFAULT_LOAD_CANDIDATES = _load_candidates
_RATINGS_SCHEMA_CACHE = WeakKeyDictionary()


def _ratings_schema(connection):
    engine = getattr(connection, 'engine', None)
    dialect = getattr(getattr(connection, 'dialect', None), 'name', None)
    if engine is not None:
        with _cache_lock:
            cached = _RATINGS_SCHEMA_CACHE.get(engine)
        if cached is not None:
            return cached
    inspector = inspect(connection)
    has_table = inspector.has_table(_RATINGS.name)
    cols = (frozenset(column['name'] for column in inspector.get_columns(_RATINGS.name))
            if has_table else frozenset())
    result = (has_table, cols)
    if engine is not None and (dialect == 'mysql' or (has_table and 'score_scale' in cols)):
        with _cache_lock:
            _RATINGS_SCHEMA_CACHE[engine] = result
    return result


def _thaw_movie(movie):
    row = dict(movie)
    row['genres'] = list(movie.get('genres') or ())
    row['director_ids'] = list(movie.get('director_ids') or ())
    row['actor_ids'] = list(movie.get('actor_ids') or ())
    return row


def _warmed_public_movies(engine, cache):
    if _load_candidates is not _DEFAULT_LOAD_CANDIDATES:
        return None
    peek = public_catalog_cache.peek(engine)
    if peek is None:
        return None
    if cache.invalidated_public_version is not None and peek.get('version') == cache.invalidated_public_version:
        return None
    return load_candidates_readonly(engine)


def _scalar_catalog(engine, cache, catalog_token):
    with _cache_lock:
        previous = cache.scalar
        if previous and previous[0] == catalog_token and previous[1] > time.monotonic():
            return previous[2]
        pending = cache.scalar_inflight.get(catalog_token)
        if pending is None:
            pending = cache.scalar_inflight[catalog_token] = Future()
            owner = True
        else:
            owner = False
    if not owner:
        return pending.result()
    try:
        with engine.connect() as connection:
            facts = sql_scalar_projection(connection, _active_ids().cte('rec_active'), revenue=True)
            rows = connection.execute(select(facts)).all()
        db_rows = {
            int(row.movie_id): (
                row.external_id,
                None if row.feature_stamp is None else str(row.feature_stamp),
                bool(row.base_applied),
                bool(row.metrics_applied),
                None if row.heat is None else float(row.heat),
                None if row.votes is None else int(row.votes),
                None if row.score is None else float(row.score),
                None if row.revenue is None else int(row.revenue),
            )
            for row in rows
        }
        with _cache_lock:
            if cache.catalog_token is catalog_token[0]:
                cache.scalar = (catalog_token, time.monotonic() + BOARD_TTL, db_rows)
        pending.set_result(db_rows)
        return db_rows
    except BaseException as error:
        pending.set_exception(error)
        raise
    finally:
        with _cache_lock:
            cache.scalar_inflight.pop(catalog_token, None)


def _effective_scalars(db_rows):
    archive = archived_scalar_map()
    if not archive:
        return {mid: (row[4], row[5], row[6], row[7]) for mid, row in db_rows.items()}
    effective = {}
    for mid, (ext_id, f_stamp, base_app, metrics_app, heat, votes, score, rev) in db_rows.items():
        if ext_id is not None:
            entry = archive.get(f'{mid}:{ext_id}')
            if entry:
                a_stamp = entry.get('stamp') or ''
                newer = (f_stamp is None) or (f_stamp.replace(' ', 'T')[:19] < a_stamp[:19])
                if newer:
                    if not metrics_app:
                        pop = entry.get('popularity')
                        heat = float(pop) if valid_number(pop) else None
                        vc = entry.get('vote_count')
                        votes = vc if type(vc) is int and vc >= 0 else None
                        va = entry.get('vote_average')
                        score = float(va) if votes is not None and votes > 0 and valid_number(va, 10) else None
                    if not base_app:
                        a_rev = entry.get('revenue')
                        if valid_number(a_rev) and a_rev > 0:
                            rev = int(a_rev)
        effective[mid] = (heat, votes, score, rev)
    return effective


def _catalog_counts(engine, cache, catalog_token, *, ro_movies=None):
    if ro_movies is not None:
        active = [m for m in ro_movies if m.get('catalog_status') == 'active' and m.get('is_adult') is not True]
        return len(active), sum(1 for m in active if m.get('tmdb_popularity') is not None)
    with _cache_lock:
        previous = cache.coverage
        if previous and previous[0] == catalog_token and previous[1] > time.monotonic():
            return previous[2]
        pending = cache.coverage_inflight.get(catalog_token)
        if pending is None:
            pending = cache.coverage_inflight[catalog_token] = Future()
            owner = True
        else:
            owner = False
    if not owner:
        return pending.result()
    try:
        eff = _effective_scalars(_scalar_catalog(engine, cache, catalog_token))
        totals = (len(eff), sum(1 for val in eff.values() if val[0] is not None))
        with _cache_lock:
            if cache.catalog_token is catalog_token[0]:
                cache.coverage = (catalog_token, time.monotonic() + BOARD_TTL, totals)
        pending.set_result(totals)
        return totals
    except BaseException as error:
        pending.set_exception(error)
        raise
    finally:
        with _cache_lock:
            cache.coverage_inflight.pop(catalog_token, None)


def _rank_by_scalars(rows, scalars, *, prefix_cols=0):
    ordered = sorted(rows, key=lambda r: r[0])
    ordered.sort(key=lambda r: (r[-1] is not None, r[-1]), reverse=True)

    def scalar_tuple(mid):
        val = scalars.get(mid)
        if val is None:
            return (False, 0.0, False, 0, False, 0.0)
        heat, votes, score, _rev = val
        return (
            score is not None, score if score is not None else 0.0,
            votes is not None, votes if votes is not None else 0,
            heat is not None, heat if heat is not None else 0.0,
        )

    ordered.sort(key=lambda r: (*r[1:1 + prefix_cols], *scalar_tuple(r[0])), reverse=True)
    return [r[0] for r in ordered]


def _recall(engine, channel, state, *, filters, keyword, min_votes, weights, persisted, catalog_total, snapshot,
            cache=None, catalog_token=None):
    if channel in PUBLIC:
        upstream = [str(row['id']) for row in (snapshot or {}).get('items', [])[:60]
                    if type(row.get('id')) is int and row['id'] > 0]
        if not upstream:
            return [], None, None
        query = _active_ids().join(_EXTERNAL, _EXTERNAL.c.movie_id == _MOVIES.c.movie_id).where(
                                 _EXTERNAL.c.source == 'tmdb', _EXTERNAL.c.external_id.in_(upstream)).limit(60)
        with engine.connect() as connection:
            ids = connection.execute(query).scalars().all()
        return finalize(_load_candidates(engine, movie_ids=ids)), None, None

    if channel == 'watchlist':
        wanted = sorted(_state_ids(state, lambda item: item.get('want') and not item.get('watched')))
        movies = [movie for offset in range(0, len(wanted), MAX_RECALL)
                  for movie in _load_candidates(engine, movie_ids=wanted[offset:offset + MAX_RECALL])]
        return finalize(movies), None, None

    small = catalog_total < MAX_RECALL
    if small and channel in ('daily', 'weekly', 'personal'):
        with engine.connect() as connection:
            ids = connection.execute(_active_ids().order_by(_MOVIES.c.movie_id).limit(MAX_RECALL)).scalars().all()
        return finalize(_load_candidates(engine, movie_ids=ids)), None, None
    seed_ids = _profile_ids(state, channel, weights, small) if channel in _PERSONAL else set()
    seed_order = sorted(seed_ids)
    seeds = [seed for offset in range(0, len(seed_order), MAX_RECALL)
             for seed in _load_candidates(engine, movie_ids=seed_order[offset:offset + MAX_RECALL])]
    seeds = [seed for seed in seeds if seed.get('is_adult') is not True]
    if channel == 'because' and not seeds:
        return [], 0, None

    matching_count, rating_mean = None, None
    if channel in ('top_rated', 'revenue') and cache is not None and catalog_token is not None:
        scalars = _effective_scalars(_scalar_catalog(engine, cache, catalog_token))
        if not filters and not keyword:
            exclude_watched = bool(filters.get('unwatched'))
            excluded = _state_ids(state, lambda item: item.get('not_interested') or (exclude_watched and item.get('watched')))
            allowed_ids = set(scalars) - excluded
        else:
            with engine.connect() as connection:
                allowed_ids = set(connection.execute(_eligible_ids(state, channel, filters, keyword)).scalars())
        if channel == 'top_rated':
            threshold = 100 if min_votes is None else max(1, min_votes)
            matched = [(mid, val[1], val[2]) for mid in allowed_ids
                       if (val := scalars.get(mid)) is not None and val[2] is not None and val[1] is not None and val[1] >= threshold]
            matching_count = len(matched)
            rating_mean = sum(s for _, _, s in matched) / matching_count if matching_count else 0
            matched.sort(key=lambda item: (-((item[1] * item[2] + 100 * rating_mean) / (item[1] + 100)), -item[1], item[0]))
            recalled = [mid for mid, _, _ in matched[:400]]
        else:
            matched = [(mid, val[3]) for mid in allowed_ids
                       if (val := scalars.get(mid)) is not None and val[3] is not None and val[3] >= 1
                       and (min_votes is None or (val[1] is not None and val[1] >= min_votes))]
            matching_count = len(matched)
            matched.sort(key=lambda item: (-item[1], item[0]))
            recalled = [mid for mid, _ in matched[:400]]
        loaded = {seed['movie_id']: seed for seed in seeds}
        loaded.update((movie['movie_id'], movie) for movie in _load_candidates(engine, movie_ids=set(recalled) - loaded.keys()))
        return finalize([loaded[mid] for mid in sorted(loaded)]), matching_count, rating_mean

    eligible = _eligible_ids(state, channel, filters, keyword).cte('rec_eligible')
    with engine.connect() as connection:
        start = eligible
        if min_votes is not None and cache is not None and catalog_token is not None:
            scalars = _effective_scalars(_scalar_catalog(engine, cache, catalog_token))
            eligible_ids = [mid for mid in connection.execute(select(eligible.c.movie_id)).scalars()
                            if (val := scalars.get(mid)) is not None and val[1] is not None and val[1] >= min_votes]
            if not eligible_ids:
                return [], 0, None
            start = select(_MOVIES.c.movie_id).where(_MOVIES.c.movie_id.in_(eligible_ids)).cte('rec_filtered_facts')
        elif min_votes is not None or channel in ('top_rated', 'revenue'):
            facts = sql_feature_projection(connection, eligible, revenue=channel == 'revenue')
            narrowed = select(facts)
            if min_votes is not None:
                narrowed = narrowed.where(facts.c.votes >= min_votes)
            if channel == 'top_rated':
                threshold = 100 if min_votes is None else max(1, min_votes)
                narrowed = narrowed.where(facts.c.score.is_not(None), facts.c.votes >= threshold)
            elif channel == 'revenue':
                narrowed = narrowed.where(facts.c.revenue >= 1)
            start = narrowed.cte('rec_filtered_facts')
        ids = select(start.c.movie_id)
        if channel == 'top_rated':
            window = select(start, func.count().over().label('matching_count'),
                            func.avg(start.c.score).over().label('rating_mean')).cte('rec_rating_window')
            score = (window.c.votes * window.c.score + 100 * window.c.rating_mean) / (window.c.votes + 100)
            rows = connection.execute(select(window.c.movie_id, window.c.matching_count, window.c.rating_mean)
                                      .order_by(score.desc(), window.c.votes.desc(), window.c.movie_id).limit(400)).all()
            matching_count, rating_mean = (rows[0].matching_count, float(rows[0].rating_mean)) if rows else (0, 0)
            recalled, ids = [row.movie_id for row in rows], None
        elif channel == 'revenue':
            rows = connection.execute(ids.add_columns(func.count().over().label('matching_count'))
                                      .order_by(start.c.revenue.desc(), start.c.movie_id).limit(400)).all()
            matching_count = rows[0].matching_count if rows else 0
            recalled, ids = [row.movie_id for row in rows], None
        elif channel == 'because':
            directors, actors, genres = _relations(start.c.movie_id, seeds)
            ids = ids.where(start.c.movie_id.not_in(sorted(seed_ids)), or_(directors, actors, genres))
            if not _ratings_schema(connection)[0] and cache is not None and catalog_token is not None:
                scalars = _effective_scalars(_scalar_catalog(engine, cache, catalog_token))
                rows = connection.execute(
                    select(start.c.movie_id,
                           case((directors, 3), else_=0).label('d_hit'),
                           case((actors, 2), else_=0).label('a_hit'),
                           _MOVIES.c.updated_at)
                    .select_from(start.join(_MOVIES, _MOVIES.c.movie_id == start.c.movie_id))
                    .where(start.c.movie_id.not_in(sorted(seed_ids)), or_(directors, actors, genres))
                ).all()
                matching_count = len(rows)
                recalled, ids = _rank_by_scalars(rows, scalars, prefix_cols=2)[:400], None
            else:
                matching_count = connection.scalar(select(func.count()).select_from(ids.subquery()))
                ids, quality = _quality(connection, ids, start, eligible)
                ids = ids.order_by(case((directors, 3), else_=0).desc(), case((actors, 2), else_=0).desc(), *quality).limit(400)
        else:
            matching_count = connection.scalar(select(func.count()).select_from(start))
            seed_loaded = {seed['movie_id'] for seed in seeds}
            capacity = min(450, MAX_RECALL - len(seed_loaded))
            if not small and matching_count > capacity:
                pinned = []
                for offset in range(0, len(persisted), 300):
                    chunk = persisted[offset:offset + 300]
                    valid = set(connection.execute(ids.where(start.c.movie_id.in_(chunk))).scalars())
                    pinned.extend(mid for mid in chunk if mid in valid)
                    if len(pinned) >= 50:
                        break
                directors, actors, _ = _relations(start.c.movie_id, seeds)
                preferred = Counter()
                for seed in seeds:
                    for genre in seed['genres']:
                        preferred[genre] += _seed_weight(state[str(seed['movie_id'])], weights)
                genre_ids = [genre for genre, weight in preferred.most_common(5) if weight > 0]
                if not _ratings_schema(connection)[0] and cache is not None and catalog_token is not None:
                    scalars = _effective_scalars(_scalar_catalog(engine, cache, catalog_token))
                    base_rows = connection.execute(
                        select(start.c.movie_id, _MOVIES.c.updated_at)
                        .select_from(start.join(_MOVIES, _MOVIES.c.movie_id == start.c.movie_id))
                    ).all()
                    lanes = [
                        _rank_by_scalars(base_rows, scalars)[:140],
                        connection.execute(ids.join(_MOVIES, _MOVIES.c.movie_id == start.c.movie_id)
                                           .order_by(_MOVIES.c.updated_at.desc(), start.c.movie_id).limit(40)).scalars().all(),
                    ]
                    if seeds:
                        credit_rows = connection.execute(
                            select(start.c.movie_id, case((directors, 1), else_=0), _MOVIES.c.updated_at)
                            .select_from(start.join(_MOVIES, _MOVIES.c.movie_id == start.c.movie_id))
                            .where(or_(directors, actors))
                        ).all()
                        lanes.append(_rank_by_scalars(credit_rows, scalars, prefix_cols=1)[:160])
                    if genre_ids:
                        genre_rows = connection.execute(
                            select(start.c.movie_id, _MOVIES.c.updated_at)
                            .select_from(start.join(_MOVIES, _MOVIES.c.movie_id == start.c.movie_id))
                            .where(_genre_match(start.c.movie_id, _GENRES.c.name_zh.in_(genre_ids)))
                        ).all()
                        lanes.append(_rank_by_scalars(genre_rows, scalars)[:180])
                else:
                    quality_ids, quality = _quality(connection, ids, start, eligible)
                    lanes = [connection.execute(quality_ids.order_by(*quality).limit(140)).scalars().all(),
                             connection.execute(ids.join(_MOVIES, _MOVIES.c.movie_id == start.c.movie_id)
                                                .order_by(_MOVIES.c.updated_at.desc(), start.c.movie_id).limit(40)).scalars().all()]
                    if seeds:
                        lanes.append(connection.execute(quality_ids.where(or_(directors, actors))
                                     .order_by(case((directors, 1), else_=0).desc(), *quality).limit(160)).scalars().all())
                    if genre_ids:
                        lanes.append(connection.execute(quality_ids.where(_genre_match(start.c.movie_id, _GENRES.c.name_zh.in_(genre_ids)))
                                     .order_by(*quality).limit(180)).scalars().all())
                recalled = dict.fromkeys(pinned[:50])
                for index in range(max(map(len, lanes))):
                    for lane in lanes:
                        if index < len(lane) and len(recalled) < capacity:
                            recalled.setdefault(lane[index], None)
                ids = None
        if ids is not None:
            recalled = connection.execute(ids).scalars().all()
    loaded = {seed['movie_id']: seed for seed in seeds}
    loaded.update((movie['movie_id'], movie) for movie in _load_candidates(engine, movie_ids=set(recalled) - loaded.keys()))
    return finalize([loaded[mid] for mid in sorted(loaded)]), matching_count, rating_mean


def _quality(connection, query, start, eligible):
    has_ratings, rating_cols = _ratings_schema(connection)
    if 'score' in start.c:
        quality = (start.c.score.desc(), start.c.votes.desc(), start.c.heat.desc())
    elif has_ratings:
        tmdb_r = _RATINGS.alias('rec_tmdb_ratings')
        douban_r = _RATINGS.alias('rec_douban_ratings')
        tmdb_on = and_(tmdb_r.c.movie_id == start.c.movie_id, tmdb_r.c.source == 'tmdb')
        douban_on = and_(douban_r.c.movie_id == start.c.movie_id, douban_r.c.source == 'douban')
        if 'score_scale' in rating_cols:
            douban_on = and_(douban_on, douban_r.c.score_scale == 10)
        query = query.outerjoin(tmdb_r, tmdb_on).outerjoin(douban_r, douban_on)
        score_val = func.coalesce(tmdb_r.c.score_value, douban_r.c.score_value)
        vote_cnt = func.coalesce(tmdb_r.c.vote_count, douban_r.c.vote_count)
        bayes = (vote_cnt * score_val + 650) / (vote_cnt + 100)
        quality = (bayes.desc(), vote_cnt.desc())
    else:
        facts = sql_feature_projection(connection, eligible)
        query = query.join(facts, facts.c.movie_id == start.c.movie_id)
        quality = (facts.c.score.desc(), facts.c.votes.desc(), facts.c.heat.desc())
    query = query.join(_MOVIES, _MOVIES.c.movie_id == start.c.movie_id)
    return query, (*quality, _MOVIES.c.updated_at.desc(), start.c.movie_id)


BOARD_TTL = 120
BOARD_STALE_GRACE = 15
MAX_BOARD_CACHE = 64
_GLOBAL_BOARD_OWNER = '__public__'
_GLOBAL_CHANNELS = PUBLIC | frozenset(('top_rated', 'revenue'))
_cache_lock = threading.RLock()
_board_caches = WeakKeyDictionary()
_refresh_workers = ThreadPoolExecutor(max_workers=4, thread_name_prefix='recommendation-warm')


class _BoardCache:
    def __init__(self, engine=None):
        self.engine = engine
        self.results = OrderedDict()
        self.inflight = {}
        self.warming = {}
        self.versions = OrderedDict()
        self.catalog_token = object()
        self.coverage = None
        self.coverage_inflight = {}
        self.scalar = None
        self.scalar_inflight = {}
        self.user_states = OrderedDict()
        self.invalidated_public_version = None


def _engine_cache(engine):
    with _cache_lock:
        cache = _board_caches.get(engine)
        if cache is None:
            cache = _board_caches[engine] = _BoardCache(engine)
        return cache


def invalidate_board_cache(engine=None, user=None, *, behavior_version=None):
    with _cache_lock:
        caches = list(_board_caches.values()) if engine is None else [_engine_cache(engine)]
        for cache in caches:
            if user is None:
                cache.results.clear()
                cache.catalog_token = object()
                cache.coverage = None
                cache.scalar = None
                cache.user_states.clear()
                if cache.engine is not None:
                    cache.invalidated_public_version = (public_catalog_cache.peek(cache.engine) or {}).get('version')
                    _RATINGS_SCHEMA_CACHE.pop(cache.engine, None)
            else:
                principal = str(user)
                previous, _ = cache.versions.get(principal, (None, None))
                version = max(previous, behavior_version) if previous is not None and behavior_version is not None else behavior_version
                cache.versions[principal] = (version, object())
                cache.versions.move_to_end(principal)
                for key in [key for key in cache.results if key[0] == principal]:
                    del cache.results[key]
                for key in [key for key in cache.user_states if key[0] == principal]:
                    del cache.user_states[key]
                if len(cache.versions) > MAX_BOARD_CACHE:
                    dropped, _ = cache.versions.popitem(last=False)
                    for key in [key for key in cache.results if key[0] == dropped]:
                        del cache.results[key]
                    for key in [key for key in cache.user_states if key[0] == dropped]:
                        del cache.user_states[key]


def _canonical_filters(filters):
    return json.dumps(filters, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def _clone_dto(value):
    if type(value) is dict:
        return {key: _clone_dto(item) for key, item in value.items()}
    if type(value) is list:
        return [_clone_dto(item) for item in value]
    return value


def _tmdb_token(service, user, binding):
    if not service:
        return None
    lock = getattr(service, '_lock', None)
    with lock if lock is not None else nullcontext():
        entry = getattr(service, '_entries', {}).get((user, binding))
        if entry and (getattr(service, 'store', None) or entry['expires'] > time.monotonic()):
            state = tuple(entry.get(field) for field in ('generation', 'enabled', 'verification', 'last_error', 'last_checked_at'))
        else:
            state = None
    return id(service), binding, state


def _request_binding(app):
    from flask import has_request_context
    from .tmdb_connection import request_binding
    return request_binding(app) if has_request_context() else hashlib.sha256(b'').hexdigest()


def _schedule_home_work(app, user, callback, *, replace=False):
    cache = _engine_cache(app.extensions['auth_engine'])
    principal = str(user)
    with _cache_lock:
        if principal in cache.warming:
            if replace:
                cache.warming[principal] = callback
            return False
        if len(cache.warming) >= 64:
            return False
        cache.warming[principal] = None

    def run():
        task = callback
        while task is not None:
            try:
                with app.app_context():
                    task()
            except Exception as error:
                app.logger.warning('Recommendation cache background refresh failed: %s', type(error).__name__)
            with _cache_lock:
                task = cache.warming[principal]
                if task is None:
                    del cache.warming[principal]
                else:
                    cache.warming[principal] = None

    try:
        _refresh_workers.submit(run)
    except RuntimeError:
        with _cache_lock:
            del cache.warming[principal]
        return False
    return True


def warm_user_home_async(app, user):
    binding = _request_binding(app)
    return _schedule_home_work(app, user, lambda: _for_app(app, user, 'daily', binding=binding, limit=13), replace=True)


def for_app(app, user, channel='personal', **kwargs):
    return _for_app(app, user, channel, binding=_request_binding(app), **kwargs)


def _has_personal_exclusions(state, filters):
    if not state:
        return False
    check_watched = bool(filters.get('unwatched'))
    return any(
        item.get('not_interested') or (check_watched and item.get('watched'))
        for item in state.values()
    )


def _user_state(cache, repository, user, principal, behavior_token):
    state_key = (principal, id(repository))
    now_mono = time.monotonic()
    with _cache_lock:
        cached = cache.user_states.get(state_key)
        if cached and cached[0] == behavior_token and cached[1] > now_mono:
            cache.user_states.move_to_end(state_key)
            return cached[2]
    state = repository.snapshot(user)
    if BOARD_TTL > 0:
        with _cache_lock:
            if cache.versions.get(principal) == behavior_token:
                cache.user_states[state_key] = (behavior_token, time.monotonic() + BOARD_TTL, state)
                cache.user_states.move_to_end(state_key)
                while len(cache.user_states) > MAX_BOARD_CACHE:
                    cache.user_states.popitem(last=False)
    return state


def _with_tmdb_status(dto, service, user, binding):
    out = _clone_dto(dto)
    if service:
        status = service.status(user, binding)
        out['tmdb_connection'] = status
        if status.get('last_error'):
            message = 'TMDB在线读取失败，当前使用可用缓存；可在数据连接中检查原因。'
            if message not in out['limitations']:
                out['limitations'].append(message)
    return out


def _warmed_board(ro_movies, state, user, channel, *, limit, filters, keyword, min_votes,
                  scope, folder, now, weights, snapshot):
    active_ro = [m for m in ro_movies if m.get('catalog_status') == 'active' and m.get('is_adult') is not True]
    totals = (len(active_ro), sum(1 for m in active_ro if m.get('tmdb_popularity') is not None))
    personal = channel in ('daily', 'weekly', 'because', 'personal')
    kw = keyword.casefold() if keyword else ''

    def allowed(m):
        s = state.get(str(m.get('movie_id')), {})
        return (matches(m, filters, s)
                and (not personal or not s.get('watched') or filters.get('unwatched') is False)
                and (not kw or kw in (m['title'] + ' ' + (m.get('original_title') or '')).casefold())
                and (min_votes is None or (m.get('vote_count') is not None and m['vote_count'] >= min_votes)))

    candidate_count = None
    rating_mean = None
    if channel in PUBLIC:
        bytmdb = {str(m['tmdb_id']): m for m in active_ro if m.get('tmdb_id')}
        movies = []
        seen_ids = set()
        for raw in (snapshot or {}).get('items', []):
            m = bytmdb.get(str(raw.get('id')))
            if m is not None and m['movie_id'] not in seen_ids:
                seen_ids.add(m['movie_id'])
                movies.append(_thaw_movie(m))
    elif channel == 'top_rated':
        threshold = 100 if min_votes is None else max(1, min_votes)
        eligible = [m for m in active_ro if allowed(m) and m.get('tmdb_rating') is not None and (m.get('vote_count') or 0) >= threshold]
        candidate_count = len(eligible)
        rating_mean = sum(m['tmdb_rating'] for m in eligible) / candidate_count if candidate_count else 0
        ordered = sorted(
            eligible,
            key=lambda m: (-((m['vote_count'] * m['tmdb_rating'] + 100 * rating_mean) / (m['vote_count'] + 100)),
                           -m['vote_count'], m['movie_id']),
        )
        movies = [_thaw_movie(m) for m in ordered[:400]]
    elif channel == 'revenue':
        eligible = [m for m in active_ro if allowed(m) and m.get('revenue') is not None and m['revenue'] > 0]
        candidate_count = len(eligible)
        ordered = sorted(eligible, key=lambda m: (-m['revenue'], m['movie_id']))
        movies = [_thaw_movie(m) for m in ordered[:400]]
    else:
        movies = [_thaw_movie(m) for m in active_ro]

    result = _board(movies, state, user, channel, limit=limit, filters=filters, keyword=keyword,
                    min_votes=min_votes, scope=scope, cache_dir=folder, now=now, weights=weights,
                    snapshot=snapshot, rating_mean=rating_mean)
    result['catalog_total'], result['popularity_covered'] = totals
    if candidate_count is not None:
        result['candidate_count'] = candidate_count
    elif channel in PUBLIC and 'candidate_count' not in result:
        result['candidate_count'] = result['ranked_window_count'] = 0
    return result


def _for_app(app, user, channel, *, binding, limit=12, filters=None, keyword='', min_votes=None,
             scope='all', now=None, weights=None, trusted_filters=False, refresh=False):
    from .tmdb_connection import using

    explicit_now = now
    filters, now, weights = _board_arguments(channel, limit, filters, keyword, min_votes,
                                            scope, now, weights, trusted_filters)
    weights = {key: float(value) for key, value in weights.items()}
    folder = os.environ.get('MI2_RECOMMENDATION_CACHE_DIR') or str(Path(app.config['MEDIA_CACHE_DIR']) / '.private-recommendations')
    engine = app.extensions['auth_engine']
    repository = app.extensions['m6_behavior']
    service = app.extensions.get('tmdb_connection')
    cache = _engine_cache(engine)
    period = period_for(channel, now) if channel in _PERIOD else None
    principal = str(user)
    with using(service, user, binding):
        snapshot = public_snapshot(channel, online=False) if channel in PUBLIC else None
        stamp = snapshot['captured_at'] if snapshot else None
        snapshot_token = (stamp, (now - datetime.fromisoformat(stamp)).total_seconds() > 21600) if stamp else None
        catalog_version = (public_catalog_cache.peek(engine) or {}).get('version')
        with _cache_lock:
            catalog_token = (cache.catalog_token, catalog_version)
            behavior_token = cache.versions.get(principal)
        preloaded_state = None
        if channel in _GLOBAL_CHANNELS:
            preloaded_state = _user_state(cache, repository, user, principal, behavior_token)
            if _has_personal_exclusions(preloaded_state, filters):
                cache_owner = principal
                cache_behavior_token = behavior_token
                effective_state = preloaded_state
            else:
                cache_owner = _GLOBAL_BOARD_OWNER
                cache_behavior_token = None
                effective_state = {}
        else:
            cache_owner = principal
            cache_behavior_token = behavior_token
            effective_state = None

        key = (cache_owner, channel, scope, limit, _canonical_filters(filters), keyword, min_votes,
               tuple(sorted(weights.items())), period, catalog_token, cache_behavior_token,
               snapshot_token, explicit_now, folder, id(repository))
        with _cache_lock:
            hit = cache.results.get(key)
            if hit:
                age = time.monotonic() - hit[0]
                if not refresh and age < BOARD_TTL + BOARD_STALE_GRACE:
                    cache.results.move_to_end(key)
                    if age >= BOARD_TTL:
                        _schedule_home_work(app, user, lambda: _for_app(app, user, channel, binding=binding, limit=limit,
                                            filters=filters, keyword=keyword, min_votes=min_votes, scope=scope,
                                            now=explicit_now, weights=weights, trusted_filters=True, refresh=True))
                    return _with_tmdb_status(hit[1], service, user, binding)
            future = cache.inflight.get(key)
            if future is None:
                future = cache.inflight[key] = Future()
                owner = True
            else:
                owner = False
        if not owner:
            return _with_tmdb_status(future.result(), service, user, binding)
        try:
            state = effective_state if effective_state is not None else repository.snapshot(user)
            ro_movies = _warmed_public_movies(engine, cache)
            if ro_movies is not None:
                result = _warmed_board(
                    ro_movies, state, user, channel, limit=limit, filters=filters,
                    keyword=keyword, min_votes=min_votes, scope=scope, folder=folder,
                    now=now, weights=weights, snapshot=snapshot,
                )
            else:
                totals = _catalog_counts(engine, cache, catalog_token)
                persisted = []
                if period:
                    options = _period_options(filters, keyword, min_votes, weights)
                    persisted = _read_period_ids(Path(folder) / (_period_key(user, channel, period, options) + '.json'), period)
                movies, candidate_count, mean = _recall(
                    engine, channel, state, filters=filters, keyword=keyword,
                    min_votes=min_votes, weights=weights, persisted=persisted,
                    catalog_total=totals[0], snapshot=snapshot,
                    cache=cache, catalog_token=catalog_token,
                )
                result = _board(movies, state, user, channel, limit=limit, filters=filters, keyword=keyword,
                                min_votes=min_votes, scope=scope, cache_dir=folder, now=now, weights=weights,
                                snapshot=snapshot, rating_mean=mean)
                result['catalog_total'], result['popularity_covered'] = totals
                if candidate_count is not None:
                    result['candidate_count'] = candidate_count
                elif channel in PUBLIC and 'candidate_count' not in result:
                    result['candidate_count'] = result['ranked_window_count'] = 0
            attach_backdrops(engine, result['items'])
            with _cache_lock:
                if cache.catalog_token is catalog_token[0] and (
                        cache_owner == _GLOBAL_BOARD_OWNER or cache.versions.get(principal) == behavior_token):
                    cache.results[key] = (time.monotonic(), result)
                    cache.results.move_to_end(key)
                    while len(cache.results) > MAX_BOARD_CACHE:
                        cache.results.popitem(last=False)
                future.set_result(result)
            return _with_tmdb_status(result, service, user, binding)
        except BaseException as error:
            future.set_exception(error)
            raise
        finally:
            with _cache_lock:
                cache.inflight.pop(key, None)
