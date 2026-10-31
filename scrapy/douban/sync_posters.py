#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Copy stored poster metadata from movies into the app-facing movies2 table.

The poster pipeline writes `movies`; Flask reads `movies2`. Without this step a
converted poster never reaches the UI. Only poster columns are touched, so other
fields in `movies2` stay as they are.
"""

import argparse

from douban.database import get_connection


POSTER_COLUMNS = [
    'cover',
    'cover_path',
    'cover_status',
    'cover_hash',
    'cover_checked_at',
    'cover_error',
]


def sync_posters(apply=False):
    """Return affected-row counts; write only when apply is explicitly true."""
    assignments = ','.join(
        't.`{column}`=s.`{column}`'.format(column=column)
        for column in POSTER_COLUMNS
    )
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                'SELECT COUNT(*) AS total FROM movies2 t '
                'JOIN movies s ON t.douban_id=s.douban_id '
                "WHERE s.cover_status='ok' AND s.cover_path<>'' "
                'AND (t.cover_path<>s.cover_path OR t.cover_status<>s.cover_status)'
            )
            pending = cursor.fetchone()['total']
            changed = 0
            if apply and pending:
                cursor.execute(
                    'UPDATE movies2 t JOIN movies s ON t.douban_id=s.douban_id '
                    "SET {} WHERE s.cover_status='ok' AND s.cover_path<>''".format(
                        assignments
                    )
                )
                changed = cursor.rowcount
                connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    return {'pending': pending, 'changed': changed, 'applied': apply}


def main():
    parser = argparse.ArgumentParser(
        description='Copy poster columns from movies to movies2; dry-run by default.'
    )
    parser.add_argument(
        '--apply', action='store_true',
        help='commit the update (without this flag no rows are changed)',
    )
    args = parser.parse_args()
    result = sync_posters(args.apply)
    mode = 'APPLIED' if result['applied'] else 'DRY-RUN'
    print(
        '{mode}: pending={pending}, changed={changed}'.format(mode=mode, **result)
    )


if __name__ == '__main__':
    main()
