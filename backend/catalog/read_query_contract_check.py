"""Offline Select compilation only; no database execution."""
import argparse,json,pathlib,sys,platform
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from catalog import read_queries as q
from sqlalchemy.dialects import mysql
from sqlalchemy.sql.selectable import Select
import sqlalchemy

def main():
    p=argparse.ArgumentParser();p.add_argument('--output-dir',required=True);p.add_argument('--negative',action='store_true');a=p.parse_args();out=pathlib.Path(a.output_dir).resolve()
    if not out.is_dir() or out.drive.upper()=='C:':raise ValueError('Approved non-C output required')
    rows=[];samples=[]
    def check(name,ok):rows.append({'case':name,'expected':True,'actual':bool(ok),'status':'PASS' if ok else 'FAIL'})
    def compile_query(stmt):
        if not isinstance(stmt,Select):raise ValueError('Not Select')
        c=stmt.compile(dialect=mysql.dialect());samples.append({'sql':str(c),'params':c.params});return str(c),c.params
    def rejects(fn):
        try:fn()
        except ValueError:return True
        return False
    sql,params=compile_query(q.movie_list());check('default active filter','active' in params.values() and 'catalog_status' in sql);check('bounded default pagination',20 in params.values() and 0 in params.values())
    for sort in q.SORTS:
        s,v=compile_query(q.movie_list(sort=sort));check('stable sort '+sort,'ORDER BY' in s and 'movie_id' in s.split('ORDER BY')[1])
    s,v=compile_query(q.movie_list(year_from=1800,year_to=2199,genre_id=7));check('year bindings',1800 in v.values() and 2199 in v.values());check('genre EXISTS no multiplying JOIN','EXISTS' in s and 'movie_genres' in s and 'JOIN' not in s)
    for text in ['%', '_', 'SYNTHETIC\\path', "SYNTHETIC' OR 1=1 --",'a/b']:
        s,v=compile_query(q.movie_list(keyword=text));expected=text.replace('/','//').replace('%','/%').replace('_','/_');check('literal keyword '+text,expected in v.values() and "ESCAPE '/'" in s and (len(text)==1 or text not in s))
    for name,kwargs in [('zero limit',{'limit':0}),('huge limit',{'limit':101}),('negative offset',{'offset':-1}),('huge offset',{'offset':10001}),('bool limit',{'limit':True}),('invalid sort',{'sort':'injected'}),('empty keyword',{'keyword':''}),('space keyword',{'keyword':' '}),('bad year low',{'year_from':1799}),('bad year high',{'year_to':2200}),('inverted years',{'year_from':2001,'year_to':2000})]:check(name,rejects(lambda kwargs=kwargs:q.movie_list(**kwargs)))
    for value in [0,-1,True,'1',None]:check('invalid movie id '+str(value),rejects(lambda value=value:q.movie_detail(value)))
    s,v=compile_query(q.movie_detail(999999));check('missing detail remains bounded Select',999999 in v.values() and 'active' in v.values() and 'LIMIT' in s)
    for name,fn in [('external ids',q.movie_external_ids),('aliases',q.movie_aliases),('genres',q.movie_genres),('ratings',q.movie_ratings)]:
        s,v=compile_query(fn(1));check('separate detail '+name,'active' in v.values() and 'EXISTS' in s and 'ORDER BY' in s)
    for source in q.SOURCES:
        s,v=compile_query(q.movie_by_external_id(source,'SYNTHETIC-ID'));check('external exact '+source,source in v.values() and 'SYNTHETIC-ID' in v.values() and 'LIKE' not in s)
    for source,eid in [('invalid','x'),('tmdb',''),('tmdb',' x '),('tmdb','x'*129)]:check('invalid external '+source+str(len(eid)),rejects(lambda source=source,eid=eid:q.movie_by_external_id(source,eid)))
    check('no arbitrary relation',rejects(lambda:q.related(1,'injected')))
    if a.negative:check('EXPECTED failure missing active marker','active' not in compile_query(q.movie_list())[1].values())
    report={'cases':rows,'failed':sum(x['status']=='FAIL' for x in rows),'python':platform.python_version(),'sqlalchemy':sqlalchemy.__version__,'scope':'Select compilation only; missing-row behavior not executed'}
    (out/'read_query_results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');(out/'synthetic_sql.json').write_text(json.dumps(samples,ensure_ascii=False,indent=2),encoding='utf-8');print('checks',len(rows),'failed',report['failed']);return 1 if report['failed'] else 0
if __name__=='__main__':raise SystemExit(main())
