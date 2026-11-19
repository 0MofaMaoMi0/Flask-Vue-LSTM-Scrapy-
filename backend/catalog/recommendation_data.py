"""Shared full-catalog, read-only ranking inputs. Missing source facts stay missing.

The loader reads the *currently attributed* source record per movie from
``tmdb_movie_metadata`` instead of every historical payload, and wraps the
result in the shared public-catalog cache (cross-process fingerprint + atomic
snapshot).  Historical records are still consulted, but only for a bounded
fallback: movies without a current supplement capture (e.g. the 114 unmapped
titles) and cumulative revenue that a newer heat-only capture dropped.
"""
import math,json
from datetime import datetime
from pathlib import Path
from sqlalchemy import select, case
from . import current_state, public_catalog_cache
from .models import CatalogBase
from .media_urls import asset_url


def invalidate(engine):
    public_catalog_cache.invalidate(engine)


def load_candidates(engine):
    from .tmdb_connection import enrich
    def build_all(counters):
        return _load_candidates(engine,stats=counters)
    def build_subset(movie_ids,counters):
        return _load_candidates(engine,stats=counters,movie_ids=movie_ids)
    entry=public_catalog_cache.load(engine,build_all,build_subset,finalize)
    return enrich(entry["movies"])


def valid_number(value,maximum=None):
    return type(value) in (int,float) and math.isfinite(value) and value>=0 and (maximum is None or value<=maximum)


def features(row,payload,stamp,source):
    heat=payload.get('popularity');score=payload.get('vote_average');votes=payload.get('vote_count');revenue=payload.get('revenue')
    prior_revenue=row.get('revenue');prior_date=row.get('revenue_as_of') or row.get('feature_as_of');prior_source=row.get('revenue_source') or row.get('feature_source')
    row.update(tmdb_popularity=float(heat) if valid_number(heat) else None,tmdb_rating=float(score) if valid_number(score,10) and type(votes) is int and votes>0 else None,vote_count=votes if type(votes) is int and votes>=0 else None,revenue=int(revenue) if valid_number(revenue) and revenue>0 else None,popularity_observed_at=stamp,feature_source=source,feature_as_of=stamp)
    if 'revenue' in payload:row.update(revenue_as_of=stamp,revenue_source=source if row['revenue'] is not None else None)
    if row['revenue'] is None and valid_number(prior_revenue) and prior_revenue>0:
        row.update(revenue=prior_revenue,revenue_as_of=prior_date,revenue_source=prior_source)


def _stamp(value):
    if isinstance(value,datetime):return value.isoformat()+'Z'
    return value if isinstance(value,str) and value else None


def _ordered_source_snapshots(connection,sources,movies,*,source_names=('tmdb',),order_by_movie=False,movie_ids=None,batch_size=128,include_source=False,stats=None):
    # Sort only narrow columns: carrying the large source JSON through filesort
    # can exhaust MySQL sort memory after a full metadata capture. Keep every
    # historical record in the same order for identity/revenue fallback rules.
    if movie_ids is not None:
        ids=sorted({int(mid) for mid in movie_ids})
        chunks=[ids[i:i+400] for i in range(0,len(ids),400)]
    else:
        chunks=[None]
    for chunk in chunks:
        query=(select(sources.c.id,sources.c.movie_id,sources.c.fetched_at,sources.c.source)
            .join(movies,movies.c.movie_id==sources.c.movie_id)
            .where(movies.c.catalog_status=='active',sources.c.source.in_(source_names)))
        if chunk is not None:
            query=query.where(sources.c.movie_id.in_(chunk))
        ordered=connection.execute(query.order_by(*((sources.c.movie_id,case((sources.c.source=='tmdb',0),else_=1),sources.c.fetched_at.desc(),sources.c.id.desc()) if order_by_movie else (sources.c.fetched_at.desc(),sources.c.id.desc())))).all()
        for offset in range(0,len(ordered),batch_size):
            batch=ordered[offset:offset+batch_size]
            payloads=dict(connection.execute(select(sources.c.id,sources.c.metadata_json)
                .where(sources.c.id.in_([row.id for row in batch]))).all())
            if stats is not None:stats['json_reads']=stats.get('json_reads',0)+len(payloads)
            for pk,mid,observed,source in batch:
                result=(mid,payloads.get(pk),observed)
                yield result+(source,) if include_source else result


def _apply_current(connection,byid,mappings,stats):
    """Attach each topic's current facts; return ids that still need fallback.

    Topics are resolved independently: countries come from the ``countries``
    capture, rating/heat/votes from ``metrics``, poster/revenue/budget from
    ``base``.  A topic that never succeeded leaves its fields at the historical
    fallback path; ``EMPTY`` is a successful "no value" and never falls back.
    """
    pending=set(byid)
    if not current_state.has_metadata_table(connection.engine if hasattr(connection,'engine') else None):
        return pending
    meta={movie_id:entry for movie_id,entry in current_state.load_topics(connection).items()
          if movie_id in byid}
    pks=[entry['source_record_pk'] for movie in meta.values()
         for entry in movie['topics'].values() if entry.get('source_record_pk') is not None]
    records=current_state.fetch_records(connection,pks)
    stats['json_reads']=stats.get('json_reads',0)+len(records)
    applied=set()
    retained={}
    for movie_id,entry in meta.items():
        row=byid.get(movie_id)
        tmdb_id=entry.get('tmdb_id')
        mapping=mappings.get(movie_id)
        if row is None or mapping is None or str(mapping)!=str(tmdb_id):
            continue
        for topic,fact in entry['topics'].items():
            record=records.get(fact.get('source_record_pk'))
            if fact.get('state')=='EMPTY':
                payload={}
            elif not current_state.identity_ok(movie_id,tmdb_id,mapping,record):
                continue
            else:
                payload=record[2]
            if topic==current_state.BASE_TOPIC:
                current_state.apply_base(row,payload,fact.get('fetched_at'))
                row['feature_source']=current_state.CURRENT_SOURCE
                row['feature_as_of']=fact.get('fetched_at')
                applied.add(movie_id)
            elif topic==current_state.METRICS_TOPIC:
                current_state.apply_metrics(row,payload,fact.get('fetched_at'))
            elif topic==current_state.COUNTRIES_TOPIC:
                current_state.apply_countries(row,payload,fact.get('fetched_at'))
            fields={"base":("revenue","revenue_as_of","revenue_source","base_observed_at","tmdb_poster_path","feature_source","feature_as_of"),
                    "metrics":("tmdb_popularity","vote_count","tmdb_rating","popularity_observed_at"),
                    "countries":("production_countries","countries_observed_at")}[topic]
            retained.setdefault(movie_id,{}).update({key:row.get(key) for key in fields if key in row})
    pending-=applied
    missing=[mid for mid in applied if byid[mid].get('revenue') is None
             and (meta[mid]['topics'].get(current_state.BASE_TOPIC) or {}).get('state')=='PRESENT']
    if missing:
        fallbacks,fetched=current_state.revenue_fallbacks(connection,missing,mappings,
            {mid:meta[mid].get('tmdb_id') for mid in meta})
        stats['json_reads']=stats.get('json_reads',0)+fetched
        for movie_id,fact in fallbacks.items():
            row=byid.get(movie_id)
            if row is None:continue
            row.update(revenue=int(fact['revenue']),revenue_as_of=fact['fetched_at'],
                       revenue_source=current_state.HISTORICAL_SOURCE)
            retained.setdefault(movie_id,{}).update({key:row[key] for key in ('revenue','revenue_as_of','revenue_source')})
    for movie_id,fields in retained.items():byid[movie_id]['_retained_current_fields']=fields
    return pending


def _load_candidates(engine,*,stats=None,movie_ids=None):
    stats=stats if isinstance(stats,dict) else {}
    subset=sorted({int(mid) for mid in movie_ids}) if movie_ids is not None else None
    stats.setdefault('json_reads',0)
    t=CatalogBase.metadata.tables
    m,g,links,sources,external,assets=[t[n] for n in ('movies','genres','movie_genres','movie_source_records','movie_external_ids','movie_assets')]
    with engine.connect() as c:
        statement=select(m).where(m.c.catalog_status=='active')
        if subset is not None:statement=statement.where(m.c.movie_id.in_(subset))
        movies=[dict(row) for row in c.execute(statement.order_by(m.c.movie_id)).mappings()]
        byid={row['movie_id']:row for row in movies}
        stats['movies']=len(movies)
        for row in movies:row.update(genres=[],tmdb_popularity=None,popularity_observed_at=None,poster=None,poster_fallback=None,tmdb_id=None,tmdb_rating=None,vote_count=None,revenue=None,feature_source=None,feature_as_of=None,actor_ids=[],director_ids=[])
        genre_query=select(links.c.movie_id,g.c.name_zh).join(g,g.c.genre_id==links.c.genre_id).join(m,m.c.movie_id==links.c.movie_id).where(m.c.catalog_status=='active')
        if subset is not None:genre_query=genre_query.where(m.c.movie_id.in_(subset))
        for mid,name in c.execute(genre_query):
            byid[mid]['genres'].append(name)
        mapping_query=select(external.c.movie_id,external.c.external_id).where(external.c.source=='tmdb')
        if subset is not None:mapping_query=mapping_query.where(external.c.movie_id.in_(subset))
        mappings=dict(c.execute(mapping_query).all())
        for mid,tid in mappings.items():
            if mid in byid:byid[mid]['tmdb_id']=tid
        pending=_apply_current(c,byid,mappings,stats)
        stats['legacy_fallback_movies']=len(pending)
        seen=set()
        if pending:
            for mid,payload,observed in _ordered_source_snapshots(c,sources,m,movie_ids=pending,stats=stats):
                if not isinstance(payload,dict) or not mappings.get(mid) or str(payload.get('id'))!=str(mappings[mid]):continue
                stamp=observed.isoformat()+'Z' if observed else None
                if mid not in seen:
                    seen.add(mid)
                    features(byid[mid],payload,stamp,'TMDB_DATABASE_SNAPSHOT')
                # An incomplete newer capture must not erase a known cumulative
                # revenue. Preserve its actual observation date, not today's date.
                elif byid[mid]['revenue'] is None and valid_number(payload.get('revenue')) and payload['revenue']>0:
                    byid[mid].update(revenue=int(payload['revenue']),revenue_as_of=stamp,revenue_source='TMDB_HISTORICAL_DATABASE_SNAPSHOT')
        archive=Path(__file__).with_name('data')/'archived_features.json'
        if archive.is_file():
            doc=json.loads(archive.read_text(encoding='utf-8'))
            for fact in doc.get('rows',[]):
                mid=fact.get('movie_id');row=byid.get(mid) if subset is None or mid in byid else None
                if not row or str(row['tmdb_id'])!=str(fact.get('id')):continue
                stamp=fact.get('archive_date','')+'T00:00:00Z'
                if not row['feature_as_of'] or row['feature_as_of']<stamp:
                    features(row,fact,stamp,'APPROVED_STATIC_TMDB_CAPTURE')
        credits=t.get('movie_credits')
        if credits is not None:
            from sqlalchemy import or_,and_
            credit_query=select(credits.c.movie_id,credits.c.person_id,credits.c.role_type,credits.c.job).join(m,m.c.movie_id==credits.c.movie_id).where(m.c.catalog_status=='active',or_(and_(credits.c.role_type=='crew',credits.c.job=='Director'),and_(credits.c.role_type=='cast',credits.c.sort_order<10)))
            if subset is not None:credit_query=credit_query.where(credits.c.movie_id.in_(subset))
            for mid,pid,role,job in c.execute(credit_query):
                byid[mid]['director_ids' if role=='crew' else 'actor_ids'].append(int(pid))
        # Keep a second candidate: a stale primary poster must not blank the card
        # when another stored poster for the same movie still resolves.
        asset_query=select(assets).join(m,m.c.movie_id==assets.c.movie_id).where(m.c.catalog_status=='active',assets.c.asset_type=='poster')
        if subset is not None:asset_query=asset_query.where(assets.c.movie_id.in_(subset))
        for row in c.execute(asset_query.order_by(assets.c.is_primary.desc(),assets.c.asset_id)).mappings():
            movie=byid[row['movie_id']];url=asset_url(row)
            if not url:continue
            if movie['poster'] is None:movie['poster']=url
            elif url!=movie['poster'] and movie['poster_fallback'] is None:movie['poster_fallback']=url
    for row in movies:row.update(row.pop('_retained_current_fields',{}))
    if subset is None:finalize(movies)
    return movies


def finalize(movies):
    """Recompute catalog heat normalisation after a full or partial refresh."""
    maximum=max((math.log1p(row['tmdb_popularity']) for row in movies if row.get('tmdb_popularity') is not None),default=0)
    for row in movies:
        row['catalog_popularity']=(math.log1p(row['tmdb_popularity'])/maximum if maximum else 0.0) if row.get('tmdb_popularity') is not None else None
    return movies