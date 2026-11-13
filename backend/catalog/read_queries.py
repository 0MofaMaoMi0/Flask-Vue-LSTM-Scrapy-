"""Read-only Select builders. No configuration, engine, session or execution."""
from sqlalchemy import select, exists, Table, Column, BigInteger, String, Boolean, DateTime, MetaData
from .models import CatalogBase

T=CatalogBase.metadata.tables
_pa_meta=MetaData()
_PERSON_ASSETS=Table('person_assets',_pa_meta,
    Column('asset_id',BigInteger,primary_key=True),Column('person_id',BigInteger),
    Column('source',String(16)),Column('source_path',String(512)),
    Column('is_primary',Boolean),Column('license_status',String(24)),Column('updated_at',DateTime))
SOURCES=frozenset(('douban','tmdb','imdb','wikidata'))
SORTS=('updated','release','id')
MAX_LIMIT=100
MAX_OFFSET=10000

def positive(value,name):
    if type(value) is not int or not 1<=value<=9223372036854775807:
        raise ValueError('Invalid '+name)
    return value

def active_movie(movie_id):
    m=T['movies']
    return (m.c.movie_id==positive(movie_id,'movie id')) & (m.c.catalog_status=='active')

def movie_list(*, keyword=None, year_from=None, year_to=None, genre_id=None, sort='updated', limit=20, offset=0, movie_ids=None):
    if type(limit) is not int or not 1<=limit<=MAX_LIMIT or type(offset) is not int or not 0<=offset<=MAX_OFFSET:
        raise ValueError('Pagination bounds exceeded')
    if sort not in SORTS:raise ValueError('Invalid sort')
    for year in (year_from,year_to):
        if year is not None and (type(year) is not int or not 1800<=year<=2199):raise ValueError('Invalid year')
    if year_from is not None and year_to is not None and year_from>year_to:raise ValueError('Inverted year range')
    m=T['movies'];q=select(m).where(m.c.catalog_status=='active')
    if keyword is not None:
        if type(keyword) is not str or not keyword.strip() or len(keyword)>255:raise ValueError('Invalid keyword')
        # contains(autoescape=True) binds literal text and escapes /, percent, underscore.
        q=q.where(m.c.title.contains(keyword,autoescape=True,escape='/'))
    if year_from is not None:q=q.where(m.c.release_year>=year_from)
    if year_to is not None:q=q.where(m.c.release_year<=year_to)
    if genre_id is not None:
        g=T['movie_genres'];q=q.where(exists(select(1).select_from(g).where(g.c.movie_id==m.c.movie_id,g.c.genre_id==positive(genre_id,'genre id'))))
    if movie_ids is not None:
        if type(movie_ids) is not list or any(type(i) is not int or i<1 for i in movie_ids):raise ValueError('Movie scope')
        q=q.where(m.c.movie_id.in_(movie_ids))
    order={'updated':(m.c.updated_at.desc(),m.c.movie_id.desc()),'release':(m.c.release_date.desc(),m.c.movie_id.desc()),'id':(m.c.movie_id.asc(),)}
    return q.order_by(*order[sort]).limit(limit).offset(offset)

def movie_detail(movie_id):return select(T['movies']).where(active_movie(movie_id)).limit(1)

def related(movie_id,kind):
    positive(movie_id,'movie id')
    m=T['movies']
    if kind=='genres':
        g=T['genres'];mg=T['movie_genres']
        return select(g).select_from(g.join(mg,g.c.genre_id==mg.c.genre_id)).where(mg.c.movie_id==movie_id,exists(select(1).where(active_movie(movie_id)))).order_by(g.c.genre_id)
    spec={'external_ids':('movie_external_ids','id'),'aliases':('movie_aliases','id'),'ratings':('movie_ratings_summary','source')}
    if kind not in spec:raise ValueError('Invalid detail relation')
    table,key=spec[kind];t=T[table]
    return select(t).where(t.c.movie_id==movie_id,exists(select(1).where(active_movie(movie_id)))).order_by(t.c[key])

def movie_external_ids(movie_id):return related(movie_id,'external_ids')
def movie_aliases(movie_id):return related(movie_id,'aliases')
def movie_genres(movie_id):return related(movie_id,'genres')
def movie_assets(movie_id):
    a=T['movie_assets']
    return select(a).where(a.c.movie_id==movie_id).order_by(a.c.is_primary.desc(),a.c.asset_id)

def movie_ratings(movie_id):return related(movie_id,'ratings')

def movie_by_external_id(source,external_id):
    if type(source) is not str or source not in SOURCES:raise ValueError('Invalid source')
    if type(external_id) is not str or not external_id.strip() or external_id!=external_id.strip() or len(external_id)>128:raise ValueError('Invalid external id')
    m=T['movies'];e=T['movie_external_ids']
    return select(m).select_from(m.join(e,m.c.movie_id==e.c.movie_id)).where(m.c.catalog_status=='active',e.c.source==source,e.c.external_id==external_id).limit(1)


def movie_posters(movie_ids):
    a=T['movie_assets']
    return select(a.c.movie_id,a.c.source,a.c.source_path,a.c.is_primary).where(a.c.asset_type=='poster',a.c.movie_id.in_(movie_ids)).order_by(a.c.is_primary.desc(),a.c.asset_id)

def movie_credits(movie_id):
    cr=T['movie_credits'];p=T['people'];pa=_PERSON_ASSETS
    return (select(cr.c.credit_id,cr.c.source,cr.c.role_type,cr.c.job,cr.c.character_name,cr.c.sort_order,p.c.person_id,p.c.name,pa.c.source_path.label('profile_path'))
            .select_from(cr.join(p,p.c.person_id==cr.c.person_id).outerjoin(pa,(pa.c.person_id==p.c.person_id)&(pa.c.is_primary==True)))
            .where(cr.c.movie_id==movie_id)
            .order_by(cr.c.role_type.desc(),cr.c.sort_order,cr.c.credit_id))


def movie_list_genres(ids):
    g=T['genres'];links=T['movie_genres']
    return select(links.c.movie_id,g.c.genre_id,g.c.name_zh,g.c.name_en).join(g,g.c.genre_id==links.c.genre_id).where(links.c.movie_id.in_(ids)).order_by(links.c.movie_id,g.c.genre_id)

def movie_list_ratings(ids):
    r=T['movie_ratings_summary']
    return select(r).where(r.c.movie_id.in_(ids)).order_by(r.c.movie_id,r.c.source)
