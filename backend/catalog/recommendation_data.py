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
from functools import lru_cache
from pathlib import Path
from sqlalchemy import Integer, Numeric, String, and_, case, cast, column, func, inspect, literal, or_, select, table
from . import current_state, public_catalog_cache
from .models import CatalogBase
from .media_urls import asset_url


def invalidate(engine):
    public_catalog_cache.invalidate(engine)


def _cached_candidates(engine, copy_mode):
    def build_all(counters):
        return _load_candidates(engine, stats=counters)

    def build_subset(movie_ids, counters):
        return _load_candidates(engine, stats=counters, movie_ids=movie_ids)

    return public_catalog_cache.load(
        engine, build_all, build_subset, finalize, copy_mode=copy_mode, swr=True,
    )["movies"]


def load_candidates(engine):
    from .tmdb_connection import enrich

    movies = _cached_candidates(engine, "shallow")
    for movie in movies:
        # Keep mutable fields local to this call for existing consumers.
        movie["genres"] = list(movie["genres"])
        movie["director_ids"] = list(movie.get("director_ids") or ())
        movie["actor_ids"] = list(movie.get("actor_ids") or ())
        if "production_countries" in movie:
            movie["production_countries"] = [
                dict(country) for country in movie["production_countries"]
            ]
    return enrich(movies)


def load_candidates_readonly(engine):
    """Return immutable public ranking inputs without request-scoped TMDB enrichment."""
    return _cached_candidates(engine, "readonly")


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


def _apply_current(connection,byid,mappings,stats,*,bounded=False):
    """Attach each topic's current facts; return ids that still need fallback.

    Topics are resolved independently: countries come from the ``countries``
    capture, rating/heat/votes from ``metrics``, poster/revenue/budget from
    ``base``.  A topic that never succeeded leaves its fields at the historical
    fallback path; ``EMPTY`` is a successful "no value" and never falls back.
    """
    pending=set(byid)
    if not current_state.has_metadata_table(connection.engine if hasattr(connection,'engine') else None):
        return pending
    meta={movie_id:entry for movie_id,entry in current_state.load_topics(connection,movie_ids=byid if bounded else None).items()
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
    if subset == []:
        stats.update(movies=0, legacy_fallback_movies=0)
        return []
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
        pending=_apply_current(c,byid,mappings,stats,bounded=subset is not None)
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
        archive=archived_features()
        for mid,row in byid.items():
            for fact in archive.get(mid,()):
                if str(row['tmdb_id'])!=str(fact.get('id')):continue
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


def archived_features():
    path = Path(__file__).with_name('data') / 'archived_features.json'
    try:
        stat = path.stat()
    except FileNotFoundError:
        return {}
    return _read_archive(path, stat.st_mtime_ns, stat.st_size)


@lru_cache(maxsize=2)
def _read_archive(path, modified, size):
    rows = {}
    for fact in json.loads(path.read_text(encoding='utf-8')).get('rows', []):
        rows.setdefault(fact.get('movie_id'), []).append(fact)
    return rows


def attach_backdrops(engine, items):
    ids = {item['movie_id'] for item in items if item.get('movie_id') is not None
           and not item.get('backdrop')}
    urls = {}
    if ids:
        assets = CatalogBase.metadata.tables['movie_assets']
        statement = (select(assets.c.movie_id, assets.c.source, assets.c.asset_type,
                            assets.c.source_path)
                     .where(assets.c.movie_id.in_(sorted(ids)), assets.c.asset_type == 'backdrop')
                     .order_by(assets.c.is_primary.desc(), assets.c.asset_id))
        with engine.connect() as connection:
            for row in connection.execute(statement).mappings():
                if row['movie_id'] not in urls and (url := asset_url(row)):
                    urls[row['movie_id']] = url
    for item in items:
        if not item.get('backdrop'):
            item['backdrop'] = urls.get(item.get('movie_id'))


def sql_feature_projection(connection, pool, *, revenue=False):
    """Project verified scalar facts in SQL without returning source payloads to Python."""
    tables = CatalogBase.metadata.tables
    sources, external = (tables[name] for name in ('movie_source_records', 'movie_external_ids'))
    mysql = connection.dialect.name == 'mysql'

    def value(document, path):
        expression = func.json_extract(document, path)
        return func.json_unquote(expression) if mysql else expression

    def kind(document, path):
        return func.lower(func.json_type(func.json_extract(document, path)) if mysql
                          else func.json_type(document, path))

    def number(document, name, *, integer=False):
        path = '$.' + name
        types = ('integer',) if integer else ('integer', 'real', 'double', 'decimal')
        return case((kind(document, path).in_(types), cast(value(document, path), Integer if integer else Numeric(32, 12))))

    mapped = (select(pool.c.movie_id, external.c.external_id)
              .select_from(pool.outerjoin(external, and_(external.c.movie_id == pool.c.movie_id,
                                                         external.c.source == 'tmdb')))
              .cte('rec_mapped'))
    payload = sources.c.metadata_json
    def historical(field, *conditions):
        return (select(field).select_from(sources)
                .where(sources.c.movie_id == mapped.c.movie_id, sources.c.source == 'tmdb',
                       cast(value(payload, '$.id'), String) == mapped.c.external_id, *conditions)
                .order_by(sources.c.fetched_at.desc(), sources.c.id.desc()).limit(1)
                .correlate(mapped).scalar_subquery())

    latest = sources.alias('rec_latest_payload')
    join = mapped
    latest_payload = latest.c.metadata_json
    feature_stamp = cast(latest.c.fetched_at, String)
    base_applied, base_empty = literal(False), literal(False)
    metrics_applied = literal(False)
    metrics_payload, base_payload = latest_payload, latest_payload
    inspector = inspect(connection)
    if inspector.has_table('tmdb_movie_metadata'):
        columns = {entry['name'] for entry in inspector.get_columns('tmdb_movie_metadata')}
        meta = table('tmdb_movie_metadata', *(column(name) for name in columns))
        provenance = meta.c.get('provenance_json', literal(None))
        base_pk = meta.c.get('source_record_pk', literal(None))
        base_at = meta.c.get('base_at', literal(None))
        base_state = value(provenance, '$.base.state')
        metrics_state = value(provenance, '$.metrics.state')
        base = sources.alias('rec_base_payload')
        metrics = sources.alias('rec_metrics_payload')
        matching_meta = cast(meta.c.tmdb_id, String) == mapped.c.external_id
        join = join.outerjoin(meta, and_(meta.c.movie_id == mapped.c.movie_id, matching_meta))

        def attributed_record(record, pk):
            return and_(record.c.id == pk, record.c.movie_id == mapped.c.movie_id,
                        record.c.source == 'tmdb',
                        cast(value(record.c.metadata_json, '$.id'), String) == mapped.c.external_id)

        join = join.outerjoin(base, attributed_record(base, func.coalesce(number(provenance, 'base.source_record_pk', integer=True), base_pk)))
        join = join.outerjoin(metrics, attributed_record(metrics, number(provenance, 'metrics.source_record_pk', integer=True)))
        base_empty = base_state == 'EMPTY'
        base_present = and_(base.c.id.is_not(None), or_(base_state == 'PRESENT', base_at.is_not(None)))
        base_applied = or_(base_empty, base_present)
        metrics_empty = metrics_state == 'EMPTY'
        metrics_applied = or_(metrics_empty, and_(metrics_state == 'PRESENT', metrics.c.id.is_not(None)))
        metrics_payload = case((metrics_empty, None), (metrics_applied, metrics.c.metadata_json),
                               (base_applied, None), else_=latest_payload)
        base_payload = case((base_empty, None), (base_present, base.c.metadata_json), else_=latest_payload)
        feature_stamp = case((base_applied, func.coalesce(value(provenance, '$.base.fetched_at'),
                                                           cast(base_at, String))), else_=feature_stamp)

    latest_pk = case((base_applied, None), else_=historical(sources.c.id))
    join = join.outerjoin(latest, latest.c.id == latest_pk)

    amount = literal(None)
    if revenue:
        base_amount = number(base_payload, 'revenue')
        historical_amount = number(payload, 'revenue')
        amount = case((base_empty, None), (base_amount > 0, base_amount),
                      else_=historical(historical_amount, historical_amount > 0))

    archive_ids, archive = _archive_projection()
    archive_key = literal('$.') + func.json_quote(cast(mapped.c.movie_id, String) + literal(':') + mapped.c.external_id)
    archive_doc = case((mapped.c.movie_id.in_(archive_ids), func.json_extract(literal(archive), archive_key))) if archive_ids else literal(None)
    archive_stamp = value(archive_doc, '$.stamp')
    newer_archive = and_(archive_stamp.is_not(None),
                         or_(feature_stamp.is_(None),
                             func.substr(func.replace(feature_stamp, ' ', 'T'), 1, 19) < func.substr(archive_stamp, 1, 19)))
    raw = (select(mapped.c.movie_id, feature_stamp.label('feature_stamp'), metrics_payload.label('metrics_doc'),
                  amount.label('amount'), archive_doc.label('archive_doc'), newer_archive.label('newer_archive'),
                  metrics_applied.label('metrics_applied'), base_applied.label('base_applied'))
           .select_from(join).cte('rec_raw_facts'))
    effective_metrics = case((and_(raw.c.newer_archive, ~func.coalesce(raw.c.metrics_applied, False)),
                              raw.c.archive_doc), else_=raw.c.metrics_doc)
    heat = number(effective_metrics, 'popularity')
    votes = number(effective_metrics, 'vote_count', integer=True)
    score = number(effective_metrics, 'vote_average')
    archive_amount = number(raw.c.archive_doc, 'revenue') if revenue else literal(None)
    total_revenue = case((and_(raw.c.newer_archive, ~func.coalesce(raw.c.base_applied, False), archive_amount > 0),
                          archive_amount), else_=raw.c.amount)
    total_revenue = func.floor(total_revenue) if mysql else cast(total_revenue, Integer)
    return (select(raw.c.movie_id,
                   case((heat >= 0, heat)).label('heat'),
                   case((votes >= 0, votes)).label('votes'),
                   case((and_(votes > 0, score.between(0, 10)), score)).label('score'),
                   total_revenue.label('revenue'))
            .cte('rec_facts'))


def _archive_projection():
    archive = archived_features()
    projected = {}
    for movie_id, facts in archive.items():
        for fact in facts:
            key = f"{movie_id}:{fact.get('id')}"
            stamp = fact.get('archive_date', '') + 'T00:00:00Z'
            old = projected.get(key, {})
            if old.get('stamp', '') >= stamp:
                continue
            projected[key] = {field: fact[field] for field in ('popularity', 'vote_average', 'vote_count', 'revenue') if field in fact}
            if not valid_number(projected[key].get('revenue')) or not projected[key]['revenue']:
                projected[key]['revenue'] = old.get('revenue')
            projected[key]['stamp'] = stamp
    return sorted(mid for mid in archive if type(mid) is int), json.dumps(projected, separators=(',', ':'))


def finalize(movies):
    """Recompute catalog heat normalisation after a full or partial refresh."""
    maximum=max((math.log1p(row['tmdb_popularity']) for row in movies if row.get('tmdb_popularity') is not None),default=0)
    for row in movies:
        row['catalog_popularity']=(math.log1p(row['tmdb_popularity'])/maximum if maximum else 0.0) if row.get('tmdb_popularity') is not None else None
    return movies
