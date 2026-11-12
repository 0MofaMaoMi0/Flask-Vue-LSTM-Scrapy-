"""Frozen schema reflection and actual MySQL CHECK counterexamples."""
import copy
from sqlalchemy import text, select, insert, inspect, CheckConstraint, UniqueConstraint
from sqlalchemy.dialects import mysql
from sqlalchemy.exc import DBAPIError
from catalog.m6_contract import authority
from catalog.m6_offline_check import fixture
from catalog.m6_mysql import checked_rows
from catalog.m6_offline import rows_hash


def run_schema_checks(app,check):
    bundle=authority();engine=app.extensions['auth_engine'];schema=app.config['M6_SCHEMA']
    def kind(value):
        result=value.lower().split(' collate ')[0].split(' character set ')[0].replace(' ','')
        return result.replace('numeric(','decimal(').replace('integer','int').replace('bool','tinyint(1)')
    with engine.connect() as connection:
        inspector=inspect(connection)
        actual=set(inspector.get_table_names())
        check('frozen_catalog_tables_present',set(bundle['metadata'].tables)<=actual)
        for table in bundle['metadata'].tables.values():
            columns=connection.execute(text('SELECT COLUMN_NAME,COLUMN_TYPE,IS_NULLABLE,COLUMN_DEFAULT,COLLATION_NAME,CHARACTER_SET_NAME,EXTRA FROM information_schema.columns WHERE table_schema=:schema AND table_name=:table ORDER BY ORDINAL_POSITION'),{'schema':schema,'table':table.name}).mappings().all()
            check(table.name+'.frozen_column_order',list(table.c.keys())==[r['COLUMN_NAME'] for r in columns])
            for col,row in zip(table.c,columns):
                prefix=table.name+'.'+col.name
                check(prefix+'.type',kind(col.type.compile(dialect=mysql.dialect()))==kind(row['COLUMN_TYPE']))
                check(prefix+'.nullable',col.nullable==(row['IS_NULLABLE']=='YES'))
                check(prefix+'.generated',bool(col.computed)==('GENERATED' in row['EXTRA']))
                if col.computed:check(prefix+'.virtual','VIRTUAL GENERATED' in row['EXTRA'])
                else:check(prefix+'.default',(str(col.server_default.arg) if col.server_default else None)==row['COLUMN_DEFAULT'])
                if getattr(col.type,'collation',None):check(prefix+'.collation',col.type.collation==row['COLLATION_NAME'])
                if getattr(col.type,'charset',None):check(prefix+'.charset',col.type.charset==row['CHARACTER_SET_NAME'])
            check(table.name+'.primary_key',list(table.primary_key.columns.keys())==inspector.get_pk_constraint(table.name)['constrained_columns'])
            expected={c.name:list(c.columns.keys()) for c in table.constraints if isinstance(c,UniqueConstraint)}
            check(table.name+'.unique_keys',expected=={c['name']:c['column_names'] for c in inspector.get_unique_constraints(table.name)})
            actual_indexes={x['name']:x['column_names'] for x in inspector.get_indexes(table.name)}
            for index in table.indexes:check(table.name+'.index.'+index.name,actual_indexes.get(index.name)==list(index.columns.keys()))
            checks=connection.execute(text("SELECT CONSTRAINT_NAME FROM information_schema.table_constraints WHERE constraint_schema=:schema AND table_name=:table AND constraint_type='CHECK' AND ENFORCED='YES'"),{'schema':schema,'table':table.name}).scalars().all()
            check(table.name+'.checks_enabled',set(checks)=={c.name for c in table.constraints if isinstance(c,CheckConstraint)})
            expected_fk=sorted((tuple(e.parent.name for e in fk.elements),fk.elements[0].column.table.name,tuple(e.column.name for e in fk.elements),fk.ondelete,fk.onupdate) for fk in table.foreign_key_constraints)
            reflected=inspector.get_foreign_keys(table.name)
            actual_fk=sorted((tuple(fk['constrained_columns']),fk['referred_table'],tuple(fk['referred_columns']),fk['options'].get('ondelete','RESTRICT'),fk['options'].get('onupdate','RESTRICT')) for fk in reflected)
            check(table.name+'.foreign_keys',actual_fk==expected_fk and all(fk['referred_schema'] in (None,schema) for fk in reflected))
        baseline=rows_hash(checked_rows(connection,bundle))
    data=fixture(bundle)
    data['genres'][0]['tmdb_genre_id']='probe18'
    data['movie_external_ids'][0]['external_id']='probe900001'
    data['movie_source_records'][0]['source_record_id']='probe900001'
    mutations={
      'movies':["catalog_status='invalid'",'release_year=1799','runtime_minutes=0'],
      'movie_external_ids':["source='invalid'","external_id=''"],
      'movie_source_records':["source='invalid'","license_status='invalid'"],
      'movie_ratings_summary':['score_scale=0,score_value=NULL','vote_count=-1','score_value=10.01','vote_count=0'],
      'movie_credits':["role_type='invalid'",'sort_order=-1'],
      'movie_assets':["asset_type='invalid'",'width=0','height=0',"license_status='restricted'"],
      'legacy_movie_map':["legacy_table='invalid'","match_status='invalid'",'movie_id=NULL',"match_status='skipped',reason_code=NULL","reason_code='invalid'"],
      'ingest_batches':["mode='invalid'","status='invalid'"],
      'ingest_items':["status='invalid'",'attempt_count=0'],
      'match_reviews':["status='invalid'"],
      'data_quality_issues':["severity='invalid'","status='invalid'"]}
    declared={c.name for t in bundle['metadata'].tables.values() for c in t.constraints if isinstance(c,CheckConstraint)}
    planned={'ck_'+name+'_'+str(i) for name,items in mutations.items() for i in range(len(items))}
    check('check_counterexamples_cover_frozen_set',declared==planned)
    with engine.connect() as connection:
        transaction=connection.begin()
        try:
            for name in bundle['order']:
                table=bundle['metadata'].tables[name]
                for row in data[name]:
                    values={col.name:row[col.name] for col in table.c if col.computed is None}
                    connection.execute(insert(table).values(**values))
            for name,items in mutations.items():
                table=bundle['metadata'].tables[name];original=data[name][0]
                where=' AND '.join('`'+col.name+'`=:pk_'+col.name for col in table.primary_key.columns)
                params={'pk_'+col.name:original[col.name] for col in table.primary_key.columns}
                for i,change in enumerate(items):
                    constraint='ck_'+name+'_'+str(i)
                    nested=connection.begin_nested();passed=False
                    try:connection.execute(text('UPDATE `'+name+'` SET '+change+' WHERE '+where),params)
                    except DBAPIError as error:passed=error.orig.args[0]==3819 and constraint in str(error.orig.args[1])
                    finally:nested.rollback()
                    check('mysql_rejects_'+constraint,passed)
            checks=[('core_slot',"SELECT core_source_slot FROM movie_external_ids WHERE id=5001",'tmdb'),
                    ('primary_slot',"SELECT primary_slot FROM movie_assets WHERE asset_id=10001",'poster'),
                    ('approved_slot_pending',"SELECT approved_source_record_id FROM match_reviews WHERE id=13001",None)]
            for name,query,expected in checks:check('mysql_generated_'+name,connection.execute(text(query)).scalar_one()==expected)
        finally:transaction.rollback()
    with engine.connect() as connection:check('constraint_probe_rows_rolled_back',rows_hash(checked_rows(connection,bundle))==baseline)
