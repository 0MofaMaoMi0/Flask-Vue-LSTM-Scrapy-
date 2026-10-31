#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Add poster metadata columns without deleting or rewriting existing data."""

import argparse

from douban.database import get_connection


COLUMNS = [
    ('cover_source_url', "varchar(512) NOT NULL DEFAULT ''"),
    ('cover_path', "varchar(255) NOT NULL DEFAULT ''"),
    ('cover_status', "varchar(16) NOT NULL DEFAULT 'pending'"),
    ('cover_hash', "varchar(64) NOT NULL DEFAULT ''"),
    ('cover_checked_at', 'datetime DEFAULT NULL'),
    ('cover_error', "varchar(255) NOT NULL DEFAULT ''"),
]
TABLES = ('movies', 'movies2')


def migrate(apply=False):
    connection = get_connection()
    changes = []
    try:
        with connection.cursor() as cursor:
            for table in TABLES:
                cursor.execute(
                    'SELECT COLUMN_NAME FROM information_schema.COLUMNS '
                    'WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s',
                    (table,),
                )
                existing = {row['COLUMN_NAME'] for row in cursor.fetchall()}
                for name, definition in COLUMNS:
                    if name in existing:
                        continue
                    changes.append((table, name))
                    if apply:
                        cursor.execute(
                            'ALTER TABLE `{}` ADD COLUMN `{}` {}'.format(
                                table, name, definition
                            )
                        )
            if apply:
                connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return changes


def main():
    parser = argparse.ArgumentParser(
        description='Add movie poster metadata columns; dry-run by default.'
    )
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    changes = migrate(args.apply)
    mode = 'APPLIED' if args.apply else 'DRY-RUN'
    if changes:
        print('{}: {}'.format(
            mode,
            ', '.join('{}.{}'.format(*change) for change in changes),
        ))
    else:
        print('{}: schema already up to date'.format(mode))


if __name__ == '__main__':
    main()
