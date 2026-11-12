"""Explicit M6-only connection contract; never loads legacy credentials."""
import re
from sqlalchemy import text
from sqlalchemy.engine import make_url


def validate_m6_url(value, config, *, migration=False, audit=False):
    url = make_url(value)
    schema = config.get('M6_SCHEMA', '')
    if migration and audit:raise RuntimeError('Choose one M6 role')
    user = config.get('M6_AUDIT_USER' if audit else 'M6_MIGRATION_USER' if migration else 'M6_APP_USER', '')
    instance = config.get('M6_SERVER_UUID', '')
    if not isinstance(schema, str) or not re.fullmatch(r'mi2_m6_[a-z0-9_]{8,48}', schema):
        raise RuntimeError('Explicit M6 schema required')
    if not isinstance(user, str) or not re.fullmatch(r'mi2[a-z0-9_]{1,29}', user):
        raise RuntimeError('Dedicated M6 account required')
    if not isinstance(instance, str) or not re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', instance):
        raise RuntimeError('Pinned M6 instance required')
    if (url.drivername != 'mysql+pymysql' or url.database != schema
            or url.username != user or url.host != '127.0.0.1' or url.port != 3306
            or not url.password or url.query):
        raise RuntimeError('M6 connection contract mismatch')
    return url


def verify_m6_connection(connection, config, *, migration=False, audit=False):
    row = connection.execute(text('SELECT @@server_uuid, DATABASE(), CURRENT_USER()')).one()
    if migration and audit:raise RuntimeError('Choose one M6 role')
    expected_user = config['M6_AUDIT_USER' if audit else 'M6_MIGRATION_USER' if migration else 'M6_APP_USER']
    if (row[0] != config['M6_SERVER_UUID'] or row[1] != config['M6_SCHEMA']
            or row[2].split('@', 1)[0] != expected_user):
        raise RuntimeError('M6 runtime identity mismatch')


def environment_config(environ):
    return {
        'M6_ISOLATED': environ.get('MI2_M6_ISOLATED') == 'true',
        'M6_SCHEMA': environ.get('MI2_M6_SCHEMA', ''),
        'M6_APP_USER': environ.get('MI2_M6_APP_USER', ''),
        'M6_MIGRATION_USER': environ.get('MI2_M6_MIGRATION_USER', ''),
        'M6_SERVER_UUID': environ.get('MI2_M6_SERVER_UUID', ''),
        'M6_ACTIVITY_SCHEMA': environ.get('MI2_M6_ACTIVITY_SCHEMA', ''),
        'M6_AUDIT_USER': environ.get('MI2_M6_AUDIT_USER', ''),
    }
