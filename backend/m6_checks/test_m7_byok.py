import unittest,json,time
from unittest.mock import patch
from assistant.byok_vault import Vault
from assistant.intent import parse
from assistant.api_v2 import preview
from assistant.agent_tools import verify_final
class BYOKUnitTests(unittest.TestCase):
 def test_no_plaintext_key_and_user_binding(self):
  v=Vault();body={'api_key':'SYNTHETIC_SECRET_123456789','consent':True};status=v.put(1,'session-a',body)
  self.assertNotIn('SYNTHETIC_SECRET',repr(v._entries));self.assertNotIn('api_key',status);self.assertIsNone(v.config(2,'session-a'));self.assertIsNone(v.config(1,'session-b'))
  generation=v.config(1,'session-a')['generation'];self.assertEqual(v.key(1,'session-a',generation),body['api_key']);v.disconnect(1,'session-a')
  with self.assertRaises(ValueError):v.key(1,'session-a',generation)
 def test_expiry_and_logout_binding(self):
  v=Vault(ttl=.01);v.put(1,'a',{'api_key':'SYNTHETIC_SECRET_123456789','consent':True});time.sleep(.02);self.assertFalse(v.status(1,'a')['configured'])
  v.put(1,'a',{'api_key':'SYNTHETIC_SECRET_123456789','consent':True});v.disconnect_binding('a');self.assertFalse(v.status(1,'a')['configured'])
 def test_arbitrary_url_and_key_consent_denied(self):
  v=Vault()
  for body in [{'api_key':'SYNTHETIC_SECRET_123456789'},{'api_key':'SYNTHETIC_SECRET_123456789','consent':True,'base_url':'http://127.0.0.1'},{'api_key':'x\nAuthorization: y','consent':True},{'api_key':'SYNTHETIC_SECRET_123456789','consent':True,'daily_calls':True}]:
   with self.assertRaises(ValueError):v.put(1,'a',body)
 def test_model_selection(self):
  v=Vault();self.assertEqual(v.put(1,'a',{'api_key':'SYNTHETIC_SECRET_123456789','consent':True,'model':'deepseek-v4-pro'})['model'],'deepseek-v4-pro')
 def test_condition_override(self):
  _,f=parse('2010年之后的科幻片');_,f=parse('改成2000年以前的喜剧',f)
  self.assertNotIn('year_from',f);self.assertEqual(f['year_to'],1999);self.assertEqual(f['genre'],'喜剧')
  _,f=parse('不要喜剧',f);self.assertNotIn('genre',f);_,f=parse('还是喜剧',f);self.assertNotIn('喜剧',f['excluded_genres'])
  _,f=parse('不限年份，不限类型，不限时长',f);self.assertNotIn('year_to',f);self.assertNotIn('genre',f)
 def test_preview_json_escape(self):
  self.assertEqual(preview('{"answer":"你好，电'),'你好，电');self.assertEqual(preview('{"answer":"第一行\\n第二行","movie_ids":[]}'),'第一行\n第二行')
 def test_agent_tools_privilege_boundary(self):
  from assistant.agent_tools import AgentTools
  registry=AgentTools(None,1)
  for name,args in [('run_sql',{'sql':'SELECT * FROM auth_users'}),('get_my_watchlist',{'user_id':2}),('get_movie_detail',{'movie_id':True}),('search_movies',{'filters':{'sql':'DROP'}}),('compare_movies',{'movie_ids':[1,True]})]:
   with self.assertRaises(ValueError):registry.execute(name,args)
 def test_v2_http_errors_and_no_retry(self):
  from urllib.error import HTTPError
  from assistant.agent_provider import DeepSeekAgentProvider,ProviderError
  for status,code in [(401,'KEY_REJECTED'),(429,'PROVIDER_RATE_LIMITED'),(500,'LLM_UNAVAILABLE')]:
   with self.subTest(status=status),patch('urllib.request.build_opener') as opener:
    opener.return_value.open.side_effect=HTTPError('https://api.deepseek.com/chat/completions',status,'synthetic',{},None)
    with self.assertRaises(ProviderError) as error:DeepSeekAgentProvider('SYNTHETIC_SECRET_123456789').complete([],[],lambda:False,lambda text:None,time.monotonic()+10)
    self.assertEqual(error.exception.code,code);self.assertEqual(opener.return_value.open.call_count,1)
 def test_v2_invalid_stream_falls_back(self):
  import io
  from assistant.agent_provider import DeepSeekAgentProvider,ProviderError
  for raw in [b'',b'data: invalid-json\n',b'data: {"choices":[{"delta":{},"finish_reason":"length"}]}\n\ndata: [DONE]\n']:
   with patch('urllib.request.build_opener') as opener:
    opener.return_value.open.return_value=io.BytesIO(raw)
    with self.assertRaises(ProviderError):DeepSeekAgentProvider('SYNTHETIC_SECRET_123456789').complete([],[],lambda:False,lambda text:None,time.monotonic()+10)
 def test_tmdb_match_stays_outside_catalog(self):
  import io
  from assistant.discovery import TMDBVerifier
  with patch('urllib.request.build_opener') as opener:
   opener.return_value.open.return_value=io.BytesIO(json.dumps({'results':[{'id':123,'title':'合成电影','original_title':'Synthetic','release_date':'2020-01-01','adult':False}]}).encode())
   result=TMDBVerifier('SYNTHETIC_METADATA_TOKEN').verify('合成电影',2020);self.assertEqual(result['tmdb_id'],123);self.assertFalse(result['in_catalog']);self.assertIsNone(result['movie_id'])
 def test_provider_stream_tool_fragments_and_usage(self):
  import io
  from assistant.agent_provider import DeepSeekAgentProvider
  chunks=[{'choices':[{'delta':{'tool_calls':[{'index':0,'id':'call_one','function':{'name':'search_movies','arguments':'{"filters":'}}]}}]}, {'choices':[{'delta':{'tool_calls':[{'index':0,'function':{'arguments':'{}}'}}]},'finish_reason':'tool_calls'}],'usage':{'prompt_tokens':120,'completion_tokens':12,'prompt_cache_hit_tokens':20}}]
  raw=b''.join(b'data: '+json.dumps(c).encode()+b'\n\n' for c in chunks)+b'data: [DONE]\n\n'
  with patch('urllib.request.build_opener') as opener:
   opener.return_value.open.return_value=io.BytesIO(raw);p=DeepSeekAgentProvider('SYNTHETIC_SECRET_123456789');result=p.complete([],[],lambda:False,lambda text:None,time.monotonic()+10)
   self.assertEqual(json.loads(result['tool_calls'][0]['function']['arguments']),{'filters':{}});self.assertEqual(result['usage']['cached_input_tokens'],20)
   request=opener.return_value.open.call_args.args[0];self.assertEqual(request.full_url,'https://api.deepseek.com/chat/completions');self.assertTrue(json.loads(request.data)['stream'])
 def test_optional_tmdb_no_secret_no_network(self):
  from assistant.discovery import TMDBVerifier
  with patch('urllib.request.build_opener') as opener:
   result=TMDBVerifier('').verify('合成电影',2020);self.assertFalse(result['in_catalog']);self.assertIsNone(result['movie_id']);opener.assert_not_called()
 def test_final_conditions_enforced_after_model(self):
  from types import SimpleNamespace
  class Reader:
   behavior=SimpleNamespace(snapshot=lambda user:{'1':{'watched':True}})
   def get_movies(self,ids):return [{'movie_id':1,'title':'真实片','genres':['科幻'],'release_year':2010,'runtime_minutes':148}]
  output='{"answer":"模型可能忽略条件","movie_ids":[1]}'
  for filters in [{'year_to':1999},{'runtime_max':120},{'unwatched':True},{'excluded_genres':['科幻']}]:
   with self.subTest(filters=filters),self.assertRaises(ValueError):verify_final(output,{1:{}},Reader(),1,filters)
  self.assertTrue(parse('不要已看过的')[1]['unwatched'])
 def test_full_v2_input_conditions_not_silently_truncated(self):
  message='普通前文'*300+'，现在只看2000年以前的喜剧，不要已看过的'
  _,filters=parse(message,max_length=2000)
  self.assertEqual(filters['year_to'],1999);self.assertTrue(filters['unwatched']);self.assertEqual(filters['genre'],'喜剧')
 def test_final_is_actual_model_text(self):
  class Reader:
   def snapshot(self,user):return [{'movie_id':1,'title':'真实库标题','genres':[]}],{},False
  result=verify_final(json.dumps({'answer':'实际模型措辞，而非模板。','movie_ids':[1],'discoveries':[{'title':'库外建议','year':2020,'reason':'模型知识，未验证'}]},ensure_ascii=False),{1:{}},Reader(),1)
  self.assertEqual(result['answer'],'实际模型措辞，而非模板。');self.assertIsNone(result['discoveries'][0]['movie_id']);self.assertFalse(result['discoveries'][0]['in_catalog'])
  with self.assertRaises(ValueError):verify_final('{"answer":"幻觉","movie_ids":[999]}',{1:{}},Reader(),1)
if __name__=='__main__':unittest.main()
