"""Administrator read models. No provider requests, credentials or DML."""
from datetime import datetime, timezone
import json, re, uuid
from sqlalchemy import select,func,and_,case
from admin_dashboard import _token_value,_true_json
from catalog.models import CatalogBase
from app.models import User


def ai_usage(app,start,end):
    engine=app.extensions.get('m7_engine');ledger=app.extensions.get('m7_ledger')
    if engine is None or ledger is None:return dict(status='UNAVAILABLE',groups=[],daily=[],models=[],statuses=[],amount=None,amount_status='UNKNOWN')
    t=ledger.md.tables['usage'];base=and_(t.c.created_at>=start,t.c.created_at<end)
    inp=_token_value(t.c.tokens,'input_tokens');out=_token_value(t.c.tokens,'output_tokens');cached_raw=_token_value(t.c.tokens,'cached_input_tokens')
    cached=case((and_(inp.is_not(None),cached_raw<=inp),cached_raw),else_=None)
    kind=func.upper(func.json_unquote(func.json_extract(t.c.tokens,'$.kind')))
    mock=case(((func.lower(t.c.model).like('mock%'))|kind.in_(('MOCK','M7_MOCK')),1),else_=0)
    real=case((and_(kind=='BYOK_AGENT',_true_json(t.c.tokens,'paid_request'),mock==0),1),else_=0)
    bucket=case((mock==1,'MOCK'),(real==1,'REAL'),else_='UNCONFIRMED')
    metrics=[func.count().label('calls')]
    for key,value in [('input',inp),('output',out),('cached_input',cached)]:
        metrics.extend([func.sum(value).label(key),func.count(value).label(key+'_reported')])
    metrics.append(func.sum(case((and_(inp.is_not(None),out.is_not(None)),0),else_=1)).label('unknown_calls'))
    def summary(row):
        n=int(row['calls']);d=dict(calls=n,unknown_calls=int(row['unknown_calls'] or 0))
        for key in ('input','output','cached_input'):
            reported=int(row[key+'_reported']);d[key+'_reported_calls']=reported
            d['known_'+key+'_tokens']=int(row[key] or 0) if reported or not n else None
            d[key+'_tokens']=d['known_'+key+'_tokens'] if reported==n else None
        d['known_total_tokens']=sum(d['known_'+k+'_tokens'] or 0 for k in ('input','output')) if not n or d['input_reported_calls'] or d['output_reported_calls'] else None
        d['total_tokens']=d['known_total_tokens'] if not d['unknown_calls'] else None
        d['usage_status']='PARTIAL' if d['unknown_calls'] else 'COMPLETE'
        return d
    window=and_(base,t.c.status!='NOT_DISPATCHED')
    with engine.connect() as c:
        total=summary(c.execute(select(*metrics).where(window)).mappings().one())
        groups={r['bucket']:summary(r) for r in c.execute(select(bucket.label('bucket'),*metrics).where(window).group_by(bucket)).mappings()}
        models=[dict(name=r['name'],provenance=r['bucket'],**summary(r)) for r in c.execute(select(t.c.model.label('name'),bucket.label('bucket'),*metrics).where(window).group_by(t.c.model,bucket).order_by(func.count().desc(),t.c.model)).mappings()]
        statuses=[dict(name=r['name'],**summary(r)) for r in c.execute(select(t.c.status.label('name'),*metrics).where(window).group_by(t.c.status).order_by(func.count().desc())).mappings()]
        daily=[dict(date=str(r['day']),provenance=r['bucket'],**summary(r)) for r in c.execute(select(func.date(t.c.created_at).label('day'),bucket.label('bucket'),*metrics).where(window).group_by(func.date(t.c.created_at),bucket).order_by(func.date(t.c.created_at),bucket)).mappings()]
        not_dispatched=int(c.scalar(select(func.count()).where(base,t.c.status=='NOT_DISPATCHED')) or 0)
    zero={'calls':0,'input':None,'output':None,'cached_input':None,'input_reported':0,'output_reported':0,'cached_input_reported':0,'unknown_calls':0}
    return dict(status='AVAILABLE',**total,groups=[dict(provenance=k,**groups.get(k,summary(zero))) for k in ('REAL','MOCK','UNCONFIRMED')],models=models,statuses=statuses,daily=daily,not_dispatched_calls=not_dispatched,
                amount=0 if not total['calls'] else None,amount_status='NOT_CALLED' if not total['calls'] else 'UNKNOWN',
                notes=['缓存命中 Token 是输入 Token 的子集，不重复计入总量。','未知用量保留未知，已知小计不等于完整账单。','仅查询现有账本，不触发模型调用。'])


STATUSES={'ALL','PREVIEW','QUEUED','RUNNING','SUCCEEDED','PARTIAL','FAILED','NEEDS_REVIEW','CANCELLED','EXPIRED','PENDING'}
def audit_args(args):
    if set(args)-{'days','page','page_size','status'} or any(len(args.getlist(k))!=1 for k in args):raise ValueError('Parameters')
    days=args.get('days','30');page=args.get('page','1');size=args.get('page_size','25');status=args.get('status','ALL')
    if days not in ('7','30','90') or not page.isascii() or not page.isdecimal() or not 1<=int(page)<=100000 or size not in ('10','25','50') or status not in STATUSES:raise ValueError('Parameters')
    return int(days),int(page),int(size),status


def import_audit(app,start,end,page,size,status):
    jobs=app.extensions.get('admin_import_jobs');docs=[];unreadable=0
    if jobs:
        # Read only UUID state files, never payload/before-images or credentials.
        for path in jobs.folder.glob('*.json'):
            try:
                if str(uuid.UUID(path.stem))!=path.stem or path.is_symlink() or path.stat().st_size>1500000:raise ValueError()
                d=jobs.read(path.stem)
                if d.get('job_id')!=path.stem or type(d.get('owner')) is not int:raise ValueError()
                docs.append(d)
            except (ValueError,OSError,KeyError):unreadable+=1
    refs=['admin-import:'+d['job_id'] for d in docs];t=CatalogBase.metadata.tables['ingest_batches'];engine=app.extensions['auth_engine']
    dt=func.coalesce(t.c.finished_at,t.c.started_at);where=and_(dt>=start,dt<end)
    if refs:where=and_(where,t.c.source_snapshot_ref.not_in(refs))
    if status!='ALL':where=and_(where,t.c.status==status.lower())
    files=[]
    def stamp(value):
        d=datetime.fromisoformat(value)
        return d.astimezone(timezone.utc).replace(tzinfo=None) if d.tzinfo else d
    def safe_counts(value):
        if not isinstance(value,dict):return {}
        allowed={'movies','inserted','updated','skipped','errors','review','total','source_movies','people','credits','person_assets','movie_assets','movie_source_records','movie_external_ids','genres','movie_genres'}
        counts={k:v for k,v in value.items() if k in allowed and type(v) is int and v>=0}
        items=value.get('items')
        if type(items) is list:counts['movies']=len(items)
        return counts
    with engine.connect() as c:
        owners={uid:name for uid,name in c.execute(select(User.id,User.username).where(User.id.in_({d['owner'] for d in docs})))} if docs else {}
        receipts={ref:(bid,state,counts) for ref,bid,state,counts in c.execute(select(t.c.source_snapshot_ref,t.c.batch_id,t.c.status,t.c.counts_json).where(t.c.source_snapshot_ref.in_(refs)))} if refs else {}
        for d in docs:
            try:
                updated=stamp(d.get('updated_at') or d['created_at']);state=d['status']
                if state=='PREVIEW' and d.get('expires_at') and stamp(d['expires_at'])<end:state='EXPIRED'
                if not start<=updated<end or (status!='ALL' and state!=status):continue
                receipt=receipts.get('admin-import:'+d['job_id']);plans=d.get('plans') or []
                actual_ids={str(p.get('tmdb_id')):p['movie_id'] for p in ((receipt[2].get('items') or []) if receipt and isinstance(receipt[2],dict) else []) if type(p) is dict and type(p.get('movie_id')) is int and p['movie_id']>0}
                files.append(dict(id=d['job_id'],job_id=d['job_id'],at=updated.isoformat()+'Z',created_at=d['created_at'],approved_at=d.get('approved_at'),actor=owners.get(d['owner'],'账号不可用'),source='tmdb',kind='ADMIN_IMPORT',status=state,
                                  batch_id=receipt[0] if receipt else None,commit_state=receipt[1].upper() if receipt else 'NO_RECEIPT',
                                  movie_count=len(plans),movies=[dict(movie_id=actual_ids.get(str(p.get('tmdb_id')),p.get('movie_id')),tmdb_id=p.get('tmdb_id'),title=str(p.get('title') or '')[:255],action=p.get('action')) for p in plans if type(p) is dict],
                                  counts=safe_counts(receipt[2]) if receipt else {},error_code=d.get('error_code') if re.fullmatch(r'[A-Z_]{1,64}',str(d.get('error_code') or '')) else None))
            except (ValueError,KeyError,TypeError):unreadable+=1
        legacy_count=int(c.scalar(select(func.count()).select_from(t).where(where)) or 0)
        total=legacy_count+len(files)
        offset=(page-1)*size
        if offset>=total:
            page_keys=[]
        else:
            key_rows=c.execute(select(t.c.batch_id,t.c.started_at,t.c.finished_at).where(where).order_by(dt.desc(),t.c.batch_id.desc()).limit(offset+size)).all()
            candidates=[(d['at'],d['id'],'file',idx) for idx,d in enumerate(files)]
            candidates.extend((((finished or started).isoformat()+'Z'),'batch:'+str(bid),'db',bid) for bid,started,finished in key_rows)
            candidates.sort(key=lambda item:(item[0],item[1]),reverse=True)
            page_keys=candidates[offset:offset+size]
        selected_bids=[item[3] for item in page_keys if item[2]=='db']
        db_map={}
        if selected_bids:
            rows=c.execute(select(t.c.batch_id,t.c.source,t.c.mode,t.c.status,t.c.started_at,t.c.finished_at,t.c.counts_json).where(t.c.batch_id.in_(selected_bids))).all()
            for bid,source,mode,state,started,finished,counts in rows:
                db_map[bid]=dict(id='batch:'+str(bid),job_id=None,batch_id=bid,at=(finished or started).isoformat()+'Z',actor='历史批次未记录操作者',source=source,kind='DATABASE_BATCH',status=state.upper(),commit_state=state.upper(),movie_count=None,movies=[],counts=safe_counts(counts),error_code=None)
    items=[files[item[3]] if item[2]=='file' else db_map[item[3]] for item in page_keys]
    return dict(items=items,total=total,page=page,page_size=size,pages=max(1,(total+size-1)//size),unreadable_task_records=unreadable,
                notes=['这是当前任务状态及数据库批次审计，不是每次按钮操作的不可变事件日志。','文件任务与数据库批次按任务 ID 合并；历史批次未记录的操作者保持未知。','不返回来源载荷、密钥、SQL、对话或备份文件路径。'])
