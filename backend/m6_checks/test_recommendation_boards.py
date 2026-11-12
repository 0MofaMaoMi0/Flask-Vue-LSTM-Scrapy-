"""Recommendation channel contracts. Synthetic fixtures only; no provider calls."""
from datetime import datetime,timezone,timedelta
from types import SimpleNamespace
import json
import pytest
from catalog import recommendation_boards as b
from assistant.agent_tools import AgentTools,schemas,verify_final

NOW=datetime(2026,11,13,3,tzinfo=timezone.utc)
def movie(i,**kw):
    return dict(dict(movie_id=i,tmdb_id=str(i+100),title='电影'+str(i),original_title='',catalog_status='active',is_adult=False,genres=['剧情'],release_year=2000+i,runtime_minutes=90,original_language='zh',catalog_popularity=i/20,tmdb_rating=8.,vote_count=1000,tmdb_popularity=i,revenue=i*100,actor_ids=[],director_ids=[],feature_as_of='2026-11-12T00:00:00Z'),**kw)
def board(movies,state=None,channel='daily',**kw):return b.board(movies,state or {},'u1',channel,now=NOW,**kw)

@pytest.fixture
def upstream(tmp_path,monkeypatch):
    monkeypatch.setattr(b,'DATA',tmp_path)
    for c in b.PUBLIC:
        doc=dict(schema=1,channel=c,captured_at=NOW.isoformat(),genres=[dict(id=1,name='剧情')],items=[dict(id=999,title='外部影片',genre_ids=[1],release_date='2026-01-01',vote_count=10,vote_average=7.,popularity=60,source_rank=1),dict(id=101,title='电影1',genre_ids=[1],vote_count=2000,vote_average=9.,source_rank=2)])
        (tmp_path/(c+'.json')).write_text(json.dumps(doc),encoding='utf-8')
    return tmp_path

@pytest.mark.parametrize('channel',list(b.PUBLIC))
def test_official_ranking_external_identity_and_local_subset(upstream,channel):
    r=board([movie(1)],channel=channel)
    assert [m['source_rank'] for m in r['items']]==[1,2]
    assert r['items'][0]['movie_id'] is None and not r['items'][0]['in_catalog']
    assert r['items'][0]['tmdb_url'].endswith('/999')
    local=board([movie(1)],channel=channel,scope='local')
    assert [m['source_rank'] for m in local['items']]==[2]
    assert local['items'][0]['vote_count']==2000
    assert board([movie(1)],{'1':{'not_interested':True}},channel,scope='local')['items']==[]

def test_missing_and_stale_official_board_never_fabricates(upstream):
    (upstream/'popular.json').unlink()
    assert board([movie(1)],channel='popular')['status']=='UNAVAILABLE'
    assert b.board([movie(1)],{},1,'trending_day',now=NOW+timedelta(days=2))['status']=='STALE'

def test_filter_before_rank_and_missing_runtime_is_not_a_match(upstream):
    rows=[movie(i) for i in range(1,105)]+[movie(200,release_year=1980,genres=['科幻'])]
    r=board(rows,channel='daily',filters={'year_to':1990,'genres_any':['科幻']},limit=1)
    assert r['items'][0]['movie_id']==200
    assert board([movie(1)],channel='popular',filters={'runtime_max':120})['items'][0]['movie_id']==1

def test_reliable_rating_and_revenue_unknowns():
    rows=[movie(1,tmdb_rating=10.,vote_count=2,revenue=None),movie(2,tmdb_rating=9.,vote_count=10000,revenue=100),movie(3,tmdb_rating=9.1,vote_count=100,revenue=500),movie(4,revenue=0)]
    rating=board(rows,channel='top_rated')
    assert 1 not in [m['movie_id'] for m in rating['items']]
    assert rating['items'][0]['movie_id']==2
    assert [m['movie_id'] for m in board(rows,channel='revenue')['items']]==[3,2]
    assert board(rows,channel='revenue',filters={'year_to':2002})['items'][0]['movie_id']==2

@pytest.mark.parametrize('channel',['daily','weekly'])
def test_period_persistence_negative_feedback_and_user_isolation(tmp_path,channel):
    rows=[movie(i) for i in range(1,20)]
    first=board(rows,channel=channel,cache_dir=tmp_path)
    ids=[m['movie_id'] for m in first['items']]
    changed=[dict(m,catalog_popularity=1-m['catalog_popularity']) for m in rows]
    second=board(changed,{'1':{'favorite':True}},channel,cache_dir=tmp_path)
    assert [m['movie_id'] for m in second['items']]==ids
    third=board(changed,{str(ids[0]):{'not_interested':True},str(ids[1]):{'watched':True}},channel,cache_dir=tmp_path)
    assert not {ids[0],ids[1]} & {m['movie_id'] for m in third['items']}
    b.board(rows,{},'other',channel,now=NOW,cache_dir=tmp_path)
    assert len(list(tmp_path.glob('*.json')))==2
    board(rows,channel=channel,cache_dir=tmp_path,keyword='电影1')
    assert len(list(tmp_path.glob('*.json')))==3
    assert all(set(json.loads(p.read_text()))=={'version','period','ids'} for p in tmp_path.glob('*.json'))

def test_watchlist_and_similarity_are_separate_business_semantics():
    rows=[movie(1,director_ids=[5]),movie(2,director_ids=[5]),movie(3,actor_ids=[9]),movie(4)]
    state={'1':{'favorite':True},'3':{'want':True},'4':{'watched':True}}
    similar=board(rows,state,'because')
    assert similar['items'][0]['movie_id']==2 and '同导演' in similar['items'][0]['reasons'][0]
    assert [m['movie_id'] for m in board(rows,state,'watchlist')['items']]==[3]
    assert board(rows,channel='because')['status']=='NO_DATA'
    assert board(rows,channel='watchlist')['status']=='NO_DATA'

def test_weekly_diversity_and_period_boundary():
    rows=[movie(i,genres=['剧情'] if i<8 else ['科幻'],release_year=1990 if i<8 else 1970) for i in range(1,14)]
    result=board(rows,channel='weekly')
    assert len({tuple(m['genres']) for m in result['items'][:4]})==2
    assert b.period_for('daily',datetime(2026,11,12,17,tzinfo=timezone.utc))=='2026-11-13'
    assert b.period_for('weekly',NOW)=='2026-11-09'
    assert result['paid_model_tokens']==0

@pytest.mark.parametrize('kw',[dict(channel='sql'),dict(limit=True),dict(filters={'year_from':2020,'year_to':1990}),dict(min_votes=-1),dict(weights={'favorite':float('nan'),'want':2,'rating':1})])
def test_invalid_parameters_rejected(kw):
    with pytest.raises(ValueError):board([movie(1)],**kw)

def test_agent_tool_uses_user_and_cannot_weaken_constraints():
    rows=[movie(1,release_year=1980),movie(2,release_year=2020)]
    calls=[]
    class Reader:
        behavior=SimpleNamespace(snapshot=lambda user:{})
        def recommendation_board(self,user,channel,filters,keyword,limit,scope='all'):
            calls.append(user)
            return b.board(rows,{},user,channel,filters=filters,trusted_filters=True,scope='local',limit=limit,now=NOW)
        def get_movies(self,ids):return [m for m in rows if m['movie_id'] in ids]
    agent=AgentTools(Reader(),7,conditions={'year_to':1990,'excluded_genres':['恐怖']})
    result=agent.execute('get_recommendation_board',dict(channel='daily',filters={'year_to':2025}))
    assert calls==[7] and [m['movie_id'] for m in result['items']]==[1]
    assert verify_final(json.dumps(dict(answer='推荐',movie_ids=[1])),agent.observed,agent.reader,7)['items'][0]['movie_id']==1
    with pytest.raises(ValueError):agent.execute('get_recommendation_board',dict(channel='daily',user=9))
    assert 'get_recommendation_board' in [t['function']['name'] for t in schemas()]

def test_agent_external_entries_never_become_internal_ids(upstream):
    from assistant.discovery import TMDBVerifier
    class Reader:
        def recommendation_board(self,user,channel,filters,keyword,limit,scope='all'):
            return b.board([movie(1)],{},user,channel,filters=filters,trusted_filters=True,scope=scope,now=NOW)
    agent=AgentTools(Reader(),7)
    result=agent.execute('get_recommendation_board',{'channel':'popular'})
    assert None not in agent.observed and set(agent.observed)=={1}
    assert result['items'][0]['movie_id'] is None
    verified=TMDBVerifier(token='').verify('外部影片',2026)
    assert verified['verification']=='TMDB_OFFICIAL_LIST_SNAPSHOT' and verified['movie_id'] is None
    assert TMDBVerifier(token='').verify('外部影片',1990)['verification']=='UNVERIFIED_NO_TMDB_CREDENTIAL'
