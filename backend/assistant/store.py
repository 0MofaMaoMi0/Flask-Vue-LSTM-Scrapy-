"""Independent M7 MySQL auxiliary store. Never creates M6 tables."""
import re
from sqlalchemy import MetaData,Table,Column,String,BigInteger,Integer,JSON,DateTime,create_engine,text
from sqlalchemy.engine import make_url

def connect_store(url,uuid):
    u=make_url(url)
    if u.drivername!='mysql+pymysql' or u.host!='127.0.0.1' or u.port!=3306 or not u.password or u.query or not re.fullmatch(r'mi2_m7_[a-z0-9_]{1,40}',u.database or '') or not re.fullmatch(r'mi2m7[a-z0-9_]{1,24}',u.username or ''):raise RuntimeError('M7 isolation contract')
    engine=create_engine(u,pool_pre_ping=True,hide_parameters=True)
    with engine.connect() as c:
        row=c.execute(text('SELECT @@server_uuid,DATABASE(),CURRENT_USER()')).one()
        if row[0]!=uuid or row[1]!=u.database or row[2].split('@')[0]!=u.username:raise RuntimeError('M7 runtime identity')
    return engine

def tables():
    md=MetaData()
    Table('sessions',md,Column('id',String(64),primary_key=True),Column('user_id',BigInteger,nullable=False),Column('auth_binding',String(64),nullable=False),Column('state',JSON,nullable=False),Column('turns',Integer,nullable=False),Column('expires_at',DateTime,nullable=False))
    Table('requests',md,Column('id',String(64),primary_key=True),Column('user_id',BigInteger,nullable=False),Column('fingerprint',String(64),nullable=False),Column('response',JSON),Column('expires_at',DateTime,nullable=False))
    Table('budgets',md,Column('id',String(96),primary_key=True),Column('calls',Integer,nullable=False))
    Table('cache',md,Column('id',String(64),primary_key=True),Column('response',JSON,nullable=False),Column('expires_at',DateTime,nullable=False))
    Table('usage',md,Column('id',String(64),primary_key=True),Column('user_id',BigInteger,nullable=False),Column('model',String(64),nullable=False),Column('status',String(32),nullable=False),Column('tokens',JSON),Column('created_at',DateTime,nullable=False))
    return md
