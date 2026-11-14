"""M1 synthetic provider, API and live loopback HTTP verification."""
import pathlib,sys,json,urllib.request,urllib.error
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from catalog.demo_server import create_demo_app
from catalog.api_read import create_read_blueprint
from flask import Flask

def main(out):
    results=[]
    def check(name,condition):
        results.append({'case':name,'pass':bool(condition)})
        if not condition:raise ValueError(name)
    client=create_demo_app().test_client()
    get=lambda p:client.get('/api/catalog/'+p)
    first=get('movies');check('six selected synthetic rows',len(get('movies?limit=6').json['data']['items'])==6)
    check('hidden never listed',all(x['movie_id']!=16 for x in first.json['data']['items']))
    check('keyword result',get('movies?keyword=星海').json['data']['items'][0]['movie_id']==1)
    check('year interval',len(get('movies?year_from=2011&year_to=2011').json['data']['items'])==1)
    check('genre existence filter',len(get('movies?genre_id=2').json['data']['items'])==5)
    check('paging disjoint',get('movies?limit=6&offset=6').json['data']['items'][0]['movie_id']!=get('movies?limit=6').json['data']['items'][0]['movie_id'])
    check('detail relation ratings serialized',get('movies/1').json['data']['ratings'][0]['score_value']=='7.20')
    check('external exact lookup',get('lookup?source=tmdb&external_id=demo-3').json['data']['movie_id']==3)
    for route in ['movies/16','movies/999','lookup?source=tmdb&external_id=demo-16','lookup?source=tmdb&external_id=missing']:check('404 '+route,get(route).status_code==404)
    for route in ['movies?limit=0','movies?sort=bad','movies?year_from=1799','movies?limit=2&limit=3','lookup?source=bad&external_id=x']:check('400 '+route,get(route).status_code==400)
    check('405 write rejected',client.post('/api/catalog/movies').status_code==405)
    check('injection parameterized',get("movies?keyword=%27%20OR%201%3D1%20--").json['data']['items']==[])
    check('escaped wildcard',get('movies?keyword=%25').json['data']['items']==[])
    for name,executor,code in [('no executor',None,503),('DTO bad internal value',lambda s:[{'movie_id':1,'catalog_status':'active','title':object()}],500),('executor ValueError',lambda s:(_ for _ in ()).throw(ValueError('INTERNAL SYNTHETIC')),500)]:
        app=Flask('synthetic-'+name);app.register_blueprint(create_read_blueprint(executor));resp=app.test_client().get('/api/catalog/movies');check(name,resp.status_code==code and 'SYNTHETIC' not in resp.get_data(as_text=True))
    with urllib.request.urlopen('http://127.0.0.1:5175/api/catalog/movies?limit=6') as response:
        data=json.load(response);check('live frontend proxy HTTP to backend provider',response.status==200 and len(data['data']['items'])==6)
    pathlib.Path(out,'demo-api-results.json').write_text(json.dumps({'mode':'DEMO synthetic SQLite memory only; MySQL BLOCKED','results':results},ensure_ascii=False,indent=2),encoding='utf-8')
    print('M1 API checks',len(results),'passed')
if __name__=='__main__':main(sys.argv[1])
