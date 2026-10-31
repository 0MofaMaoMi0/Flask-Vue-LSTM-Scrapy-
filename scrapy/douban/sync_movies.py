#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Synchronize raw movie rows into the application-facing movies2 table."""

import argparse

from douban.database import get_connection


MOVIE_COLUMNS = [
    'type',
    'slug',
    'name',
    'alias',
    'cover',
    'cover_source_url',
    'cover_path',
    'cover_status',
    'cover_hash',
    'cover_checked_at',
    'cover_error',
    'year',
    'regions',
    'genres',
    'languages',
    'release_date',
    'official_site',
    'directors',
    'actors',
    'storyline',
    'mins',
    'recommend_tip',
    'tags',
    'avg_score',
    'imdb_id',
    'imdb_score',
    'imdb_votes',
    'douban_id',
    'douban_score',
    'douban_votes',
    'actor_ids',
    'director_ids',
]


def sync_movies(limit=100, batch_size=100, apply=False):
    """Return insert/update counts; write only when apply is explicitly true."""
    limit = max(0, int(limit))
    batch_size = max(1, int(batch_size))
    selected = ','.join(['id'] + MOVIE_COLUMNS)
    assignments = ','.join(
        '{}=%s'.format(column) for column in MOVIE_COLUMNS
    )
    insert_fields = ','.join(MOVIE_COLUMNS)
    placeholders = ','.join(['%s'] * len(MOVIE_COLUMNS))

    scanned = inserted = updated = 0
    last_id = 0
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            while not limit or scanned < limit:
                current_batch = batch_size
                if limit:
                    current_batch = min(current_batch, limit - scanned)
                cursor.execute(
                    'SELECT {} FROM movies WHERE id>%s '
                    'ORDER BY id LIMIT %s'.format(selected),
                    (last_id, current_batch),
                )
                rows = cursor.fetchall()
                if not rows:
                    break

                for row in rows:
                    last_id = row['id']
                    scanned += 1
                    cursor.execute(
                        'SELECT id FROM movies2 '
                        'WHERE douban_id=%s ORDER BY id LIMIT 1',
                        (row['douban_id'],),
                    )
                    target = cursor.fetchone()
                    values = tuple(row[column] for column in MOVIE_COLUMNS)
                    if target:
                        updated += 1
                        if apply:
                            cursor.execute(
                                'UPDATE movies2 SET {} WHERE id=%s'.format(
                                    assignments
                                ),
                                values + (target['id'],),
                            )
                    else:
                        inserted += 1
                        if apply:
                            cursor.execute(
                                'INSERT INTO movies2 ({}) VALUES ({})'.format(
                                    insert_fields, placeholders
                                ),
                                values,
                            )

                if apply:
                    connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    return {
        'scanned': scanned,
        'inserted': inserted,
        'updated': updated,
        'applied': apply,
    }


def main():
    parser = argparse.ArgumentParser(
        description='Synchronize movies into movies2; dry-run by default.'
    )
    parser.add_argument(
        '--limit', type=int, default=100,
        help='maximum source rows to inspect; use 0 only for an intentional full run',
    )
    parser.add_argument('--batch-size', type=int, default=100)
    parser.add_argument(
        '--apply', action='store_true',
        help='commit inserts and updates (without this flag no rows are changed)',
    )
    args = parser.parse_args()
    result = sync_movies(args.limit, args.batch_size, args.apply)
    mode = 'APPLIED' if result['applied'] else 'DRY-RUN'
    print(
        '{}: scanned={scanned}, inserts={inserted}, updates={updated}'.format(
            mode, **result
        )
    )


if __name__ == '__main__':
    main()

