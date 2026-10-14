#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
"""

import sys
from pathlib import Path

import pymysql

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from project_config import optional, required


def get_connection():
    """Create the MySQL connection only when a spider pipeline needs it."""
    return pymysql.connect(
        host=optional('MYSQL_HOST', 'localhost'),
        user=required('MYSQL_USER'),
        password=required('MYSQL_PASSWORD'),
        port=int(optional('MYSQL_PORT', '3306')),
        db=optional('MYSQL_DATABASE_SPIDER', required('MYSQL_DATABASE')),
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor,
    )


class LazyConnection:
    """Compatibility proxy that defers database access until it is used."""

    def __init__(self):
        self._connection = None

    def _get(self):
        if self._connection is None:
            self._connection = get_connection()
        return self._connection

    def __getattr__(self, name):
        return getattr(self._get(), name)


connection = LazyConnection()
