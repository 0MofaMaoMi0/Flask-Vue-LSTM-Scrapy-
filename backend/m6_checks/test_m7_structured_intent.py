"""Independent semantic fixtures + real Agent orchestration, no paid network."""
import json,time,unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch
from assistant.structured_intent import apply_update,model_base,validate_json,INTENT_SCHEMA,plan_schema,load_json
from assistant.conditions import matches,hard_conditions
from assistant.agent_tools import AgentTools,verify_final,schemas
from assistant.agent_provider import ScriptedAgentProvider
from assistant.agent_service import AgentService
from assistant.intent import parse
from m6_checks.test_m7_conditions import MOVIES,ids
from m6_checks.m7_intent_fixtures import fixture,update,set_,clear,years

class Reader:
    behavior=SimpleNamespace(snapshot=lambda user:{})
    def get_movies(self,values):return [deepcopy(m) for m in MOVIES if m['movie_id'] in values]
    def snapshot(self,user):return deepcopy(MOVIES),{},False
    def search(self,user,filters,keyword='',limit=10):return [deepcopy(m) for m in MOVIES if matches(m,filters,{})][:limit],False
    def resolve(self,user,intent,filters,exclude=()):return [m for m in MOVIES if matches(m,filters,{}) and m['movie_id'] not in exclude],{},False,deepcopy(MOVIES)

def call(payload,actions=None):
    return {'content':'','tool_calls':[{'id':'plan_fixture','type':'function','function':{'name':'plan_movie_turn','arguments':json.dumps({'intent_update':payload,'actions':actions if actions is not None else [{'name':'search_movies','arguments':{'filters':{}}}]},ensure_ascii=False)}}]}

class Ledger:
    def __init__(self):self.dispatches=0;self.records=[];self.result=None
    def dispatch(self,*args):self.dispatches+=1;return str(self.dispatches)
    def record(self,*args):self.records.append(args)
    def read(self,key):return {}
    def cost(self,key):return {'provider_calls':self.dispatches}
    def finish(self,key,result,state):self.result=result

def agent(steps,old=None,message='test'):
    provider=ScriptedAgentProvider(steps);ledger=Ledger();config={'generation':1,'model':'mock-agent','enabled':True,'verification':'VERIFIED','daily_calls':None,'max_output_tokens':2048,'cache_enabled':False}
    vault=SimpleNamespace(key=lambda *a:'SYNTHETIC_ONLY',config=lambda *a:config,verified=lambda *a:None)
    service=AgentService(None,None,Reader(),vault,ledger,lambda *a:provider)
    service.conversation=lambda *a:(old or {'filters':{},'history':[],'shown':[]},0)
    saved={}
    def finish(key,user,binding,cid,previous,message,result,filters,turns):saved.update(result=result,filters=filters)
    service.finish_turn=finish
    import threading
    service.run('key',1,'binding',message,None,'query',None,{'draft':'','cancel':threading.Event()})
    return saved,ledger,provider

class StructuredIntentRegression(unittest.TestCase):
    def test_four_independent_counterexamples_in_local_and_model_paths(self):
        expected=[{1,5},{2,3},{1,4,5},{1,3}]
        messages=['请不要推荐科幻或恐怖片','我想看非恐怖的科幻电影','不想看2000年到2010年的电影','别推荐有恐怖元素的喜剧电影']
        for message,wanted in zip(messages,expected):
            with self.subTest(message=message):
                self.assertEqual(ids(parse(message)[1]),wanted)
                _,state=apply_update({},fixture(message));self.assertEqual(ids(state),wanted)
                tool=AgentTools(Reader(),1,conditions=state);rows=tool.execute('search_movies',{'filters':{}})['items']
                self.assertEqual({m['movie_id'] for m in rows},wanted)
                final=verify_final(json.dumps({'answer':'合成模型回答','movie_ids':sorted(wanted)}),tool.observed,Reader(),1,state)
                self.assertEqual({m['movie_id'] for m in final['items']},wanted)

    def test_byok_uses_model_scope_without_rule_veto_and_two_dispatches(self):
        old={'filters':{'genre':'恐怖','year_from':2000,'_condition_version':4},'history':[]}
        with patch('assistant.agent_service.parse',side_effect=AssertionError('BYOK must not parse with rules')):
            saved,ledger,provider=agent([call(fixture('我想看非恐怖的科幻电影')),{'content':json.dumps({'answer':'模型实际生成内容','movie_ids':[2,3]})}],old,'我想看非恐怖的科幻电影')
        self.assertEqual(saved['result']['source'],'MOCK');self.assertEqual(saved['result']['answer'],'模型实际生成内容')
        self.assertEqual(saved['filters']['genre'],'科幻');self.assertNotIn('year_from',saved['filters'])
        self.assertEqual(ledger.dispatches,2);self.assertEqual(len(ledger.records),2)

    def test_structured_schema_rejects_coercion_unknown_fields_ranges_and_sql(self):
        bad=[]
        for field,value in [('year_from','2000'),('year_to',True),('year_to',2200),('genre',{}),('unwatched',1),('years_any',[2000,2000]),('runtime_max',0),('excluded_year_ranges',[[1990,2000,2010]])]:
            bad.append(update([set_(field,value)]))
        value=update();value['sql']='DROP DATABASE';bad.append(value)
        value=update();value['version']=True;bad.append(value)
        value=update();value['changes']=[{'op':'set','field':'sql','value':'SELECT secret'}];bad.append(value)
        value=update();value.pop('uncertain');bad.append(value)
        for value in bad:
            with self.subTest(value=value),self.assertRaises(ValueError):apply_update({},value)

    def test_plan_schema_rejects_uncontrolled_tools_before_any_execution(self):
        plan={'intent_update':update(),'actions':[{'name':'run_sql','arguments':{'sql':'SELECT 1'}}]}
        with self.assertRaises(ValueError):validate_json(plan,plan_schema(schemas()))
        steps=[call(update(),plan['actions'])]
        with patch.object(Reader,'search',side_effect=AssertionError('no catalog tool allowed')):
            saved,ledger,_=agent(steps)
        self.assertEqual(saved['result']['source'],'FALLBACK');self.assertEqual(saved['result']['items'],[])
        self.assertEqual(ledger.dispatches,1);self.assertTrue(saved['filters']['_needs_clarification'])

    def test_missing_or_invalid_plan_never_silently_retries(self):
        for response in [{'content':'{"answer":"没有结构化意图","movie_ids":[]}'},call({'version':999})]:
            saved,ledger,_=agent([response])
            self.assertEqual(saved['result']['source'],'FALLBACK');self.assertEqual(saved['result']['items'],[]);self.assertEqual(ledger.dispatches,1)

    def test_combinations_positive_negative_and_and_or(self):
        _,f=apply_update({},update([set_('genres_any',['科幻','喜剧']),set_('excluded_genres',['恐怖'])]))
        self.assertEqual(ids(f),{1,2,3})
        _,f=apply_update(f,update([set_('genres_all',['科幻','喜剧'])]));self.assertEqual(ids(f),{3})
        _,f=apply_update(f,update([{'op':'remove','field':'genres_all','value':['喜剧']}]))
        self.assertEqual(ids(f),{2,3});self.assertEqual(f['excluded_genres'],['恐怖'])

    def test_multiturn_range_replacement_partial_override_revoke_and_soft(self):
        _,f=apply_update({},update(years(1990,2000)+[set_('excluded_genres',['恐怖']),set_('soft_preferences',['不要太恐怖'])]))
        previous=deepcopy(f)
        _,f=apply_update(f,update([set_('year_to',2010)]));self.assertEqual(f['year_from'],1990);self.assertEqual(f['year_to'],2010)
        _,f=apply_update(f,update(years(2000,2020)));self.assertEqual(ids(f),{2,3,5})
        _,f=apply_update(f,update([clear('years'),{'op':'remove','field':'excluded_genres','value':['恐怖']}]))
        self.assertEqual(ids(f),{1,2,3,4,5});self.assertTrue(f['_soft_preferences']);self.assertEqual(previous['year_to'],2000)
        _,f=apply_update(f,update([clear('all')]));self.assertEqual(hard_conditions(f),{});self.assertFalse(f['_soft_preferences'])

    def test_year_alternatives_exclusions_and_boundary(self):
        _,single=apply_update({},update([set_('excluded_year_ranges',[[2000,2000]])]))
        self.assertEqual(ids(single),{1,3,4,5})
        _,single=apply_update({},update([set_('year_ranges_any',[[2000,2000]])]))
        self.assertEqual(ids(single),{2})
        _,f=apply_update({},update([set_('year_ranges_any',[[1990,1995],[2010,2020]]),set_('excluded_year_ranges',[[1995,2010]])]))
        self.assertEqual(ids(f),{1,5})
        _,f=apply_update(f,update([clear('years'),set_('years_any',[1990,2000,2010]),set_('excluded_year_ranges',[[2000,2010]])]))
        self.assertEqual(ids(f),{1})

    def test_unknown_meaning_and_conflicts_require_clarification(self):
        conflicting=[update([set_('year_from',2010),set_('year_to',2000)]),update([set_('genre','恐怖'),set_('excluded_genres',['恐怖'])]),update([set_('year_ranges_any',[[2010,2000]])])]
        for payload in conflicting+[update([set_('genre','恐怖')],uncertain=True,clarification='你是排除恐怖还是只是不想太恐怖？')]:
            _,f=apply_update({},payload);self.assertTrue(f['_needs_clarification'])
            with self.assertRaises(ValueError):verify_final('{"answer":"错误候选","movie_ids":[4]}',{4:{}},Reader(),1,f)
        _,f=apply_update({},conflicting[0]);_,f=apply_update(f,update(years(1990,2000)))
        self.assertFalse(f.get('_needs_clarification'));self.assertEqual(ids(f),{1,2,4})

    def test_uncertain_model_does_not_execute_tools_or_manufacture_hard_constraints(self):
        first=call(update([set_('genre','恐怖')],uncertain=True,clarification='请确认否定范围'))
        with patch.object(Reader,'search',side_effect=AssertionError('must clarify before search')):
            saved,ledger,_=agent([first,{'content':'{"answer":"请确认否定范围","movie_ids":[]}'}])
        self.assertEqual(saved['result']['items'],[]);self.assertEqual(saved['result']['conditions'],{})
        self.assertEqual(ledger.dispatches,2)

    def test_model_query_and_final_cannot_weaken_accepted_conditions(self):
        _,f=apply_update({},fixture('我想看非恐怖的科幻电影'))
        tools=AgentTools(Reader(),1,conditions=f)
        with self.assertRaises(ValueError):tools.execute('search_movies',{'filters':{'genre':'恐怖'}})
        tools.execute('get_movie_detail',{'movie_id':4})
        with self.assertRaises(ValueError):verify_final('{"answer":"不合要求","movie_ids":[4]}',tools.observed,Reader(),1,f)
        saved,ledger,_=agent([call(fixture('我想看非恐怖的科幻电影'),[{'name':'get_movie_detail','arguments':{'movie_id':4}}]),{'content':'{"answer":"试图返回恐怖片","movie_ids":[4]}'}])
        self.assertEqual(saved['result']['source'],'FALLBACK');self.assertEqual(saved['result']['failure_code'],'MODEL_CONDITION_REJECTED')
        self.assertEqual({m['movie_id'] for m in saved['result']['items']},{2,3})

    def test_provenance_prevents_rules_from_becoming_model_authority(self):
        _,rule=parse('不想看恐怖片',{'_semantic_version':1,'_condition_source':'MODEL_STRUCTURED'})
        self.assertEqual(rule['_condition_source'],'LOCAL_RULE');self.assertNotIn('excluded_genres',model_base(rule))
        _,f=apply_update({},update([set_('genre','科幻')]))
        self.assertEqual(model_base(f)['genre'],'科幻')

    def test_duplicate_json_fields_and_non_json_numbers_are_rejected(self):
        for text in ['{"uncertain":true,"uncertain":false}','{"value":NaN}','{"value":Infinity}']:
            with self.subTest(text=text),self.assertRaises(ValueError):load_json(text)

    def test_combined_negative_scope_and_complete_year_conflict(self):
        for message in ['我想看喜剧，不要科幻或恐怖','我不想看科幻或者恐怖，但想看喜剧']:
            self.assertEqual(ids(parse(message)[1]),{1})
        _,f=apply_update({},update([set_('genres_any',['喜剧','科幻']),set_('excluded_genres',['恐怖','科幻'])]))
        self.assertEqual(ids(f),{1})
        for changes in [years(2000,2010)+[set_('excluded_year_ranges',[[1990,2020]])],
                        years(1990,2000)+[set_('year_ranges_any',[[2010,2020]])]]:
            _,f=apply_update({},update(changes));self.assertTrue(f['_needs_clarification'])

    def test_provider_requests_the_plan_on_the_existing_first_dispatch(self):
        import io
        from assistant.agent_provider import DeepSeekAgentProvider
        response=b'data: {"choices":[{"delta":{"content":"{}"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n'
        tools=[{'type':'function','function':{'name':'plan_movie_turn','parameters':plan_schema(schemas())}}]
        with patch('urllib.request.build_opener') as opener:
            opener.return_value.open.return_value=io.BytesIO(response)
            DeepSeekAgentProvider('SYNTHETIC_ONLY').complete([],tools,lambda:False,lambda chunk:None,time.monotonic()+5)
            payload=json.loads(opener.return_value.open.call_args.args[0].data)
        self.assertEqual(payload['tool_choice']['function']['name'],'plan_movie_turn')
        self.assertEqual(len(payload['tools']),1)

    def test_tool_free_model_answer_or_clarification_needs_only_one_dispatch(self):
        for payload in [update(),update(uncertain=True,clarification='请确认否定范围')]:
            response=call(payload,[])
            args=json.loads(response['tool_calls'][0]['function']['arguments']);args['answer']='模型实际生成的澄清或闲聊'
            response['tool_calls'][0]['function']['arguments']=json.dumps(args)
            saved,ledger,_=agent([response])
            self.assertEqual(saved['result']['source'],'MOCK');self.assertEqual(saved['result']['answer'],args['answer'])
            self.assertEqual(saved['result']['items'],[]);self.assertEqual(ledger.dispatches,1)

    def test_failure_diagnostics_do_not_copy_text_keys_or_arguments(self):
        from assistant.diagnostics import failure_details
        secret='SYNTHETIC_PRIVATE_NOT_FOR_DIAGNOSTICS'
        value=failure_details('FINAL_VERIFY',ValueError(secret),{'content':json.dumps({'answer':secret,secret:secret,'movie_ids':[1]})})
        self.assertNotIn(secret,json.dumps(value));self.assertEqual(value['extra_field_count'],1)
        self.assertEqual(value['field_types']['answer'],'str')

    def test_agent_diagnostic_distinguishes_final_and_tool_argument_failures(self):
        bad_call={'tool_calls':[{'id':'bad','type':'function','function':{'name':'search_movies','arguments':'INVALID_SYNTHETIC_JSON'}}]}
        for response,stage,reason in [({'content':'NOT_JSON'},'FINAL_VERIFY','FINAL_JSON_INVALID'),(bad_call,'TOOL_ARGUMENTS','INVALID_JSON')]:
            saved,ledger,_=agent([call(update()),response])
            self.assertEqual(saved['result']['failure_details']['stage'],stage)
            self.assertEqual(saved['result']['failure_details']['reason'],reason)
            self.assertEqual(ledger.dispatches,2)

    def test_terminal_tool_uses_model_answer_without_an_extra_dispatch(self):
        final={'answer':'终结工具中的模型原文','movie_ids':[2],'discoveries':[]}
        response={'tool_calls':[{'id':'finish_one','type':'function','function':{'name':'finish_movie_turn','arguments':json.dumps(final)}}]}
        saved,ledger,_=agent([call(fixture('我想看非恐怖的科幻电影')),response])
        self.assertEqual(saved['result']['source'],'MOCK');self.assertEqual(saved['result']['answer'],final['answer'])
        self.assertEqual([m['movie_id'] for m in saved['result']['items']],[2]);self.assertEqual(ledger.dispatches,2)

    def test_terminal_tool_cannot_bypass_intent_evidence_or_conditions(self):
        def final(ids):return {'tool_calls':[{'id':'end','type':'function','function':{'name':'finish_movie_turn','arguments':json.dumps({'answer':'合成模型回答','movie_ids':ids,'discoveries':[]})}}]}
        saved,ledger,_=agent([final([])])
        self.assertEqual(saved['result']['source'],'FALLBACK');self.assertEqual(ledger.dispatches,1)
        for mid in [4,999]:
            saved,ledger,_=agent([call(fixture('我想看非恐怖的科幻电影'),[{'name':'get_movie_detail','arguments':{'movie_id':4}}]),final([mid])])
            self.assertEqual(saved['result']['source'],'FALLBACK');self.assertNotIn(mid,[m['movie_id'] for m in saved['result']['items']]);self.assertEqual(ledger.dispatches,2)

    def test_legacy_final_duplicate_fields_bad_id_shapes_and_constants_rejected(self):
        values=['{"answer":"a","answer":"b","movie_ids":[]}',
                '{"answer":"a","movie_ids":[{"movie_id":2}]}',
                '{"answer":"a","movie_ids":[[2]]}',
                '{"answer":"a","movie_ids":["2"]}',
                '{"answer":"a","movie_ids":[true]}',
                '{"answer":"a","movie_ids":[NaN]}']
        for content in values:
            with self.subTest(content=content),self.assertRaises(ValueError):verify_final(content,{2:{}},Reader(),1)

    def test_terminal_schema_does_not_coerce_invalid_ids(self):
        for bad in [['2'],[True],[{'movie_id':2}],[[2]]]:
            response={'tool_calls':[{'id':'end','type':'function','function':{'name':'finish_movie_turn','arguments':json.dumps({'answer':'合成','movie_ids':bad,'discoveries':[]})}}]}
            saved,ledger,_=agent([call(update()),response])
            self.assertEqual(saved['result']['source'],'FALLBACK');self.assertEqual(saved['result']['failure_details']['stage'],'FINAL_SCHEMA');self.assertEqual(ledger.dispatches,2)

    def test_malformed_tool_batches_are_rejected_before_any_tool_executes(self):
        valid={'id':'same','type':'function','function':{'name':'search_movies','arguments':'{}'}}
        finish={'id':'end','type':'function','function':{'name':'finish_movie_turn','arguments':'{}'}}
        for calls in [[valid,deepcopy(valid)],[valid,finish],[{'id':'bad','type':'function','function':[]}]]:
            with patch.object(Reader,'search',side_effect=AssertionError('Batch must be rejected before search')):
                saved,ledger,_=agent([call(update(),[]),{'tool_calls':calls}])
            self.assertEqual(saved['result']['source'],'FALLBACK');self.assertEqual(saved['result']['failure_details']['stage'],'TOOL_ENVELOPE');self.assertEqual(ledger.dispatches,2)

if __name__=='__main__':unittest.main()
