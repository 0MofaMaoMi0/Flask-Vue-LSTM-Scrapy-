import unittest,math
from datetime import datetime
from analytics.evaluator import ranking_metrics,temporal_split,experiment
from analytics.metrics import summarize_usage
from analytics.api import parse_window
class MetricsTest(unittest.TestCase):
 def test_rank(self):
  r=ranking_metrics({'a':[1,2]},{'a':{2,3}},[1,2,3,4],2)
  self.assertEqual(r['precision'],.5);self.assertEqual(r['recall'],.5);self.assertAlmostEqual(r['ndcg'],(1/math.log2(3))/(1+1/math.log2(3)));self.assertEqual(r['coverage'],.5)
 def test_invalid(self):
  with self.assertRaises(ValueError):ranking_metrics({'a':[1,1]},{'a':{2}},[1,2],2)
 def test_split(self):
  self.assertEqual(temporal_split([('a',1,1),('a',1,1),('a',2,2)]),({'a':[1]},{'a':{2}}))
 def test_experiment(self):
  movies=[dict(movie_id=i,title=str(i),catalog_status='active',genres=['g']) for i in range(1,5)];rows=[('a',1,1),('a',2,2),('b',3,1),('b',4,2)]
  a=experiment(movies,rows);self.assertEqual(a,experiment(movies,rows));self.assertEqual(set(a['algorithms']),{'Popularity','MovieInsightRules'})
 def test_usage(self):
  def row(tokens,status='SUCCEEDED'):return dict(tokens=tokens,status=status,model='mock',created_at=datetime(2026,11,7))
  r=summarize_usage([row(None),row({'input_tokens':100,'output_tokens':10,'cached_input_tokens':40}),row(None,'NOT_DISPATCHED')])
  self.assertEqual(r['calls'],2);self.assertEqual(r['unknown_calls'],1);self.assertEqual(r['known_tokens']['input_tokens'],100);self.assertEqual(r['known_tokens']['cached_input_tokens'],40);self.assertIsNone(r['amount']);self.assertIsNone(summarize_usage([row(None)])['known_tokens']['input_tokens'])
 def test_windows(self):
  a,b,w=parse_window({'from':'2026-11-06T23:00:00Z','to':'2026-11-07T01:00:00Z'});self.assertEqual((b-a).total_seconds(),7200)
  for args in [{'from':'bad'},{'from':'2020-01-01','to':'2026-01-01'},{'from':'2026-11-07','to':'2026-11-07'},{'from':'2026-11-06T23:00:00'}]:
   with self.assertRaises(ValueError):parse_window(args)
if __name__=='__main__':unittest.main()
