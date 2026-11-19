import unittest
from assistant.intent import parse,matches
from assistant.providers import FakeProvider,verify_output,Unavailable
class AssistantContracts(unittest.TestCase):
    def test_multiturn(self):
        _,f=parse('2010年之后的科幻片，时长别太长')
        _,f=parse('不要恐怖，只看我没看过的',f)
        self.assertEqual(f['year_from'],2011);self.assertNotIn('runtime_max',f);self.assertIn('runtime',f['_soft_preferences'])
        m={'release_year':2012,'runtime_minutes':100,'genres':['科幻'],'original_language':'en'}
        self.assertTrue(matches(m,f,{}));self.assertFalse(matches(m,f,{'watched':True}))
    def test_hallucinations(self):
        for ids in [[999],[True],[1,1]]:
            with self.assertRaises(Unavailable):verify_output({'movie_ids':ids,'reason_codes':['CATALOG_FACTS']},[{'movie_id':1}])
    def test_fake(self):
        facts=[{'movie_id':1,'title':'合成片'}];p=FakeProvider();self.assertEqual(verify_output(p.explain(facts),facts)[0],[1]);self.assertFalse(p.paid)
    def test_input(self):
        for bad in [None,{},'', 'x'*501]:
            with self.assertRaises(ValueError):parse(bad)
if __name__=='__main__':unittest.main()
