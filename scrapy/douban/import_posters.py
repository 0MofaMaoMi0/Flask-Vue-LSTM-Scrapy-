#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Import authorized local poster files named with their Douban movie ID."""

import argparse
import hashlib
import os
import re
import tempfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from douban.database import get_connection
from douban.runtime_config import (
    get_poster_public_prefix,
    get_poster_storage_root,
)


SUPPORTED_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp'}
DOUBAN_ID_PATTERN = re.compile(r'^(\d{5,})$')
MAX_SIZE = (360, 540)
WEBP_QUALITY = 82


def discover_posters(source_dir, recursive=False):
    """Return one deterministic source file per numeric Douban ID."""
    source_dir = Path(source_dir).resolve()
    iterator = source_dir.rglob('*') if recursive else source_dir.iterdir()
    posters = {}
    for path in sorted(iterator):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        match = DOUBAN_ID_PATTERN.fullmatch(path.stem)
        if match:
            posters.setdefault(match.group(1), path)
    return posters


def encode_webp(source_path):
    """Validate and convert one local image to the project's WebP format."""
    with Image.open(source_path) as opened:
        image = ImageOps.exif_transpose(opened)
        if image.mode in ('RGBA', 'LA', 'P'):
            foreground = image.convert('RGBA')
            background = Image.new('RGBA', foreground.size, 'white')
            background.alpha_composite(foreground)
            image = background.convert('RGB')
        elif image.mode != 'RGB':
            image = image.convert('RGB')
        image.thumbnail(MAX_SIZE, Image.Resampling.LANCZOS)
        size = image.size
        output = BytesIO()
        image.save(output, 'WEBP', quality=WEBP_QUALITY, method=6)
    return output.getvalue(), size


def store_poster(source_path, storage_root, public_prefix):
    """Store one converted poster atomically and return its DB metadata."""
    payload, size = encode_webp(source_path)
    content_key = hashlib.sha256(payload).hexdigest()
    checksum = hashlib.md5(payload, usedforsecurity=False).hexdigest()
    relative_path = '{}/{}/{}.webp'.format(
        content_key[:2], content_key[2:4], content_key
    )
    target = Path(storage_root) / Path(relative_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        with tempfile.NamedTemporaryFile(
            dir=target.parent, suffix='.tmp', delete=False
        ) as temporary:
            temporary.write(payload)
            temporary_path = Path(temporary.name)
        try:
            os.replace(temporary_path, target)
        finally:
            if temporary_path.exists():
                temporary_path.unlink()

    public_url = '{}/{}?v={}'.format(
        public_prefix.rstrip('/'), relative_path, checksum[:12]
    )
    return {
        'cover': public_url,
        'cover_path': relative_path,
        'cover_status': 'ok',
        'cover_hash': checksum,
        'cover_checked_at': datetime.now(timezone.utc).replace(tzinfo=None),
        'cover_error': '',
        'width': size[0],
        'height': size[1],
    }


def import_posters(source_dir, limit=100, recursive=False, apply=False):
    """Validate local files and optionally update movies and movies2."""
    source_dir = Path(source_dir).resolve()
    if not source_dir.is_dir():
        raise ValueError('Poster source directory does not exist: {}'.format(
            source_dir
        ))

    posters = discover_posters(source_dir, recursive=recursive)
    ordered = sorted(posters.items(), key=lambda pair: int(pair[0]))
    limit = int(limit)
    if limit < 0:
        raise ValueError('limit must be 0 or a positive integer')
    if limit:
        ordered = ordered[:limit]

    result = {
        'discovered': len(posters),
        'selected': len(ordered),
        'validated': 0,
        'imported': 0,
        'missing_movies': 0,
        'invalid_images': 0,
        'applied': apply,
    }
    connection = get_connection() if apply else None
    try:
        cursor = connection.cursor() if connection else None
        for douban_id, source_path in ordered:
            try:
                if apply:
                    cursor.execute(
                        'SELECT id FROM movies WHERE douban_id=%s LIMIT 1',
                        (douban_id,),
                    )
                    if cursor.fetchone() is None:
                        result['missing_movies'] += 1
                        continue
                    metadata = store_poster(
                        source_path,
                        get_poster_storage_root(),
                        get_poster_public_prefix(),
                    )
                    values = (
                        metadata['cover'],
                        metadata['cover_path'],
                        metadata['cover_status'],
                        metadata['cover_hash'],
                        metadata['cover_checked_at'],
                        metadata['cover_error'],
                        douban_id,
                    )
                    update_sql = (
                        'UPDATE `{}` SET cover=%s,cover_path=%s,'
                        'cover_status=%s,cover_hash=%s,cover_checked_at=%s,'
                        'cover_error=%s WHERE douban_id=%s'
                    )
                    cursor.execute(update_sql.format('movies'), values)
                    cursor.execute(update_sql.format('movies2'), values)
                    result['imported'] += 1
                else:
                    encode_webp(source_path)
                result['validated'] += 1
            except (OSError, UnidentifiedImageError, ValueError):
                result['invalid_images'] += 1
        if apply:
            connection.commit()
    except Exception:
        if connection:
            connection.rollback()
        raise
    finally:
        if connection:
            connection.close()
    return result


def main():
    parser = argparse.ArgumentParser(
        description=(
            'Import local posters named <douban_id>.jpg/png/webp; '
            'dry-run validation by default.'
        )
    )
    parser.add_argument('source_dir')
    parser.add_argument(
        '--limit', type=int, default=100,
        help='maximum files to process; use 0 only for an intentional full run',
    )
    parser.add_argument('--recursive', action='store_true')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    result = import_posters(
        args.source_dir,
        limit=args.limit,
        recursive=args.recursive,
        apply=args.apply,
    )
    mode = 'APPLIED' if result['applied'] else 'DRY-RUN'
    print(
        '{}: discovered={discovered}, selected={selected}, '
        'validated={validated}, imported={imported}, '
        'missing_movies={missing_movies}, invalid_images={invalid_images}'.format(
            mode, **result
        )
    )


if __name__ == '__main__':
    main()
