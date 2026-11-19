"""Synthetic semantic cases with expected movie sets, not implementation snapshots."""
import json,unittest
from types import SimpleNamespace
from assistant.intent import parse
from assistant.conditions import matches,merge_for_tool
from assistant.agent_tools import AgentTools,verify_final
MOVIES=[{'movie_id':1,'title':'合成喜剧','genres':['喜剧'],'release_year':1990,'runtime_minutes':100}, {'movie_id':2,'title':'合成科幻','genres':['科幻'],'release_year':2000,'runtime_minutes':130}, {'movie_id':3,'title':'合成双类型','genres':['科幻','喜剧'],'release_year':2010,'runtime_minutes':110}, {'movie_id':4,'title':'合成恐怖喜剧','genres':['恐怖','喜剧'],'release_year':1995,'runtime_minutes':90}, {'movie_id':5,'title':'合成剧情','genres':['剧情'],'release_year':2020,'runtime_minutes':140}]
def ids(f):return {m['movie_id'] for m in MOVIES if matches(m,f,{})}
class ConditionRegression(unittest.TestCase):
 def test_negative_variants(self):
  for message in ['不想看恐怖片','不喜欢恐怖电影','不爱看恐怖片','不要恐怖片','恐怖片不看','我不接受恐怖电影']:
   with self.subTest(message=message):
    _,f=parse(message);self.assertEqual(ids(f),{1,2,3,5});self.assertNotIn('genre',f)
 def test_intensity_is_not_a_positive_genre_or_fake_hard_fact(self):
  _,f=parse('不要太恐怖');self.assertEqual(ids(f),{1,2,3,4,5});self.assertTrue(f['_soft_preferences']['恐怖']);self.assertTrue(f['_notes'])
  _,continued=parse('换一批',f);self.assertEqual(continued['_soft_preferences'],f['_soft_preferences'])
 def test_background_and_permission_are_not_mandatory_genres(self):
  for text in ['不排斥悬疑','我昨天看过恐怖电影','恐怖电影的名字很有趣']:
   with self.subTest(text=text):self.assertNotIn('genre',parse(text)[1])
 def test_or_and_and(self):
  self.assertEqual(ids(parse('科幻或喜剧')[1]),{1,2,3,4})
  self.assertEqual(ids(parse('同时包含科幻和喜剧')[1]),{3})
  self.assertEqual(ids(parse('科幻和喜剧都不要')[1]),{5})
 def test_mixed_positive_negative(self):
  self.assertEqual(ids(parse('喜欢科幻和喜剧，但不喜欢恐怖')[1]),{1,2,3})
  self.assertEqual(ids(parse('不是恐怖，而是科幻')[1]),{2,3})
  self.assertEqual(ids(parse('1990到2000年的科幻或喜剧，不想看恐怖')[1]),{1,2})
 def test_type_correction_and_undo(self):
  _,f=parse('科幻或喜剧，不要恐怖');_,f=parse('改成剧情',f);self.assertEqual(ids(f),{5})
  _,f=parse('取消类型限制',f);self.assertEqual(ids(f),{1,2,3,4,5})
  _,f=parse('不想看恐怖');_,f=parse('撤销对恐怖的排除',f);self.assertEqual(ids(f),{1,2,3,4,5})
 def test_adding_a_type_does_not_drop_earlier_types(self):
  _,f=parse('科幻');_,f=parse('再加喜剧',f);self.assertEqual(ids(f),{1,2,3,4})
 def test_inclusive_ranges_and_boundaries(self):
  for message in ['1990到2000年的电影','1990—2000年','1990年～2000年','不早于1990年，不晚于2000年']:
   with self.subTest(message=message):self.assertEqual(ids(parse(message)[1]),{1,2,4})
  self.assertEqual(ids(parse('1990年之后，2000年之前')[1]),{4})
 def test_positive_and_excluded_year_ranges(self):
  self.assertEqual(ids(parse('1990到2020年，不要2000到2010年的电影')[1]),{1,4,5})
  self.assertEqual(ids(parse('1990到1995年或2010到2020年的电影')[1]),{1,3,4,5})
  _,f=parse('不要1990到2000年');_,f=parse('撤销1990到2000年的排除',f);self.assertEqual(ids(f),{1,2,3,4,5})
 def test_partial_bound_override(self):
  _,f=parse('1990到2020年');_,f=parse('上限改成2000年',f);self.assertEqual(f['year_from'],1990);self.assertEqual(f['year_to'],2000)
 def test_approximate_year_does_not_veto_model(self):
  _,f=parse('改成2000年前后的电影');self.assertNotIn('year_from',f);self.assertNotIn('year_to',f);self.assertFalse(f.get('_needs_clarification'));self.assertTrue(f['_soft_preferences']['years'])
 def test_range_override_and_partial_retraction(self):
  _,f=parse('1990到2000年');_,f=parse('改成2005到2020年',f);self.assertEqual(ids(f),{3,5})
  _,f=parse('取消上限',f);self.assertEqual(f['year_from'],2005);self.assertNotIn('year_to',f)
  _,f=parse('年份不限',f);self.assertEqual(ids(f),{1,2,3,4,5})
 def test_year_negation_and_discrete_years(self):
  self.assertEqual(ids(parse('不要2000年以前的电影')[1]),{2,3,5})
  self.assertEqual(ids(parse('不要1990到2000年的电影')[1]),{3,5})
  self.assertEqual(ids(parse('1990年或2000年的电影')[1]),{1,2})
 def test_explicit_conflict_not_silently_repaired(self):
  _,f=parse('2010年之后');_,f=parse('再加一个条件，2000年之前',f);self.assertTrue(f['_needs_clarification']);self.assertEqual(f['year_from'],2011);self.assertEqual(f['year_to'],1999)
  self.assertTrue(parse('2020到1990年的电影')[1]['_needs_clarification'])
 def test_movie_title_is_not_a_genre_filter(self):
  self.assertEqual(ids(parse('不喜欢《恐怖游轮》')[1]),{1,2,3,4,5})
  self.assertNotIn('year_from',parse('介绍《2012》')[1])
 def test_shared_query_and_final_semantics(self):
  class Reader:
   behavior=SimpleNamespace(snapshot=lambda user:{})
   def get_movies(self,wanted):return [m for m in MOVIES if m['movie_id'] in wanted]
  observed={m['movie_id']:m for m in MOVIES}
  for message in ['不想看恐怖电影','科幻或喜剧，不要恐怖','科幻和喜剧都要','1990到2000年']:
   _,f=parse(message);selected=sorted(ids(f));result=verify_final(json.dumps({'answer':'模拟模型正确理解','movie_ids':selected}),observed,Reader(),1,f)
   self.assertEqual({m['movie_id'] for m in result['items']},set(selected))
 def test_soft_preference_does_not_veto_correct_model(self):
  class Reader:
   behavior=SimpleNamespace(snapshot=lambda user:{})
   def get_movies(self,wanted):return [m for m in MOVIES if m['movie_id'] in wanted]
  _,f=parse('不要太恐怖');result=verify_final('{"answer":"模型依据程度偏好解释喜剧","movie_ids":[1]}',{1:MOVIES[0]},Reader(),1,f);self.assertEqual(result['items'][0]['movie_id'],1)
 def test_model_cannot_weaken_explicit_user_filters(self):
  _,f=parse('2000年以前，科幻或喜剧，不要恐怖')
  merged=merge_for_tool(f,{'genre':'喜剧'});self.assertEqual(ids(merged),{1})
  with self.assertRaises(ValueError):merge_for_tool(f,{'year_from':2010})
  with self.assertRaises(ValueError):merge_for_tool(f,{'genre':'恐怖'})
 def test_negative_model_arguments_cannot_invert_user_intent(self):
  class Reader:
   def search(self,user,f,keyword,limit):return [m for m in MOVIES if matches(m,f,{})],False
  _,f=parse('不想看恐怖片');tool=AgentTools(Reader(),1,conditions=f)
  result=tool.execute('search_movies',{'filters':{},'limit':10});self.assertEqual({m['movie_id'] for m in result['items']},{1,2,3,5})
  with self.assertRaises(ValueError):tool.execute('search_movies',{'filters':{'genre':'恐怖'}})
if __name__=='__main__':unittest.main()
