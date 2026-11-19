"""Real MySQL parity and HTTP+Mock condition regression, no external provider."""
import secrets,json
from sqlalchemy import select,insert
from datetime import datetime,timedelta
from assistant.intent import parse
from assistant.agent_tools import AgentTools,verify_final

def run(app,check):
 reader=app.extensions['m7_agent'].reader;engine=app.extensions['m7_engine'];client=app.test_client();client.environ_base['REMOTE_ADDR']='198.18.'+str(secrets.randbelow(250))+'.'+str(secrets.randbelow(250)+1);origin=app.config['ALLOWED_ORIGINS'][0]
 headers={'Origin':origin,'X-CSRF-Token':client.get('/api/v1/auth/csrf').json['data']['csrf_token']};name='condition_'+secrets.token_hex(6);password=secrets.token_urlsafe(24)
 check('conditions_register_synthetic_user',client.post('/api/v1/auth/register',json={'username':name,'password':password},headers=headers).status_code==201)
 check('conditions_login',client.post('/api/v1/auth/login',json={'username':name,'password':password},headers=headers).status_code==200)
 headers['X-CSRF-Token']=client.get('/api/v1/auth/csrf').json['data']['csrf_token'];user=client.get('/api/v1/auth/me').json['data']['id'];movies,_,_=reader.snapshot(user);all_ids={m['movie_id'] for m in movies}
 check('conditions_config_mock_only',client.post('/api/v1/assistant/config',json={'api_key':'SYNTHETIC_CONDITION_NOT_A_REAL_KEY','consent':True},headers=headers).status_code==200)
 genre_a=next(g for g in movies[0]['genres'] if g not in movies[1]['genres']);genre_b=next(g for g in movies[1]['genres'] if g not in movies[0]['genres'])
 from m6_checks.m7_intent_fixtures import FIXTURES,update as intent_update,set_
 FIXTURES[genre_a+'或'+genre_b]=intent_update([set_('genres_any',[genre_a,genre_b])])
 FIXTURES['同时包含'+genre_a+'和'+genre_b]=intent_update([set_('genres_all',[genre_a,genre_b])])
 scenarios=[('请不要推荐科幻或恐怖片',{m['movie_id'] for m in movies if not any(g in m['genres'] for g in ['科幻','恐怖'])}),('我想看非恐怖的科幻电影',{m['movie_id'] for m in movies if '科幻' in m['genres'] and '恐怖' not in m['genres']}),('不想看2000年到2010年的电影',{m['movie_id'] for m in movies if not 2000<=m['release_year']<=2010}),('别推荐有恐怖元素的喜剧电影',{m['movie_id'] for m in movies if '喜剧' in m['genres'] and '恐怖' not in m['genres']}),('1990到2000年或2005到2020年的电影',all_ids),('1994年或2010年的电影',all_ids),('不想看恐怖片',all_ids),('不喜欢恐怖电影',all_ids),('不要太恐怖',all_ids),(genre_a+'或'+genre_b,all_ids),('同时包含'+genre_a+'和'+genre_b,set()),('1990到2000年的电影',{m['movie_id'] for m in movies if 1990<=m['release_year']<=2000}),('1990到2020年，不要2000到2010年',{m['movie_id'] for m in movies if 1990<=m['release_year']<=2020 and not 2000<=m['release_year']<=2010})]
 for index,(message,expected) in enumerate(scenarios):
  _,f=parse(message);rows,_=reader.search(user,f);actual={m['movie_id'] for m in rows};check('conditions_sql_'+str(index),actual==expected)
  tool=AgentTools(reader,user,conditions=f);tool_rows=tool.execute('search_movies',{'filters':{}})['items'];check('conditions_tool_sql_parity_'+str(index),{m['movie_id'] for m in tool_rows}==expected)
  final=verify_final(json.dumps({'answer':'合成模型基于真实工具返回','movie_ids':sorted(expected)}),tool.observed,reader,user,f);check('conditions_final_sql_parity_'+str(index),{m['movie_id'] for m in final['items']}==expected)
  response=client.post('/api/v1/assistant/query',json={'message':message,'request_id':secrets.token_hex(12)},headers=headers)
  check('conditions_agent_http_'+str(index),response.status_code==200 and response.json['data']['source']=='MOCK' and {m['movie_id'] for m in response.json['data']['items']}==expected)
 # A retained old erroneous positive genre is rebuilt from its recorded user intent.
 vault=app.extensions['m7_vault'];binding=vault.config(user,next(v['binding'] for u,v in vault._entries.items() if u==user))['binding'];cid=secrets.token_urlsafe(32);table=app.extensions['m7_ledger'].md.tables['sessions']
 with engine.begin() as c:c.execute(insert(table).values(id=cid,user_id=user,auth_binding=binding,state={'filters':{'genre':'恐怖'},'history':[{'role':'user','content':'不想看恐怖片'}]},turns=1,expires_at=datetime.utcnow()+timedelta(minutes=30)))
 repaired,_=app.extensions['m7_agent'].conversation(user,binding,cid);check('conditions_legacy_wrong_positive_repaired',repaired['filters']['excluded_genres']==['恐怖'] and not repaired['filters'].get('genre'))
 def query(message,cid=None):return client.post('/api/v1/assistant/query',json={'message':message,'conversation_id':cid,'request_id':secrets.token_hex(12)},headers=headers)
 first=query('1990到2000年的电影');cid=first.json['data']['conversation_id'];second=query('改成2005到2020年',cid);third=query('年份不限',cid)
 check('conditions_multiturn_mysql_override',{m['movie_id'] for m in second.json['data']['items']}=={m['movie_id'] for m in movies if 2005<=m['release_year']<=2020})
 check('conditions_multiturn_mysql_retract',{m['movie_id'] for m in third.json['data']['items']}==all_ids)
 # Verify invalid/uncertain/conflicting semantic updates via authenticated HTTP
 # and persist/reload the resulting state in real MySQL. All values are synthetic.
 from m6_checks.m7_intent_fixtures import clear
 invalid=intent_update();invalid['unexpected_sql']='SELECT secret'
 cases=[('合成结构非法',invalid),('合成语义不确定',intent_update(uncertain=True,clarification='请确认否定范围')),
        ('合成年份冲突',intent_update([set_('year_from',2020),set_('year_to',1990)])),
        ('合成类型冲突',intent_update([set_('genre','科幻'),set_('excluded_genres',['科幻'])]))]
 for message,payload in cases:
  FIXTURES[message]=payload
  response=query(message);body=response.json['data']
  check('structured_http_no_false_candidates_'+message,response.status_code==200 and not body['items'] and body.get('needs_clarification') is True)
  with engine.connect() as c:stored=c.execute(select(table.c.state).where(table.c.id==body['conversation_id'])).scalar_one()
  check('structured_mysql_clarification_persisted_'+message,stored['filters'].get('_needs_clarification') is True and stored['filters'].get('_condition_source')=='MODEL_STRUCTURED')
  check('structured_no_semantic_retry_'+message,body['cost']['provider_calls']==(1 if message=='合成结构非法' else 2))
 FIXTURES['合成单次澄清']=intent_update(uncertain=True,clarification='请确认否定范围')
 direct=query('合成单次澄清').json['data']
 check('structured_clarification_single_dispatch',direct['source']=='MOCK' and direct['cost']['provider_calls']==1 and not direct['items'])
 check('structured_direct_model_answer_used',direct['needs_clarification'] and direct['answer']=='模拟模型直接澄清，请确认否定范围')
 # Explicit multi-round corrections preserve unrelated exclusion and soft intent.
 FIXTURES['合成组合条件']=intent_update([set_('genres_any',[genre_a,genre_b]),set_('excluded_genres',['恐怖']),set_('soft_preferences',['不要太恐怖'])]+[clear('years'),set_('year_from',1990),set_('year_to',2020)])
 FIXTURES['合成仅改上限']=intent_update([set_('year_to',2000)])
 FIXTURES['合成撤销年份']=intent_update([clear('years')])
 first=query('合成组合条件');cid=first.json['data']['conversation_id'];second=query('合成仅改上限',cid);third=query('合成撤销年份',cid)
 with engine.connect() as c:stored=c.execute(select(table.c.state).where(table.c.id==cid)).scalar_one()['filters']
 check('structured_partial_bound_and_other_filters',second.json['data']['conditions']['year_from']==1990 and second.json['data']['conditions']['year_to']==2000 and second.json['data']['conditions']['excluded_genres']==['恐怖'])
 check('structured_revoke_preserves_multigenre_soft',not any(k in stored for k in ('year_from','year_to')) and stored['genres_any']==[genre_a,genre_b] and bool(stored['_soft_preferences']))
 # A model may inspect an excluded film but cannot recommend it as a selected ID.
 from m6_checks.m7_intent_fixtures import ACTION_FIXTURES
 FIXTURES['合成最终候选冲突']=intent_update([set_('year_to',movies[0]['release_year']-1)])
 ACTION_FIXTURES['合成最终候选冲突']=[{'name':'get_movie_detail','arguments':{'movie_id':movies[0]['movie_id']}}]
 conflict=query('合成最终候选冲突').json['data']
 check('structured_real_mysql_final_mismatch_rejected',conflict['source']=='FALLBACK' and conflict.get('failure_code')=='MODEL_CONDITION_REJECTED' and movies[0]['movie_id'] not in [m['movie_id'] for m in conflict['items']])
 check('structured_final_mismatch_no_retries',conflict['cost']['provider_calls']==2)
 client.post('/api/v1/assistant/config',json={'api_key':'SYNTHETIC_CONDITION_NOT_A_REAL_KEY','consent':True,'cache_enabled':True},headers=headers)
 first=query('我想看非恐怖的科幻电影').json['data'];cached=query('我想看非恐怖的科幻电影').json['data']
 check('structured_cached_conditions_match',cached['source']=='CACHE' and cached['conditions']==first['conditions'] and cached['cost']['provider_calls']==0)
 check('structured_cached_private_state_not_exposed','_condition_state' not in cached)
 with engine.connect() as c:cached_filters=c.execute(select(table.c.state).where(table.c.id==cached['conversation_id'])).scalar_one()['filters']
 check('structured_cached_state_persisted',cached_filters.get('_condition_source')=='MODEL_STRUCTURED' and cached_filters['genre']=='科幻' and cached_filters['excluded_genres']==['恐怖'])
 # Structured terminal output exercises the real HTTP / MySQL persistence path.
 FIXTURES['合成终结工具']=intent_update([set_('excluded_genres',['恐怖'])])
 terminal=query('合成终结工具').json['data']
 check('terminal_http_actual_model_text',terminal['source']=='MOCK' and terminal.get('completion_protocol')=='TERMINAL_TOOL' and terminal['answer']=='模拟模型正确使用实际查询条件')
 check('terminal_no_extra_dispatch',terminal['cost']['provider_calls']==2)
 with engine.connect() as c:terminal_state=c.execute(select(table.c.state).where(table.c.id==terminal['conversation_id'])).scalar_one()
 check('terminal_real_mysql_state',terminal_state['filters']['excluded_genres']==['恐怖'] and terminal_state['history'][-1]['content']==terminal['answer'])
 FIXTURES['合成终结工具冲突']=intent_update([set_('year_to',movies[0]['release_year']-1)])
 ACTION_FIXTURES['合成终结工具冲突']=[{'name':'get_movie_detail','arguments':{'movie_id':movies[0]['movie_id']}}]
 mismatch=query('合成终结工具冲突').json['data']
 check('terminal_final_conditions_not_bypassed',mismatch['source']=='FALLBACK' and mismatch['failure_code']=='MODEL_CONDITION_REJECTED')
 check('terminal_mismatch_no_retry',mismatch['cost']['provider_calls']==2)
 FIXTURES['合成终结工具非法ID']=intent_update()
 invalid_terminal=query('合成终结工具非法ID').json['data']
 check('terminal_invalid_id_schema_denied',invalid_terminal['source']=='FALLBACK' and invalid_terminal['failure_details']['stage']=='FINAL_SCHEMA')
 check('terminal_invalid_id_no_retry',invalid_terminal['cost']['provider_calls']==2)
 # No-Key path actually exercises the four reported utterances through HTTP.
 client.post('/api/v1/assistant/disconnect',json={},headers=headers)
 for message,expected in scenarios[:4]:
  response=query(message);body=response.json['data']
  check('local_http_counterexample_'+message,response.status_code==200 and body['source']=='LOCAL' and {m['movie_id'] for m in body['items']}==expected)
