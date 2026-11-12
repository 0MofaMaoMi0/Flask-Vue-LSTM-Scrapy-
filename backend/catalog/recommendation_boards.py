"""Shared read-only recommendation channels. No model calls or catalog mutations."""
import hashlib,json,math,os,threading
from pathlib import Path
from datetime import datetime,timedelta,timezone
from .recommendation import recommend
from .recommendation_data import load_candidates,valid_number
from assistant.conditions import matches,validate_filters

VERSION='boards-v1'
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

def persisted_order(folder,user,channel,period,filters,ordered):
    """Cache only IDs, never credentials or behavior payloads; fail explicitly if unwritable."""
    if folder is None:return ordered,'DETERMINISTIC_ONLY'
    key=hashlib.sha256(json.dumps([VERSION,str(user),channel,period,filters],sort_keys=True,separators=(',',':')).encode()).hexdigest()
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
            if target.is_file():
                doc=json.loads(target.read_text(encoding='utf-8'))
                if doc.get('version')==VERSION and doc.get('period')==period:
                    old=doc.get('ids',[])
                    if type(old) is not list or any(type(i) is not int or i<1 for i in old):raise ValueError('Invalid period cache')
                    ordered=list(dict.fromkeys(old+ordered))
            tmp=folder/(key+'.'+str(os.getpid())+'.tmp')
            tmp.write_text(json.dumps({'version':VERSION,'period':period,'ids':ordered}),encoding='utf-8');os.chmod(tmp,0o600);os.replace(tmp,target)
    return ordered,'PERSISTED_PERIOD'

def public_snapshot(channel):
    path=Path(os.environ.get('MI2_TMDB_BOARD_DIR') or DATA)/(channel+'.json')
    if not path.is_file():return None
    try:
        doc=json.loads(path.read_text(encoding='utf-8'))
        stamp=datetime.fromisoformat(doc['captured_at'])
        if doc.get('schema')!=1 or doc.get('channel')!=channel or not isinstance(doc.get('items'),list) or stamp.tzinfo is None:return None
    except (ValueError,KeyError,TypeError):return None
    return doc

def context_stamp():
    now=datetime.now(timezone.utc)
    return [VERSION,period_for('daily',now),period_for('weekly',now),[(c,(public_snapshot(c) or {}).get('captured_at')) for c in sorted(PUBLIC)]]

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
    if channel not in CHANNELS or type(limit) is not int or not 1<=limit<=50 or scope not in ('all','local'):raise ValueError('Channel arguments')
    if type(keyword) is not str or len(keyword)>100:raise ValueError('Keyword')
    if min_votes is not None and (type(min_votes) is not int or not 0<=min_votes<=10000000):raise ValueError('Votes')
    filters=dict(filters or {}) if trusted_filters else validate_filters(filters or {});now=now or datetime.now(timezone.utc)
    weights={'favorite':3.,'want':2.,'rating':1.} if weights is None else weights
    if set(weights)!={'favorite','want','rating'} or any(type(v) not in (int,float) or not math.isfinite(v) or not 0<=v<=10 for v in weights.values()):raise ValueError('Weights')
    if now.tzinfo is None:raise ValueError('Aware time required')
    active=[m for m in movies if m.get('catalog_status')=='active' and m.get('is_adult') is not True]
    byid={m['movie_id']:m for m in active};bytmdb={str(m['tmdb_id']):m for m in active if m.get('tmdb_id')}
    personal=channel in ('daily','weekly','because','personal')
    def allowed(m):
        s=state.get(str(m.get('movie_id')),{})
        return matches(m,filters,s) and (not personal or not s.get('watched')) and (not keyword or keyword.casefold() in (m['title']+' '+(m.get('original_title') or '')).casefold()) and (min_votes is None or (m.get('vote_count') is not None and m['vote_count']>=min_votes))
    result={'channel':channel,'title':CHANNELS[channel],'description':DESCRIPTIONS[channel],'algorithm_version':VERSION,'scope':'LOCAL_CATALOG','catalog_total':len(active),
        'as_of':now.isoformat(),'period':None,'timezone':'Asia/Shanghai','paid_model_tokens':0,'items':[],
        'popularity_covered':sum(m.get('tmdb_popularity') is not None for m in active),'status':'OK','limitations':[]}
    result['mode']='MYSQL_CATALOG_POPULAR' if channel=='popular' else 'MYSQL_AUTHENTICATED_RECOMMENDATION' if channel=='personal' else 'MYSQL_RECOMMENDATION_CHANNEL'
    ranked=[];reasons={};scores={};source_ranks={};matching_count=None
    if channel in PUBLIC:
        snapshot=public_snapshot(channel)
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
            ranking=recommend(eligible,state,profile_movies=active,limit=50,weights=weights,baseline_field='catalog_popularity',cold_start_reason='可用热度与多样性冷启动')
            ranked=[byid[r['movie_id']] for r in ranking['items']]
            reasons={str(r['movie_id']):list(r['reasons']) for r in ranking['items']};scores={str(r['movie_id']):r['score'] for r in ranking['items']}
            if channel in ('daily','weekly'):
                period=period_for(channel,now);result['period']=period
                for m in ranked:
                    n=m.get('vote_count') or 0
                    if m.get('tmdb_rating') is not None and n>0:
                        scores[str(m['movie_id'])]+=.2*(n*m['tmdb_rating']+100*6.5)/(n+100)/10
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
                options=dict(filters=filters,keyword=keyword,min_votes=min_votes,weights=weights)
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
            mean=sum(m['tmdb_rating'] for m in eligible)/len(eligible) if eligible else 0
            for m in eligible:
                n=m['vote_count'];scores[str(m['movie_id'])]=(n*m['tmdb_rating']+100*mean)/(n+100);reasons[str(m['movie_id'])]=['TMDB评分及票数 · 贝叶斯平滑（先验权重100票）']
            ranked=sorted(eligible,key=lambda m:(-scores[str(m['movie_id'])],-m['vote_count'],m['movie_id']))
            result['min_votes']=threshold
        elif channel=='revenue':
            ranked=sorted((m for m in eligible if m.get('revenue') is not None and m['revenue']>0),key=lambda m:(-m['revenue'],m['movie_id']))
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
        item={k:m.get(k) for k in ('movie_id','tmdb_id','title','original_title','release_year','runtime_minutes','original_language','genres','poster','tmdb_rating','vote_count','revenue','tmdb_popularity','feature_source','feature_as_of','popularity_observed_at')}
        item.update(key=('local:'+str(m['movie_id'])) if local else 'tmdb:'+str(m['tmdb_id']),in_catalog=local,rank=index,source_rank=source_ranks.get(str(m.get('tmdb_id'))),reasons=reasons.get(key,[]),score=scores.get(str(m.get('movie_id'))),tmdb_url='https://www.themoviedb.org/movie/'+str(m['tmdb_id']) if m.get('tmdb_id') else None,verification='LOCAL_DATABASE' if local else 'TMDB_OFFICIAL_LIST')
        result['items'].append(item)
    if not result['items'] and result['status']=='OK':result['status']='NO_DATA'
    return result

def for_app(app,user,channel='personal',**kwargs):
    folder=os.environ.get('MI2_RECOMMENDATION_CACHE_DIR') or str(Path(app.config['MEDIA_CACHE_DIR'])/'.private-recommendations')
    return board(load_candidates(app.extensions['auth_engine']),app.extensions['m6_behavior'].snapshot(user),user,channel,cache_dir=folder,**kwargs)
