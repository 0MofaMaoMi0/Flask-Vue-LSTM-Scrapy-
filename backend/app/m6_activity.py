"""Support tables outside the frozen sixteen-table catalog."""
import re
from sqlalchemy import (MetaData, Table, Column, BigInteger, String, JSON, DateTime,
                        ForeignKey, UniqueConstraint)


def activity_tables(schema, catalog_schema):
    if (not isinstance(schema, str) or not re.fullmatch(r'mi2_m6_[a-z0-9_]{8,48}', schema)
            or schema == catalog_schema):
        raise RuntimeError('Separate approved M6 activity schema required')
    metadata = MetaData()
    # Reference declarations only: these tables are never created here.
    Table('auth_users', metadata, Column('id', BigInteger, primary_key=True), schema=catalog_schema)
    Table('movies', metadata, Column('movie_id', BigInteger, primary_key=True), schema=catalog_schema)
    receipts = Table('m6_import_receipts', metadata,
        Column('receipt_key', String(64), primary_key=True),
        Column('source_hash', String(64), nullable=False),
        Column('source_seal', String(64), nullable=False),
        Column('contract_hash', String(64), nullable=False),
        Column('mapping_version', String(32), nullable=False),
        Column('target_rows_hash', String(64), nullable=False),
        Column('receipt', JSON, nullable=False),
        schema=schema, mysql_engine='InnoDB', mysql_charset='utf8mb4')
    events = Table('user_movie_events', metadata,
        Column('seq', BigInteger, primary_key=True, autoincrement=True),
        Column('user_id', BigInteger, ForeignKey(catalog_schema+'.auth_users.id', ondelete='RESTRICT'), nullable=False),
        Column('event_id', String(100, collation='utf8mb4_bin'), nullable=False),
        Column('movie_id', BigInteger, ForeignKey(catalog_schema+'.movies.movie_id', ondelete='RESTRICT'), nullable=False),
        Column('payload', JSON, nullable=False),
        Column('created_at', DateTime, nullable=False),
        UniqueConstraint('user_id', 'event_id', name='uq_m6_behavior_user_event'),
        schema=schema, mysql_engine='InnoDB', mysql_charset='utf8mb4')
    states = Table('user_movie_states', metadata,
        Column('user_id', BigInteger, ForeignKey(catalog_schema+'.auth_users.id', ondelete='RESTRICT'), primary_key=True),
        Column('movie_id', BigInteger, ForeignKey(catalog_schema+'.movies.movie_id', ondelete='RESTRICT'), primary_key=True),
        Column('state', JSON, nullable=False),
        Column('version', BigInteger, nullable=False),
        schema=schema, mysql_engine='InnoDB', mysql_charset='utf8mb4')
    return {'receipts': receipts, 'events': events, 'states': states}
