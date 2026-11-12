# DRAFT / NOT EXECUTED. Offline only; no application/config imports.
from sqlalchemy import (Table, Column, BigInteger, SmallInteger, Integer, String, Text, JSON, Boolean, Date, Numeric, ForeignKey, ForeignKeyConstraint, UniqueConstraint, CheckConstraint, Computed, Index)
from sqlalchemy.dialects.mysql import DATETIME, CHAR
from alembic import op
revision='0002_catalog'
down_revision='0001_auth'
branch_labels=None
depends_on=None

def upgrade():
    raise RuntimeError('DRAFT NOT AUTHORIZED FOR EXECUTION')
    op.create_table('movies',
        Column('movie_id', BigInteger(), nullable=False, primary_key=True, autoincrement=True),
        Column('title', String(255), nullable=False),
        Column('original_title', String(255), nullable=True),
        Column('release_date', Date(), nullable=True),
        Column('release_year', SmallInteger(), nullable=True),
        Column('runtime_minutes', SmallInteger(), nullable=True),
        Column('original_language', String(16, collation='utf8mb4_bin'), nullable=True),
        Column('overview', Text(), nullable=True),
        Column('catalog_status', String(16, collation='utf8mb4_bin'), nullable=False, server_default='draft'),
        Column('is_adult', Boolean(), nullable=True),
        Column('created_at', DATETIME(fsp=6), nullable=False),
        Column('updated_at', DATETIME(fsp=6), nullable=False),
        CheckConstraint("catalog_status IN ('draft','active','hidden')", name='ck_movies_0'),
        CheckConstraint('release_year IS NULL OR release_year BETWEEN 1800 AND 2199', name='ck_movies_1'),
        CheckConstraint('runtime_minutes IS NULL OR (runtime_minutes>0 AND runtime_minutes<=1500)', name='ck_movies_2'),
        mysql_engine='InnoDB', mysql_charset='utf8mb4')
    op.create_index('ix_movies_0','movies',['catalog_status', 'release_year', 'movie_id'])
    op.create_index('ix_movies_1','movies',['title'])
    op.create_table('genres',
        Column('genre_id', BigInteger(), nullable=False, primary_key=True, autoincrement=True),
        Column('name_zh', String(64), nullable=False),
        Column('name_en', String(64), nullable=True),
        Column('tmdb_genre_id', String(32, collation='utf8mb4_bin'), nullable=True),
        UniqueConstraint('tmdb_genre_id', name='uq_genres_0'),
        mysql_engine='InnoDB', mysql_charset='utf8mb4')
    op.create_table('people',
        Column('person_id', BigInteger(), nullable=False, primary_key=True, autoincrement=True),
        Column('name', String(255), nullable=False),
        Column('original_name', String(255), nullable=True),
        Column('created_at', DATETIME(fsp=6), nullable=False),
        Column('updated_at', DATETIME(fsp=6), nullable=False),
        mysql_engine='InnoDB', mysql_charset='utf8mb4')
    op.create_index('ix_people_0','people',['name'])
    op.create_table('ingest_batches',
        Column('batch_id', BigInteger(), nullable=False, primary_key=True, autoincrement=True),
        Column('source', String(32, collation='utf8mb4_bin'), nullable=False),
        Column('mode', String(16, collation='utf8mb4_bin'), nullable=False),
        Column('status', String(16, collation='utf8mb4_bin'), nullable=False),
        Column('started_at', DATETIME(fsp=6), nullable=False),
        Column('finished_at', DATETIME(fsp=6), nullable=True),
        Column('source_snapshot_ref', String(255), nullable=False),
        Column('counts_json', JSON(), nullable=False),
        Column('error_summary', String(512), nullable=True),
        CheckConstraint("mode IN ('dry-run','write')", name='ck_ingest_batches_0'),
        CheckConstraint("status IN ('pending','running','succeeded','partial','failed')", name='ck_ingest_batches_1'),
        mysql_engine='InnoDB', mysql_charset='utf8mb4')
    op.create_index('ix_ingest_batches_0','ingest_batches',['source', 'started_at', 'batch_id'])
    op.create_table('movie_external_ids',
        Column('id', BigInteger(), nullable=False, primary_key=True, autoincrement=True),
        Column('movie_id', BigInteger(), ForeignKey('movies.movie_id', ondelete='RESTRICT', onupdate='RESTRICT'), nullable=False),
        Column('source', String(16, collation='utf8mb4_bin'), nullable=False),
        Column('external_id', String(128, collation='utf8mb4_bin'), nullable=False),
        Column('verified_method', String(32), nullable=False),
        Column('verified_at', DATETIME(fsp=6), nullable=True),
        Column('created_at', DATETIME(fsp=6), nullable=False),
        Column('core_source_slot', String(16, collation='utf8mb4_bin'), Computed("CASE WHEN source IN ('douban','tmdb','imdb') THEN source ELSE NULL END", persisted=False), nullable=True),
        UniqueConstraint('source', 'external_id', name='uq_movie_external_ids_0'),
        UniqueConstraint('movie_id', 'core_source_slot', name='uq_movie_external_ids_1'),
        CheckConstraint("source IN ('douban','tmdb','imdb','wikidata')", name='ck_movie_external_ids_0'),
        CheckConstraint('CHAR_LENGTH(external_id)>0', name='ck_movie_external_ids_1'),
        mysql_engine='InnoDB', mysql_charset='utf8mb4')
    op.create_index('ix_movie_external_ids_0','movie_external_ids',['movie_id', 'source'])
    op.create_table('movie_source_records',
        Column('id', BigInteger(), nullable=False, primary_key=True, autoincrement=True),
        Column('movie_id', BigInteger(), ForeignKey('movies.movie_id', ondelete='RESTRICT', onupdate='RESTRICT'), nullable=True),
        Column('source', String(32, collation='utf8mb4_bin'), nullable=False),
        Column('source_record_id', String(128, collation='utf8mb4_bin'), nullable=False),
        Column('fetched_at', DATETIME(fsp=6), nullable=False),
        Column('source_updated_at', DATETIME(fsp=6), nullable=True),
        Column('payload_hash', CHAR(64, charset='ascii', collation='ascii_bin'), nullable=False),
        Column('metadata_json', JSON(), nullable=False),
        Column('ingest_batch_id', BigInteger(), ForeignKey('ingest_batches.batch_id', ondelete='RESTRICT', onupdate='RESTRICT'), nullable=False),
        Column('license_status', String(24, collation='utf8mb4_bin'), nullable=False),
        UniqueConstraint('source', 'source_record_id', name='uq_movie_source_records_0'),
        UniqueConstraint('id', 'movie_id', name='uq_movie_source_records_1'),
        CheckConstraint("source IN ('legacy_movies','legacy_movies2','tmdb')", name='ck_movie_source_records_0'),
        CheckConstraint("license_status IN ('unknown','allowed','restricted')", name='ck_movie_source_records_1'),
        mysql_engine='InnoDB', mysql_charset='utf8mb4')
    op.create_index('ix_movie_source_records_0','movie_source_records',['movie_id'])
    op.create_index('ix_movie_source_records_1','movie_source_records',['ingest_batch_id'])
    op.create_table('movie_aliases',
        Column('id', BigInteger(), nullable=False, primary_key=True, autoincrement=True),
        Column('movie_id', BigInteger(), ForeignKey('movies.movie_id', ondelete='RESTRICT', onupdate='RESTRICT'), nullable=False),
        Column('title', String(255), nullable=False),
        Column('title_normalized', String(255, collation='utf8mb4_bin'), nullable=False),
        Column('language', String(16, collation='utf8mb4_bin'), nullable=True),
        Column('source', String(16, collation='utf8mb4_bin'), nullable=False),
        Column('created_at', DATETIME(fsp=6), nullable=False),
        UniqueConstraint('movie_id', 'source', 'title_normalized', name='uq_movie_aliases_0'),
        mysql_engine='InnoDB', mysql_charset='utf8mb4')
    op.create_index('ix_movie_aliases_0','movie_aliases',['title_normalized', 'movie_id'])
    op.create_table('movie_ratings_summary',
        Column('movie_id', BigInteger(), ForeignKey('movies.movie_id', ondelete='RESTRICT', onupdate='RESTRICT'), nullable=False, primary_key=True),
        Column('source', String(16, collation='utf8mb4_bin'), nullable=False, primary_key=True),
        Column('score_value', Numeric(5,2), nullable=True),
        Column('score_scale', SmallInteger(), nullable=False),
        Column('vote_count', BigInteger(), nullable=True),
        Column('observed_at', DATETIME(fsp=6), nullable=False),
        Column('selected_source_record_pk', BigInteger(), nullable=False),
        ForeignKeyConstraint(['selected_source_record_pk','movie_id'],['movie_source_records.id','movie_source_records.movie_id'],ondelete='RESTRICT',onupdate='RESTRICT',name='fk_rating_source_movie'),
        CheckConstraint('score_scale>0', name='ck_movie_ratings_summary_0'),
        CheckConstraint('vote_count IS NULL OR vote_count>=0', name='ck_movie_ratings_summary_1'),
        CheckConstraint('score_value IS NULL OR (score_value>=0 AND score_value<=score_scale)', name='ck_movie_ratings_summary_2'),
        CheckConstraint('vote_count IS NULL OR vote_count<>0 OR score_value IS NULL', name='ck_movie_ratings_summary_3'),
        mysql_engine='InnoDB', mysql_charset='utf8mb4')
    op.create_index('ix_movie_ratings_summary_0','movie_ratings_summary',['selected_source_record_pk', 'movie_id'])
    op.create_table('movie_genres',
        Column('movie_id', BigInteger(), ForeignKey('movies.movie_id', ondelete='RESTRICT', onupdate='RESTRICT'), nullable=False, primary_key=True),
        Column('genre_id', BigInteger(), ForeignKey('genres.genre_id', ondelete='RESTRICT', onupdate='RESTRICT'), nullable=False, primary_key=True),
        mysql_engine='InnoDB', mysql_charset='utf8mb4')
    op.create_index('ix_movie_genres_0','movie_genres',['genre_id', 'movie_id'])
    op.create_table('person_external_ids',
        Column('id', BigInteger(), nullable=False, primary_key=True, autoincrement=True),
        Column('person_id', BigInteger(), ForeignKey('people.person_id', ondelete='RESTRICT', onupdate='RESTRICT'), nullable=False),
        Column('source', String(16, collation='utf8mb4_bin'), nullable=False),
        Column('external_id', String(128, collation='utf8mb4_bin'), nullable=False),
        Column('verified_method', String(32), nullable=False),
        Column('verified_at', DATETIME(fsp=6), nullable=True),
        UniqueConstraint('source', 'external_id', name='uq_person_external_ids_0'),
        mysql_engine='InnoDB', mysql_charset='utf8mb4')
    op.create_index('ix_person_external_ids_0','person_external_ids',['person_id', 'source'])
    op.create_table('movie_credits',
        Column('credit_id', BigInteger(), nullable=False, primary_key=True, autoincrement=True),
        Column('movie_id', BigInteger(), ForeignKey('movies.movie_id', ondelete='RESTRICT', onupdate='RESTRICT'), nullable=False),
        Column('person_id', BigInteger(), ForeignKey('people.person_id', ondelete='RESTRICT', onupdate='RESTRICT'), nullable=False),
        Column('source', String(16, collation='utf8mb4_bin'), nullable=False),
        Column('source_credit_key', String(128, collation='utf8mb4_bin'), nullable=False),
        Column('role_type', String(8, collation='utf8mb4_bin'), nullable=False),
        Column('job', String(128), nullable=True),
        Column('character_name', String(255), nullable=True),
        Column('sort_order', Integer(), nullable=False, server_default='0'),
        UniqueConstraint('movie_id', 'source', 'source_credit_key', name='uq_movie_credits_0'),
        CheckConstraint("role_type IN ('cast','crew')", name='ck_movie_credits_0'),
        CheckConstraint('sort_order>=0', name='ck_movie_credits_1'),
        mysql_engine='InnoDB', mysql_charset='utf8mb4')
    op.create_index('ix_movie_credits_0','movie_credits',['movie_id', 'role_type', 'sort_order', 'credit_id'])
    op.create_index('ix_movie_credits_1','movie_credits',['person_id', 'movie_id'])
    op.create_table('movie_assets',
        Column('asset_id', BigInteger(), nullable=False, primary_key=True, autoincrement=True),
        Column('movie_id', BigInteger(), ForeignKey('movies.movie_id', ondelete='RESTRICT', onupdate='RESTRICT'), nullable=False),
        Column('asset_type', String(16, collation='utf8mb4_bin'), nullable=False),
        Column('source', String(16, collation='utf8mb4_bin'), nullable=False),
        Column('source_path', String(512, collation='utf8mb4_bin'), nullable=False),
        Column('language', String(16, collation='utf8mb4_bin'), nullable=True),
        Column('width', Integer(), nullable=True),
        Column('height', Integer(), nullable=True),
        Column('is_primary', Boolean(), nullable=False, server_default='0'),
        Column('license_status', String(24, collation='utf8mb4_bin'), nullable=False),
        Column('updated_at', DATETIME(fsp=6), nullable=False),
        Column('primary_slot', String(16, collation='utf8mb4_bin'), Computed('CASE WHEN is_primary THEN asset_type ELSE NULL END', persisted=False), nullable=True),
        UniqueConstraint('movie_id', 'asset_type', 'source', 'source_path', name='uq_movie_assets_0'),
        UniqueConstraint('movie_id', 'primary_slot', name='uq_movie_assets_1'),
        CheckConstraint("asset_type IN ('poster','backdrop')", name='ck_movie_assets_0'),
        CheckConstraint('width IS NULL OR width>0', name='ck_movie_assets_1'),
        CheckConstraint('height IS NULL OR height>0', name='ck_movie_assets_2'),
        CheckConstraint("NOT is_primary OR license_status='allowed'", name='ck_movie_assets_3'),
        mysql_engine='InnoDB', mysql_charset='utf8mb4')
    op.create_index('ix_movie_assets_0','movie_assets',['movie_id', 'asset_type', 'is_primary'])
    op.create_table('legacy_movie_map',
        Column('id', BigInteger(), nullable=False, primary_key=True, autoincrement=True),
        Column('legacy_table', String(16, collation='utf8mb4_bin'), nullable=False),
        Column('legacy_pk', BigInteger(), nullable=False),
        Column('legacy_douban_id', String(128, collation='utf8mb4_bin'), nullable=True),
        Column('movie_id', BigInteger(), ForeignKey('movies.movie_id', ondelete='RESTRICT', onupdate='RESTRICT'), nullable=True),
        Column('match_status', String(16, collation='utf8mb4_bin'), nullable=False),
        Column('match_method', String(32), nullable=True),
        Column('reviewed_at', DATETIME(fsp=6), nullable=True),
        Column('reason_code', String(64, collation='utf8mb4_bin'), nullable=True),
        Column('reason_note', String(255), nullable=True),
        UniqueConstraint('legacy_table', 'legacy_pk', name='uq_legacy_movie_map_0'),
        CheckConstraint("legacy_table IN ('movies','movies2')", name='ck_legacy_movie_map_0'),
        CheckConstraint("match_status IN ('matched','unmatched','review','blocked','skipped')", name='ck_legacy_movie_map_1'),
        CheckConstraint("match_status<>'matched' OR movie_id IS NOT NULL", name='ck_legacy_movie_map_2'),
        CheckConstraint("match_status NOT IN ('skipped','blocked') OR (reason_code IS NOT NULL AND CHAR_LENGTH(TRIM(reason_code))>0)", name='ck_legacy_movie_map_3'),
        CheckConstraint("reason_code IS NULL OR reason_code IN ('excluded_tv','invalid_source_id','type_conflict','identity_conflict','requires_review')", name='ck_legacy_movie_map_4'),
        mysql_engine='InnoDB', mysql_charset='utf8mb4')
    op.create_index('ix_legacy_movie_map_0','legacy_movie_map',['movie_id'])
    op.create_index('ix_legacy_movie_map_1','legacy_movie_map',['legacy_douban_id'])
    op.create_table('ingest_items',
        Column('id', BigInteger(), nullable=False, primary_key=True, autoincrement=True),
        Column('batch_id', BigInteger(), ForeignKey('ingest_batches.batch_id', ondelete='RESTRICT', onupdate='RESTRICT'), nullable=False),
        Column('source_record_id', String(128, collation='utf8mb4_bin'), nullable=False),
        Column('status', String(16, collation='utf8mb4_bin'), nullable=False),
        Column('movie_id', BigInteger(), ForeignKey('movies.movie_id', ondelete='RESTRICT', onupdate='RESTRICT'), nullable=True),
        Column('error_code', String(64), nullable=True),
        Column('payload_hash', CHAR(64, charset='ascii', collation='ascii_bin'), nullable=False),
        Column('attempt_count', Integer(), nullable=False, server_default='1'),
        Column('updated_at', DATETIME(fsp=6), nullable=False),
        UniqueConstraint('batch_id', 'source_record_id', name='uq_ingest_items_0'),
        CheckConstraint("status IN ('inserted','updated','skipped','review','error')", name='ck_ingest_items_0'),
        CheckConstraint('attempt_count>=1', name='ck_ingest_items_1'),
        mysql_engine='InnoDB', mysql_charset='utf8mb4')
    op.create_index('ix_ingest_items_0','ingest_items',['movie_id'])
    op.create_table('match_reviews',
        Column('id', BigInteger(), nullable=False, primary_key=True, autoincrement=True),
        Column('source', String(32, collation='utf8mb4_bin'), nullable=False),
        Column('source_record_id', String(128, collation='utf8mb4_bin'), nullable=False),
        Column('candidate_movie_id', BigInteger(), ForeignKey('movies.movie_id', ondelete='RESTRICT', onupdate='RESTRICT'), nullable=False),
        Column('match_features_json', JSON(), nullable=False),
        Column('status', String(16, collation='utf8mb4_bin'), nullable=False, server_default='pending'),
        Column('review_reason', String(512), nullable=True),
        Column('reviewed_at', DATETIME(fsp=6), nullable=True),
        Column('approved_source_record_id', String(128, collation='utf8mb4_bin'), Computed("CASE WHEN status='approved' THEN source_record_id ELSE NULL END", persisted=False), nullable=True),
        UniqueConstraint('source', 'source_record_id', 'candidate_movie_id', name='uq_match_reviews_0'),
        UniqueConstraint('source', 'approved_source_record_id', name='uq_match_reviews_1'),
        CheckConstraint("status IN ('pending','approved','rejected')", name='ck_match_reviews_0'),
        mysql_engine='InnoDB', mysql_charset='utf8mb4')
    op.create_index('ix_match_reviews_0','match_reviews',['status', 'id'])
    op.create_table('data_quality_issues',
        Column('id', BigInteger(), nullable=False, primary_key=True, autoincrement=True),
        Column('batch_id', BigInteger(), ForeignKey('ingest_batches.batch_id', ondelete='RESTRICT', onupdate='RESTRICT'), nullable=False),
        Column('source', String(32, collation='utf8mb4_bin'), nullable=False),
        Column('source_record_id', String(128, collation='utf8mb4_bin'), nullable=False),
        Column('issue_type', String(64), nullable=False),
        Column('severity', String(16), nullable=False),
        Column('status', String(16, collation='utf8mb4_bin'), nullable=False),
        Column('detail_json', JSON(), nullable=False),
        Column('created_at', DATETIME(fsp=6), nullable=False),
        UniqueConstraint('batch_id', 'source', 'source_record_id', 'issue_type', name='uq_data_quality_issues_0'),
        CheckConstraint("severity IN ('info','warning','error')", name='ck_data_quality_issues_0'),
        CheckConstraint("status IN ('open','resolved','accepted')", name='ck_data_quality_issues_1'),
        mysql_engine='InnoDB', mysql_charset='utf8mb4')
    op.create_index('ix_data_quality_issues_0','data_quality_issues',['status', 'severity', 'id'])

def downgrade():
    raise RuntimeError('DRAFT DESTRUCTIVE OPERATIONS NOT AUTHORIZED')
    op.drop_table('data_quality_issues')
    op.drop_table('match_reviews')
    op.drop_table('ingest_items')
    op.drop_table('legacy_movie_map')
    op.drop_table('movie_assets')
    op.drop_table('movie_credits')
    op.drop_table('person_external_ids')
    op.drop_table('movie_genres')
    op.drop_table('movie_ratings_summary')
    op.drop_table('movie_aliases')
    op.drop_table('movie_source_records')
    op.drop_table('movie_external_ids')
    op.drop_table('ingest_batches')
    op.drop_table('people')
    op.drop_table('genres')
    op.drop_table('movies')
