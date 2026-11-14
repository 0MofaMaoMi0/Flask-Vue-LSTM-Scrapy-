"""Bounded read-only aggregates. No raw event, prompt, key, or request response is returned."""
from collections import Counter,defaultdict
from sqlalchemy import select,func,case,distinct
from catalog.models import CatalogBase
from .registry import metric

def catalog(engine,window,dimension=None):
    md=CatalogBase.metadata;m=md.tables['movies'];active=m.c.catalog_status=='active';metrics=[]
    with engine.connect() as c:
        total=c.scalar(select(func.count()).select_from(m).where(active))
        def add(name,value,source='movies',**kw):metrics.append(metric('catalog.'+name,value,source,'CATALOG_SNAPSHOT',dict(from_=None,to=None),kw.pop('sample_size',total),**kw))
        statuses=dict(c.execute(select(m.c.catalog_status,func.count()).group_by(m.c.catalog_status)).all())
        add('active_movie_count',total);add('status_distribution',statuses,notes='Catalog metadata counts only; hidden movie details are not exposed.')
        for table,name,field in [('movie_external_ids','source_coverage','source'),('movie_ratings_summary','rating_coverage','source')]:
            t=md.tables[table];stmt=select(t.c[field],func.count(distinct(t.c.movie_id))).join(m,m.c.movie_id==t.c.movie_id).where(active)
            if table=='movie_ratings_summary':stmt=stmt.where(t.c.score_value.is_not(None))
            add(name,dict(c.execute(stmt.group_by(t.c[field])).all()),table,denominator=total,notes='Distinct active movies per source; sources overlap; scores are never averaged.')
        g=md.tables['genres'];links=md.tables['movie_genres']
        genres=dict(c.execute(select(g.c.name_zh,func.count(distinct(links.c.movie_id))).select_from(links.join(g).join(m)).where(active).group_by(g.c.name_zh).limit(500)).all())
        add('genres_distribution',genres,'movie_genres+genres+movies',denominator=total,notes='Multi-label counts; percentages need not sum to 100%.')
        decade=case((m.c.release_year.is_(None),'unknown'),else_=func.concat(func.floor(m.c.release_year/10)*10,'s'))
        add('decades_distribution',dict(c.execute(select(decade,func.count()).where(active).group_by(decade)).all()))
        overview=c.scalar(select(func.count()).select_from(m).where(active,m.c.overview.is_not(None),func.length(func.trim(m.c.overview))>0))
        add('overview_completeness',overview/total if total else None,unit='ratio',denominator=total,notes='Nonblank overview / active movies.')
        add('poster_coverage',None,'movie_assets',status='UNAVAILABLE',unit='ratio',denominator=total,notes='Asset URL presence does not establish accessibility; no network probe performed.')
        for table,field in [('ingest_batches','status'),('ingest_items','status'),('data_quality_issues','status')]:
            t=md.tables[table];rows=dict(c.execute(select(t.c[field],func.count()).group_by(t.c[field])).all());add(table,rows,table,sample_size=sum(rows.values()),notes='All-time snapshot; aggregate only, no sensitive source records.')
    if dimension:
        key={'genre':'genres_distribution','decade':'decades_distribution','source':'source_coverage'}[dimension];metrics=[v for v in metrics if v['metric_id']=='catalog.'+key]
    for v in metrics:v['window']={'from':None,'to':None}
    return metrics

def behavior(engine,repository,user,start,end,window):
    states=repository.tables['states'];events=repository.tables['events'];md=CatalogBase.metadata;m=md.tables['movies'];links=md.tables['movie_genres'];g=md.tables['genres']
    with engine.connect() as c:
        raw=list(c.execute(select(states.c.movie_id,states.c.state).where(states.c.user_id==user).limit(10001)))
        if len(raw)>10000:raise OverflowError()
        counts={key:sum(key in (row.state or {}) and bool(row.state[key]) for row in raw) for key in ('rating','favorite','want','watched','not_interested')}
        day=func.date(events.c.created_at)
        daily=[{'date':str(row[0]),'count':row[1]} for row in c.execute(select(day,func.count()).where(events.c.user_id==user,events.c.created_at>=start,events.c.created_at<end).group_by(day).order_by(day))]
        positive=[r.movie_id for r in raw if (r.state or {}).get('favorite') or (r.state or {}).get('want') or (r.state or {}).get('rating',0)>=3.5]
        genres=dict(c.execute(select(g.c.name_zh,func.count(distinct(links.c.movie_id))).select_from(links.join(g).join(m)).where(links.c.movie_id.in_(positive),m.c.catalog_status=='active').group_by(g.c.name_zh).limit(500)).all()) if positive else {}
    n=len(raw);source='user_movie_states';scope='CURRENT_USER'
    result=[metric('behavior.'+key,value,source,scope,{'from':None,'to':None},n,notes='Current latest state, independent of event time filter; rating .5–5.') for key,value in counts.items()]
    result.append(metric('behavior.daily_events',daily,'user_movie_events.created_at',scope,window,sum(r['count'] for r in daily),notes='UTC server-recorded event time; half-open window; unique(user_id,event_id).'))
    result.append(metric('behavior.genre_preferences',genres,'user_movie_states+movie_genres+genres',scope,{'from':None,'to':None},len(positive),denominator=len(positive),notes='Unweighted current favorite OR want OR rating>=3.5; watched alone is not positive; overlapping genres.'))
    return result

def summarize_usage(rows):
    buckets={};known={'input_tokens':0,'output_tokens':0,'cached_input_tokens':0};provided=Counter();unknown=0;statuses=Counter();calls=0
    for r in rows:
        if r['status']=='NOT_DISPATCHED':continue
        calls+=1;tokens=r['tokens'] if isinstance(r['tokens'],dict) else {};statuses[r['status']]+=1
        valid={k:(v if type(v) is int and v>=0 else None) for k,v in ((k,tokens.get(k)) for k in known)}
        if valid['cached_input_tokens'] is not None and (valid['input_tokens'] is None or valid['cached_input_tokens']>valid['input_tokens']):valid['cached_input_tokens']=None
        missing=valid['input_tokens'] is None or valid['output_tokens'] is None;unknown+=missing
        key=(r['created_at'].date().isoformat(),r['model'],tokens.get('kind','LEGACY_UNKNOWN'),bool(tokens.get('paid_request')))
        bucket=buckets.setdefault(key,dict(date=key[0],model=key[1],provenance=key[2],paid_request=key[3],calls=0,unknown_calls=0,input_tokens=None,output_tokens=None,cached_input_tokens=None))
        bucket['calls']+=1;bucket['unknown_calls']+=missing
        for k,v in valid.items():
            if v is not None:known[k]+=v;provided[k]+=1;bucket[k]=(bucket[k] or 0)+v
    for bucket in buckets.values():
        i=bucket['input_tokens'];cached=bucket['cached_input_tokens'];bucket['uncached_input_tokens']=i-cached if i is not None and cached is not None and i>=cached else None
    return dict(calls=calls,statuses=dict(statuses),unknown_calls=unknown,known_tokens={k:v if provided[k] else None for k,v in known.items()},reported_calls=dict(provided),daily_models=sorted(buckets.values(),key=lambda x:(x['date'],x['model'],x['provenance'])),amount=None,currency=None,amount_status='UNKNOWN',notes='Known token subtotals only; cached input is a subset, never added to input. MOCK and legacy records retain provenance; paid flag is not a provider bill.')

def usage(app,user,start,end,window):
    if 'm7_engine' not in app.extensions:return [metric('ai.usage',None,'M7 usage','CURRENT_USER',window,0,status='UNAVAILABLE')]
    t=app.extensions['m7_ledger'].md.tables['usage']
    with app.extensions['m7_engine'].connect() as c:
        rows=list(c.execute(select(t.c.model,t.c.status,t.c.tokens,t.c.created_at).where(t.c.user_id==user,t.c.created_at>=start,t.c.created_at<end).order_by(t.c.created_at).limit(10001)).mappings())
    if len(rows)>10000:raise OverflowError()
    data=summarize_usage(rows)
    req=app.extensions['m7_ledger'].md.tables['requests']
    created=func.json_unquote(func.json_extract(req.c.response,'$.created'))
    state=func.json_unquote(func.json_extract(req.c.response,'$.state'))
    with app.extensions['m7_engine'].connect() as c:
        data['request_states']=dict(c.execute(select(state,func.count()).where(req.c.user_id==user,created>=start.isoformat(),created<end.isoformat()).group_by(state)).all())
    data['request_state_notes']='Requests with retained creation timestamp only; expired tombstones lack timestamps and are excluded. Request counts differ from model dispatch counts. No prompt or request body is selected.'
    return [metric('ai.usage',data,'M7 usage (current user; non-dispatched attempts excluded)','CURRENT_USER',window,data['calls'],notes=data['notes']),metric('ai.bill_amount',None,'Provider bill unavailable','CURRENT_USER',window,data['calls'],status='UNKNOWN',unit='currency',notes='Token totals are not an actual bill.'),metric('ai.latency',None,'No durable latency field','CURRENT_USER',window,data['calls'],status='UNAVAILABLE',unit='ms')]


