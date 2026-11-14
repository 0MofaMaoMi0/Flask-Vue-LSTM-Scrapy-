import unittest
from catalog.m9_append import genre_plan,collision_check
class AppendTests(unittest.TestCase):
 def test_existing_genre_reused(self):
  self.assertEqual(genre_plan([{'genre_id':1,'tmdb_genre_id':'18','name_zh':'剧情','name_en':None}],[{'genre_id':8,'tmdb_genre_id':'18','name_zh':'剧情','name_en':None}]),{1:8})
 def test_genre_conflict_rejected(self):
  with self.assertRaises(ValueError):genre_plan([{'genre_id':1,'tmdb_genre_id':'18','name_zh':'改名','name_en':None}],[{'genre_id':8,'tmdb_genre_id':'18','name_zh':'剧情','name_en':None}])
 def test_new_genre_planned(self):self.assertEqual(genre_plan([{'genre_id':1,'tmdb_genre_id':'35'}],[]),{1:None})
 def test_duplicate_external_owner_rejected(self):
  with self.assertRaises(ValueError):collision_check([{'source':'tmdb','external_id':'550'}],[{'source':'tmdb','external_id':'550'}])
 def test_independent_external_namespaces(self):collision_check([{'source':'tmdb','external_id':'550'}],[{'source':'other','external_id':'550'}])
if __name__=='__main__':unittest.main()
