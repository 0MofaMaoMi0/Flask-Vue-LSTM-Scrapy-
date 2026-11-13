"""Read-only catalog quality inventory. No credentials, DDL or automatic matches."""
from datetime import datetime, timezone
from sqlalchemy import select, text
from .models import CatalogBase
from .recommendation_data import _load_candidates, valid_number
from . import board_snapshots
from .admin_importer import digest


def classify_search(target, candidates):
    def name(value):return ''.join(c for c in (value or '').casefold() if c.isalnum())
    title=name(target.get('title'));year=str(target.get('year') or '')
    same=[p for p in candidates if title and title in {name(p.get('title')),name(p.get('original_title'))}]
    dated=[p for p in same if str(p.get('release_date') or '')[:4]==year and year]
    if dated:return dict(code='TITLE_YEAR_CANDIDATE_ONLY',tmdb_ids=[p['id'] for p in dated],reason='片名及年份一致，仍缺少独立外部身份或演职员证据；不自动绑定。')
    if any(not p.get('release_date') for p in same):return dict(code='UNDATED_CANDIDATE',tmdb_ids=[p['id'] for p in same],reason='同名候选缺少日期或存在多个版本；不自动绑定。')
    if same:return dict(code='YEAR_CONFLICT',tmdb_ids=[p['id'] for p in same],reason='同名候选年份不一致，需核对原始首映、地区上映及制作身份。')
    if candidates:return dict(code='SEARCH_RESULTS_NOT_EXACT',tmdb_ids=[],reason='搜索返回近似片名或关联作品，没有精确身份依据。')
    return dict(code='SEARCH_NO_MOVIE_CANDIDATE',tmdb_ids=[],reason='本轮公开电影搜索未命中；不据此断言 TMDB 不存在。')


def pending_identity(engine, raw):
    """Details can confirm a TMDB movie exists, not that it is absent locally."""
    ext, m = (CatalogBase.metadata.tables[n] for n in ('movie_external_ids', 'movies'))
    with engine.connect() as c:
        claims = []
        for source, eid in (('tmdb',str(raw['id'])),('imdb',raw.get('imdb_id'))):
            if eid:
                claims.extend(dict(movie_id=mid,source=source) for mid in c.execute(select(ext.c.movie_id).where(ext.c.source==source,ext.c.external_id==eid)).scalars())
        titles = {v for v in (raw.get('title'),raw.get('original_title')) if v}
        from sqlalchemy import or_
        names = c.execute(select(m.c.movie_id,m.c.title,m.c.release_year).where(or_(m.c.title.in_(titles),m.c.original_title.in_(titles)))).all() if titles else []
    return dict(status='POSSIBLE_EXISTING_MOVIE_REQUIRES_REVIEW' if claims or names else 'VERIFIED_EXTERNAL_MOVIE_NOT_IMPORTED',
                identity_claims=claims,title_candidates=[dict(movie_id=mid,title=title,year=year) for mid,title,year in names])


def verify_run(engine, run):
    """Independent readback of committed receipts, metadata hashes and frozen columns."""
    if not run:return dict(status='NO_RUN',verified=0)
    t=CatalogBase.metadata.tables;batch,source,external=(t[n] for n in ('ingest_batches','movie_source_records','movie_external_ids'))
    succeeded=[item for item in run['items'] if item.get('status')=='SUCCEEDED']
    refs=['admin-import:'+item['import_job_id'] for item in succeeded]
    with engine.connect() as c:
        receipts={ref:counts for ref,counts in c.execute(select(batch.c.source_snapshot_ref,batch.c.counts_json).where(batch.c.source_snapshot_ref.in_(refs),batch.c.status=='succeeded'))} if refs else {}
        records={}
        mids=[item['movie_id'] for item in succeeded]
        if mids:
            for mid,tid,payload,payload_hash in c.execute(select(source.c.movie_id,external.c.external_id,source.c.metadata_json,source.c.payload_hash).join(external,(external.c.movie_id==source.c.movie_id)&(external.c.source=='tmdb')).where(source.c.movie_id.in_(mids),source.c.source=='tmdb')):
                if isinstance(payload,dict) and str(payload.get('id'))==tid:records[(mid,payload_hash)]=payload
        frozen=list(t)
        actual=c.execute(text('SELECT TABLE_NAME,COLUMN_NAME FROM information_schema.columns WHERE TABLE_SCHEMA=DATABASE()')).all() if engine.dialect.name=='mysql' else []
    issues=[]
    for item in succeeded:
        receipt=receipts.get('admin-import:'+item['import_job_id'])
        if not receipt or receipt!={k:v for k,v in (item.get('result') or {}).items() if k!='reconciled'}:
            issues.append(dict(movie_id=item['movie_id'],code='RECEIPT_MISMATCH'));continue
        hashes=receipt.get('source_hashes',[])
        payload=records.get((item['movie_id'],hashes[0])) if len(hashes)==1 else None
        if not payload or payload.get('metadata_only') is not True or digest(dict(payload,entries=[]))!=hashes[0]:
            issues.append(dict(movie_id=item['movie_id'],code='SOURCE_HASH_OR_IDENTITY_MISMATCH'))
    expected={(name,col.name) for name in frozen for col in t[name].columns}
    relevant={(name,col) for name,col in actual if name in frozen}
    schema_ok=relevant==expected if actual else None
    return dict(status='PASS' if not issues and schema_ok is True else 'REVIEW_REQUIRED',verified=len(succeeded)-len(issues),issues=issues,
                frozen_schema=dict(tables=len(frozen),columns=len(expected),actual_columns=len(relevant),matches=schema_ok),
                database_writes=0,model_dispatches=0)


def inventory(engine):
    rows = _load_candidates(engine)
    t = CatalogBase.metadata.tables
    with engine.connect() as c:
        identities = {}
        for mid, source, eid in c.execute(select(t['movie_external_ids'].c.movie_id, t['movie_external_ids'].c.source, t['movie_external_ids'].c.external_id)):
            identities.setdefault(mid, {})[source] = eid
        ratings = {mid: dict(score=float(score) if score is not None else None, votes=votes)
                   for mid, score, votes in c.execute(select(t['movie_ratings_summary'].c.movie_id, t['movie_ratings_summary'].c.score_value, t['movie_ratings_summary'].c.vote_count).where(t['movie_ratings_summary'].c.source == 'tmdb'))}
    mapped = {str(r['tmdb_id']): r for r in rows if r['tmdb_id']}
    gaps, refresh, facts = [], [], []
    for r in rows:
        summary = ratings.get(r['movie_id'], {})
        item = dict(movie_id=r['movie_id'], title=r['title'], original_title=r.get('original_title'), year=r.get('release_year'),
                    tmdb_id=r['tmdb_id'], imdb_id=identities.get(r['movie_id'], {}).get('imdb'),
                    tmdb_rating=r['tmdb_rating'], vote_count=r['vote_count'], revenue=r['revenue'],
                    feature_as_of=r['feature_as_of'], revenue_as_of=r.get('revenue_as_of'), revenue_source=r.get('revenue_source'), rating_summary=summary)
        facts.append(item)
        if not r['tmdb_id']:
            gaps.append(dict(item, status='UNRESOLVED_IDENTITY', reason='未绑定电影 TMDB ID；历史搜索未命中不代表不存在'))
        elif r['tmdb_rating'] is None or r['vote_count'] is None or r['revenue'] is None:
            refresh.append(item)
    pending, windows = {}, []
    for channel in ('popular', 'trending_day', 'trending_week'):
        doc = board_snapshots.read(channel)
        if not doc:
            continue
        windows.append(dict(channel=channel, captured_at=doc['captured_at']))
        for pos, raw in enumerate(doc['items'], 1):
            tid = raw.get('id')
            if type(tid) is not int or tid <= 0 or raw.get('adult') is True or str(tid) in mapped:
                continue
            title, year = raw.get('title'), str(raw.get('release_date') or '')[:4]
            # Title/year are candidate evidence only; never an identity binding.
            collisions = [r['movie_id'] for r in rows if (title and title == r['title']) or (raw.get('original_title') and raw['original_title'] == r.get('original_title'))]
            item = pending.setdefault(tid, dict(tmdb_id=tid, title=title, original_title=raw.get('original_title'), year=year,
                                              status='NEEDS_IDENTITY_REVIEW' if collisions else 'EXTERNAL_NOT_IMPORTED',
                                              candidate_movie_ids=collisions, sources=[]))
            item['sources'].append(dict(channel=channel, rank=raw.get('source_rank', pos), captured_at=doc['captured_at']))
    scope = dict(refresh=[dict(movie_id=r['movie_id'], tmdb_id=r['tmdb_id']) for r in refresh],
                 gaps=[dict(movie_id=r['movie_id'], title=r['title'], year=r['year']) for r in gaps],
                 pending=[r['tmdb_id'] for r in pending.values()])
    return dict(as_of=datetime.now(timezone.utc).isoformat(), scope=scope, scope_digest=digest(scope), counts=dict(active=len(rows), mapped=len(mapped), identity_gaps=len(gaps),
                reliable_tmdb_rating=sum(r['tmdb_rating'] is not None for r in rows), known_votes=sum(r['vote_count'] is not None for r in rows),
                known_revenue=sum(r['revenue'] is not None for r in rows), refresh_candidates=len(refresh), pending=len(pending)),
                identity_gaps=gaps, refresh_candidates=refresh, pending=list(pending.values()), windows=windows, facts=facts,
                limitations=['票房为来源记录的累计美元收入，0/缺失视为未知。', '未核验候选不自动入库；不将电视剧 ID 绑定为电影。'])
