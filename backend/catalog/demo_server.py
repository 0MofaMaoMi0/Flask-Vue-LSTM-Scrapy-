"""Loopback-only SYNTHETIC DEMO. No MySQL or authentication configuration."""
import sqlite3
import argparse,json,pathlib
from decimal import Decimal
from threading import RLock
from flask import Flask,jsonify
from sqlalchemy.dialects import sqlite
from .models import CatalogBase
from .api_read import create_read_blueprint

class DemoRepository:
    def __init__(self,dataset=None):
        self.lock=RLock();self.db=sqlite3.connect(':memory:',check_same_thread=False);self.db.row_factory=sqlite3.Row
        for table in CatalogBase.metadata.tables.values():
            columns=[]
            for col in table.c:
                typ='INTEGER' if col.type.__class__.__name__ in ('BigInteger','SmallInteger','Integer','Boolean') else 'TEXT'
                columns.append('"'+col.name+'" '+typ)
            self.db.execute('CREATE TABLE "'+table.name+'" ('+','.join(columns)+')')
        if dataset is not None:
            self.load_dataset(dataset)
            return
        titles=['星海来信','雨城最后一班车','山间放映室','第七次日出','风停在旧码头','无人岛的星期天','沿河慢行','玻璃森林','春日回声','北纬三十度','夜航手记','月光修理店','夏天的底片','远方的钟声','空房间里的海','隐秘草稿']
        for i,title in enumerate(titles,1):
            self.db.execute('INSERT INTO movies (movie_id,title,release_year,release_date,overview,runtime_minutes,catalog_status,updated_at,original_language,is_adult) VALUES (?,?,?,?,?,?,?,?,?,?)',(i,title,2010+i,'20%02d-05-01'%(10+i),'合成演示作品：'+title+'。一段关于相遇、选择与远方的故事。本记录不对应真实电影或真实平台评分。',85+i,'hidden' if i==16 else 'active','2026-01-%02dT12:00:00'%i,'zh',0))
            self.db.execute('INSERT INTO movie_genres VALUES (?,?)',(i,1+i%3))
            self.db.execute('INSERT INTO movie_external_ids (id,movie_id,source,external_id) VALUES (?,?,?,?)',(i,i,'tmdb','demo-'+str(i)))
            self.db.execute('INSERT INTO movie_ratings_summary (movie_id,source,score_value,score_scale,vote_count,observed_at) VALUES (?,?,?,?,?,?)',(i,'douban',str(6+i%4)+'.20',10,100+i,'2026-01-01T00:00:00'))
        for i,n in [(1,'剧情'),(2,'科幻'),(3,'冒险')]:self.db.execute('INSERT INTO genres (genre_id,name_zh) VALUES (?,?)',(i,n))
        self.db.commit()
    def load_dataset(self,path):
        from .data_pipeline import digest
        path=pathlib.Path(path).resolve()
        if path.drive.upper()=='C:':raise ValueError('Non-C dataset required')
        data=json.loads(path.read_text(encoding='utf-8'));original=dict(data);expected=original.pop('content_hash',None)
        if expected!=digest(original) or data.get('mode')!='SYNTHETIC':raise ValueError('Only integrity-checked synthetic M2 dataset enabled')
        self.mode='M2_SYNTHETIC_PIPELINE';self.dataset_id=expected;genres={}
        for movie in data['movies']:
            mid=movie['movie_id']
            self.db.execute('INSERT INTO movies (movie_id,title,original_title,release_date,release_year,runtime_minutes,original_language,overview,catalog_status,updated_at,is_adult) VALUES (?,?,?,?,?,?,?,?,?,?,?)',(mid,movie['title'],movie['original_title'],movie['release_date'],movie['release_year'],movie['runtime_minutes'],movie['original_language'],movie['overview'],'active',data['metadata']['imported_at'],0))
            for name in movie['genres']:
                if name not in genres:
                    genres[name]=len(genres)+1;self.db.execute('INSERT INTO genres (genre_id,name_zh) VALUES (?,?)',(genres[name],name))
                self.db.execute('INSERT INTO movie_genres VALUES (?,?)',(mid,genres[name]))
            for platform,eid in movie['external_ids'].items():self.db.execute('INSERT INTO movie_external_ids (movie_id,source,external_id) VALUES (?,?,?)',(mid,platform,eid))
            for alias in movie['aliases']:self.db.execute('INSERT INTO movie_aliases (movie_id,title) VALUES (?,?)',(mid,alias))
            grouped={}
            for rating in movie['rating_facts']:grouped.setdefault(rating['source'],[]).append(rating)
            for platform,facts in grouped.items():
                if len(facts)!=1:continue # Preserve facts in artifact, do not invent D02 winner.
                fact=facts[0];self.db.execute('INSERT INTO movie_ratings_summary (movie_id,source,score_value,score_scale,vote_count,observed_at) VALUES (?,?,?,?,?,?)',(mid,platform,fact['score_value'],fact['score_scale'],fact['vote_count'],fact['observed_at']))
        self.db.commit()
    def __call__(self,stmt):
        compiled=stmt.compile(dialect=sqlite.dialect(),compile_kwargs={'render_postcompile':True})
        params=tuple(compiled.params[n] for n in compiled.positiontup)
        with self.lock:rows=[dict(x) for x in self.db.execute(str(compiled),params).fetchall()]
        for row in rows:
            if row.get('score_value') is not None:row['score_value']=Decimal(row['score_value'])
            if row.get('is_adult') is not None:row['is_adult']=bool(row['is_adult'])
        return rows

def create_demo_app(dataset=None,behavior_db=None,initialize_behavior=False):
    app=Flask('movieinsight_catalog_demo');app.config['TESTING']=False
    repository=DemoRepository(dataset)
    app.register_blueprint(create_read_blueprint(repository))
    from .recommendation_api import create_recommendation_blueprint
    def recommendation_movies():
        with repository.lock:
            rows=[dict(x) for x in repository.db.execute('SELECT movie_id,title,release_year,original_language,catalog_status FROM movies')]
            for movie in rows:
                movie['genres']=[x[0] for x in repository.db.execute('SELECT genre_id FROM movie_genres WHERE movie_id=?',(movie['movie_id'],))]
                movie['demo_popularity']=((movie['movie_id']*37)%101)/100
        return rows
    behavior_store=None
    if behavior_db:
        from .persistent_behavior import PersistentBehaviorRepository
        # Dataset was integrity checked by DemoRepository before storage is opened.
        dataset_id=repository.dataset_id if dataset else 'M1-SYNTHETIC-SEED-v1'
        behavior_store=PersistentBehaviorRepository(behavior_db,dataset_id,initialize=initialize_behavior)
    elif initialize_behavior:raise ValueError('--behavior-db required')
    app.register_blueprint(create_recommendation_blueprint(recommendation_movies,behavior_store))
    @app.get('/api/catalog/genres')
    def genres():
        with repository.lock:items=[dict(x) for x in repository.db.execute('SELECT genre_id,name_zh FROM genres ORDER BY genre_id')]
        return jsonify(code='OK',data={'items':items})
    @app.get('/api/catalog/demo-info')
    def info():return jsonify(mode='M2_SYNTHETIC_PIPELINE' if dataset else 'DEMO_SYNTHETIC',data_source='SYNTHETIC',identity='PUBLIC_DEMO_SELECTION',persistence='M4_SQLITE_SYNTHETIC' if behavior_db else 'MEMORY_VOLATILE',real_database='BLOCKED')
    return app
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--dataset');parser.add_argument('--port',type=int,default=8873);parser.add_argument('--behavior-db');parser.add_argument('--initialize-behavior',action='store_true');args=parser.parse_args()
    create_demo_app(args.dataset,args.behavior_db,args.initialize_behavior).run(host='127.0.0.1',port=args.port,debug=False,use_reloader=False)
