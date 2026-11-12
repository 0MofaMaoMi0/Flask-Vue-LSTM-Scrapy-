import unittest,json
from unittest.mock import patch
from assistant.providers import DeepSeekProvider,Unavailable,verify_output
class Response:
 def __init__(self,body):self.body=body
 def __enter__(self):return self
 def __exit__(self,*a):pass
 def read(self,n):return self.body
class ProviderBoundary(unittest.TestCase):
 def test_official_request_non_thinking(self):
  body={'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'movie_ids':[1],'reason_codes':['CATALOG_FACTS']})}}],'usage':{'prompt_tokens':10,'completion_tokens':5}}
  with patch('urllib.request.build_opener') as op:
   op.return_value.open.return_value=Response(json.dumps(body).encode());p=DeepSeekProvider('SYNTHETIC_ONLY');result=p.explain([{'movie_id':1}]);verify_output(result,[{'movie_id':1}]);req=op.return_value.open.call_args.args[0];payload=json.loads(req.data)
   self.assertEqual(payload['thinking'],{'type':'disabled'});self.assertEqual(payload['max_tokens'],600);self.assertNotIn('tools',payload)
 def test_empty_truncated_and_malformed(self):
  for raw in [b'',b'{}',b'x'*65537,json.dumps({'choices':[{'finish_reason':'length','message':{'content':'{}'}}]}).encode(),json.dumps({'choices':[{'finish_reason':'stop','message':{'content':' '}}]}).encode()]:
   with self.subTest(length=len(raw)),patch('urllib.request.build_opener') as op:
    op.return_value.open.return_value=Response(raw)
    with self.assertRaises(Unavailable):DeepSeekProvider('SYNTHETIC_ONLY').explain([])
 def test_network_timeout_no_retry(self):
  with patch('urllib.request.build_opener') as op:
   op.return_value.open.side_effect=TimeoutError()
   with self.assertRaises(Unavailable):DeepSeekProvider('SYNTHETIC_ONLY').explain([])
   self.assertEqual(op.return_value.open.call_count,1)
 def test_http_429_and_5xx_no_retry(self):
  from urllib.error import HTTPError
  for status in (429,500,503):
   with self.subTest(status=status),patch('urllib.request.build_opener') as op:
    op.return_value.open.side_effect=HTTPError('https://api.deepseek.com/chat/completions',status,'synthetic',{},None)
    with self.assertRaises(Unavailable):DeepSeekProvider('SYNTHETIC_ONLY').explain([])
    self.assertEqual(op.return_value.open.call_count,1)
 def test_unapproved_model_denied(self):
  with self.assertRaises(ValueError):DeepSeekProvider('SYNTHETIC_ONLY',model='deepseek-v4-pro')
if __name__=='__main__':unittest.main()
