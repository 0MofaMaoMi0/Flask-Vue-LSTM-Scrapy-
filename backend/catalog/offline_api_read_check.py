"""Synthetic Flask test_client only. No network listener or database."""
import argparse,ast,datetime,decimal,inspect,json,pathlib,platform,sys
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from flask import Flask
import flask,sqlalchemy
from sqlalchemy.sql import visitors,operators
from sqlalchemy.dialects import mysql
from catalog import api_read as api
from catalog import read_queries as q

def has_active(stmt):
    return any(getattr(n,'operator',None) is operators.eq and getattr(getattr(n,'left',None),'name',None)=='catalog_status' and getattr(getattr(getattr(n,'left',None),'table',None),'name',None)=='movies' and getattr(getattr(n,'right',None),'value',None)=='active' for n in visitors.iterate(stmt))

def main():
    p=argparse.ArgumentParser();p.add_argument('--output-dir',required=True);p.add_argument('--mutate-external-active',action='store_true');p.add_argument('--negative',action='store_true');a=p.parse_args();out=pathlib.Path(a.output_dir).resolve()
    if not out.is_dir() or out.drive.upper()=='C:':raise ValueError('Approved non-C output required')
    if a.mutate_external_active:
        tree=ast.parse(inspect.getsource(q.movie_by_external_id))
        class Remove(ast.NodeTransformer):
            def visit_Call(self,node):
                self.generic_visit(node)
                if isinstance(node.func,ast.Attribute) and node.func.attr=='where':node.args=[x for x in node.args if 'catalog_status' not in ast.unparse(x)]
                return node
        tree=ast.fix_missing_locations(Remove().visit(tree));exec(compile(tree,'synthetic-mutant','exec'),q.__dict__)
    results=[];samples=[];seen=[];mode={'value':'active'}
    def check(name,condition):results.append({'case':name,'expected':True,'actual':bool(condition),'status':'PASS' if condition else 'FAIL'})
    synthetic={'movie_id':1,'title':'SYNTHETIC','catalog_status':'active','release_date':datetime.date(2020,1,2),'secret_debug':'NEVER_EXPORT'}
    def executor(stmt):
        seen.append(stmt)
        if not has_active(stmt):raise RuntimeError('SYNTHETIC missing active')
        if mode['value']=='exception':raise RuntimeError('SYNTHETIC SQL SECRET')
        if mode['value']=='missing':return []
        table=next(iter(stmt.get_final_froms())).description
        compiled=stmt.compile(dialect=mysql.dialect());samples.append({'sql':str(compiled),'params':compiled.params})
        if 'movie_aliases' in str(compiled):return [{'title':'SYNTHETIC alias'}]*100
        if 'movie_ratings_summary' in str(compiled):return [{'source':'douban','score_value':decimal.Decimal('8.20'),'score_scale':10}]*2
        if 'genres' in str(compiled):return [{'genre_id':1,'name_zh':'SYNTHETIC'}]
        if 'movie_external_ids' in str(compiled) and 'JOIN' not in str(compiled):return [{'source':'tmdb','external_id':'SYNTHETIC'}]
        return [dict(synthetic,catalog_status=mode['value'])]
    app=Flask('SYNTHETIC');app.register_blueprint(api.create_read_blueprint(executor));client=app.test_client()
    for name,stmt in [('list',q.movie_list()),('detail',q.movie_detail(1)),('lookup',q.movie_by_external_id('tmdb','SYNTHETIC')),('aliases',q.movie_aliases(1)),('genres',q.movie_genres(1)),('ids',q.movie_external_ids(1)),('ratings',q.movie_ratings(1))]:check('active predicate '+name,has_active(stmt))
    response=client.get('/api/catalog/movies');check('list success contract',response.status_code==200 and set(response.json['data'])=={'items','limit','offset'});check('DTO no internal data','secret_debug' not in response.get_data(as_text=True))
    detail=client.get('/api/catalog/movies/1');check('detail success',detail.status_code==200)
    if detail.status_code==200:
        check('date serialization',detail.json['data']['release_date']=='2020-01-02');check('decimal serialization',detail.json['data']['ratings'][0]['score_value']=='8.20');check('association size bounded',len(detail.json['data']['aliases'])==50)
    check('associated Select limit before execute',all(s._limit_clause is not None for s in seen))
    check('lookup success',client.get('/api/catalog/lookup?source=tmdb&external_id=SYNTHETIC').status_code==200)
    for uri in ['/api/catalog/movies?limit=0','/api/catalog/movies?limit=101','/api/catalog/movies?sort=SQL','/api/catalog/movies?offset=-1','/api/catalog/movies?genre_id=0','/api/catalog/movies?limit=2&limit=3','/api/catalog/movies?unknown=x','/api/catalog/lookup?source=bad&external_id=x','/api/catalog/lookup?source=tmdb','/api/catalog/movies/0']:
        check('invalid '+uri,client.get(uri).status_code==400)
    for mode_value in ('missing','hidden','draft'):
        mode['value']=mode_value
        check(mode_value+' detail 404',client.get('/api/catalog/movies/1').status_code==404)
        check(mode_value+' lookup 404',client.get('/api/catalog/lookup?source=tmdb&external_id=SYNTHETIC').status_code==404)
        check(mode_value+' list empty',client.get('/api/catalog/movies').json['data']['items']==[])
    mode['value']='exception';err=client.get('/api/catalog/movies');check('exception shield',err.status_code==500 and err.json=={'code':'INTERNAL_ERROR','data':None})
    absent=Flask('SYNTHETIC-NO-EXECUTOR');absent.register_blueprint(api.create_read_blueprint());check('missing executor closed',absent.test_client().get('/api/catalog/movies').status_code==503)
    check('write method rejected',client.post('/api/catalog/movies').status_code==405)
    if a.negative:check('EXPECTED incorrect status predicate',client.get('/api/catalog/movies').status_code==200)
    report={'cases':results,'failed':sum(x['status']=='FAIL' for x in results),'python':platform.python_version(),'sqlalchemy':sqlalchemy.__version__,'synthetic_only':True}
    (out/'api_results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');(out/'synthetic_api_sql.json').write_text(json.dumps(samples,ensure_ascii=False,indent=2),encoding='utf-8');print('checks',len(results),'failed',report['failed']);return 1 if report['failed'] else 0
if __name__=='__main__':raise SystemExit(main())
