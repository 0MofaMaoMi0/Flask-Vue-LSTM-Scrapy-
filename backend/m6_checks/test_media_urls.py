from urllib.parse import unquote
from flask import Flask
from catalog.media_urls import asset_url
from catalog.api_read import create_read_blueprint

PATH='/qf8HaEGNUFXU6nVAq9BVEk4Tiqb.jpg'
def test_relative_tmdb_size_and_legacy_urls():
 assert unquote(asset_url(dict(source='tmdb',source_path=PATH))).endswith('https://image.tmdb.org/t/p/w342'+PATH)
 assert unquote(asset_url(dict(source='tmdb',source_path=PATH,asset_type='backdrop'))).endswith('https://image.tmdb.org/t/p/w1280'+PATH)
 assert asset_url(dict(source='douban',source_path='https://img1.doubanio.com/view/photo.jpg')).startswith('/api/media/poster?u=')
 assert asset_url(dict(source='tmdb',source_path='https://image.tmdb.org/t/p/w500'+PATH))

def test_invalid_assets_never_become_local_or_arbitrary_requests():
 for path in ['/../secret.jpg','//evil.test/p.jpg','/x.jpg?key=x','http://127.0.0.1/x.jpg','https://image.tmdb.org:invalid/x.jpg','https://user:secret@image.tmdb.org/x.jpg',None]:
  assert asset_url(dict(source='tmdb',source_path=path)) is None

def test_list_and_detail_use_same_resolver_preserve_source_path():
 movie=dict(movie_id=2994,title='挽救计划',catalog_status='active')
 assets=[dict(movie_id=2994,source='tmdb',source_path=PATH,asset_type='poster',is_primary=True)]
 def execute(stmt):
  names=set(stmt.selected_columns.keys())
  return [movie] if 'title' in names else assets if 'source_path' in names else []
 app=Flask(__name__);app.register_blueprint(create_read_blueprint(execute));c=app.test_client()
 listing=c.get('/api/catalog/movies').json['data']['items'][0]
 detail=c.get('/api/catalog/movies/2994').json['data']['assets'][0]
 assert listing['poster']==detail['url']==asset_url(assets[0])
 assert detail['source_path']==PATH
