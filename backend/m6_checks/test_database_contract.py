"""Offline checks. No legacy test config, credential files or DB connections."""
import unittest
import sys
from pathlib import Path
from unittest.mock import Mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.m6_database import validate_m6_url, verify_m6_connection


class DatabaseContractTests(unittest.TestCase):
    def setUp(self):
        self.config = {'M6_SCHEMA': 'mi2_m6_synthetic_20261107', 'M6_APP_USER': 'mi2app',
                       'M6_MIGRATION_USER': 'mi2migrate',
                       'M6_SERVER_UUID': '00000000-0000-0000-0000-000000000001'}
        self.url = 'mysql+pymysql://mi2app:SYNTHETIC_ONLY@127.0.0.1:3306/mi2_m6_synthetic_20261107'

    def test_explicit_contract(self):
        self.assertEqual(validate_m6_url(self.url, self.config).database, self.config['M6_SCHEMA'])

    def test_protected_or_unapproved_targets_rejected_before_connect(self):
        for value in [self.url.replace('mi2_m6_synthetic_20261107', 'mi2_auth_dev'),
                      self.url.replace('mi2app:', 'navicat:'),
                      self.url.replace('127.0.0.1', '192.0.2.1'),
                      self.url.replace(':3306/', ':3307/'),
                      self.url + '?unix_socket=/tmp/mysql.sock',
                      self.url.replace('SYNTHETIC_ONLY', '')]:
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                validate_m6_url(value, self.config)

    def test_schema_cannot_authorize_a_protected_name(self):
        for schema in ['mi2_auth_dev', 'mi2_auth_test', 'mysql', '']:
            config = dict(self.config, M6_SCHEMA=schema)
            with self.subTest(schema=schema), self.assertRaises(RuntimeError):
                validate_m6_url(self.url, config)

    def test_missing_uuid_rejected(self):
        with self.assertRaises(RuntimeError):
            validate_m6_url(self.url, dict(self.config, M6_SERVER_UUID=''))

    def test_runtime_identity_all_fields(self):
        good = (self.config['M6_SERVER_UUID'], self.config['M6_SCHEMA'], 'mi2app@127.0.0.1')
        connection = Mock()
        connection.execute.return_value.one.return_value = good
        verify_m6_connection(connection, self.config)
        for row in [('00000000-0000-0000-0000-000000000002', good[1], good[2]),
                    (good[0], 'mi2_auth_test', good[2]), (good[0], good[1], 'navicat@localhost')]:
            connection.execute.return_value.one.return_value = row
            with self.subTest(row=row), self.assertRaises(RuntimeError):
                verify_m6_connection(connection, self.config)

    def test_migration_identity_separated(self):
        validate_m6_url(self.url.replace('mi2app:', 'mi2migrate:'), self.config, migration=True)
        with self.assertRaises(RuntimeError):
            validate_m6_url(self.url, self.config, migration=True)


if __name__ == '__main__':
    unittest.main()
