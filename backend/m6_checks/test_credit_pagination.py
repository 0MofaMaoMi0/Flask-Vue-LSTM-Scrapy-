"""Exercise real Flask route validation and cursor boundaries without any DB connection."""
from datetime import datetime
from flask import Flask
from catalog.api_read import create_read_blueprint


def client():
    rows=[dict(credit_id=i,person_id=i,source='tmdb',role_type='cast',job=None,character_name='role',sort_order=i,name='person '+str(i),profile_path='/p.jpg') for i in range(1,124)]
    movie=dict(movie_id=1,title='test',catalog_status='active',release_year=2000)
    def execute(stmt):
        sql=str(stmt);params=stmt.compile().params
        if 'JOIN people' not in sql:return [movie] if 'FROM movies' in sql else []
        offset=stmt._offset_clause.value if stmt._offset_clause is not None else 0
        limit=stmt._limit_clause.value if stmt._limit_clause is not None else len(rows)
        return rows[offset:offset+limit]
    app=Flask(__name__);app.register_blueprint(create_read_blueprint(execute));return app.test_client()


def test_complete_pagination_no_duplicates():
    c=client();offset=0;ids=[]
    while True:
        r=c.get('/api/catalog/movies/1/credits?limit=50&offset='+str(offset));assert r.status_code==200
        data=r.json['data'];ids.extend(x['credit_id'] for x in data['items'])
        if not data['has_more']:break
        assert data['next_offset']>offset;offset=data['next_offset']
    assert ids==list(range(1,124))


def test_bad_and_duplicate_arguments_rejected():
    c=client()
    for query in ['limit=0','limit=101','offset=-1','offset=10001','limit=5&limit=6','sql=drop','offset=abc']:
        assert c.get('/api/catalog/movies/1/credits?'+query).status_code==400


def test_exact_page_boundary_has_no_phantom_page():
    data=client().get('/api/catalog/movies/1/credits?limit=41&offset=82').json['data']
    assert len(data['items'])==41 and data['has_more'] is False and data['next_offset'] is None
