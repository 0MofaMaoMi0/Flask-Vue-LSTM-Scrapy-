import unittest
from ops.m6.m7_reliability import fingerprint
class BackupFingerprintTests(unittest.TestCase):
 def fake(self,q):
  if q.startswith('SHOW CREATE'):return 'usage\tCREATE TABLE `usage` (`id` varchar(64) PRIMARY KEY)'
  if 'information_schema.columns' in q:return 'id\ntokens'
  if 'key_column_usage' in q:return 'id'
  return '["a", {"input_tokens": null}]'
 def test_determinism(self):
  a=fingerprint(self.fake,'mi2_m7_assistant_20261107','usage');b=fingerprint(self.fake,'mi2_m7_restore_fixture_1','usage');self.assertEqual(a,b)
 def test_content_change(self):
  def changed(q):return '["changed", null]' if q.startswith('SELECT JSON_ARRAY') else self.fake(q)
  a=fingerprint(self.fake,'mi2_m7_restore_fixture_1','usage');b=fingerprint(changed,'mi2_m7_restore_fixture_1','usage');self.assertNotEqual(a['content_sha256'],b['content_sha256']);self.assertEqual(a['structure_sha256'],b['structure_sha256'])
 def test_identifier_rejection(self):
  for schema,table in [('mysql','usage'),('mi2_m7_restore_ok','usage; DROP TABLE')]:
   with self.assertRaises(RuntimeError):fingerprint(self.fake,schema,table)
if __name__=='__main__':unittest.main()
