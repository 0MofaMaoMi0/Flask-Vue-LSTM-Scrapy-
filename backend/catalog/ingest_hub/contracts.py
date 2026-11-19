"""Shared contracts, error types and security validators for ingest_hub."""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

HUB_CONTRACT = 'mi2-ingest-hub-v1'
RULE_VERSION = '2026.11-v1'

SUPPORTED_SOURCES = (
    'tmdb_supplement',
    'tmdb_trending',
    'tmdb_catalog',
    'local_envelope',
    'douban_archive',
)

JOB_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$')
ENV_NAME_RE = re.compile(r'^[A-Z][A-Z0-9_]{1,63}$')
_REPARSE_ATTRIBUTE = 0x400


class HubError(Exception):
    """Base exception with a stable machine code and no secret-bearing message."""

    code = 'HUB_ERROR'

    def __init__(self, code=None, message=None):
        self.code = code or self.code
        self.message = message or self.code
        super().__init__(self.code if message is None else f'{self.code}: {message}')


class HubInputError(HubError):
    code = 'INPUT_INVALID'


class HubSecurityError(HubError):
    code = 'SECURITY_BOUNDARY'


class HubRejected(HubError):
    code = 'HUB_REJECTED'


def canonical_json(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(',', ':'),
        allow_nan=False,
    )


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_json(value):
    return sha256_bytes(canonical_json(value).encode('utf-8'))


def is_positive_int(value):
    return type(value) is int and value > 0


def is_reparse(path):
    try:
        if path.is_symlink():
            return True
        stat = path.lstat()
    except OSError:
        return False
    return bool(getattr(stat, 'st_file_attributes', 0) & _REPARSE_ATTRIBUTE)


def validate_job_id(job_id):
    if not isinstance(job_id, str) or not JOB_RE.fullmatch(job_id):
        raise HubInputError('JOB_ID_INVALID', 'job_id must match [A-Za-z0-9][A-Za-z0-9._-]{0,63}')
    return job_id


def validate_env_name(name, label='env_name'):
    if not isinstance(name, str) or not ENV_NAME_RE.fullmatch(name):
        raise HubSecurityError(
            'ENV_NAME_INVALID',
            f'{label} must be an environment variable name matching [A-Z][A-Z0-9_]{{1,63}}',
        )
    return name


def _check_non_c_absolute(raw_path, code_prefix='ARCHIVE'):
    if not isinstance(raw_path, (str, os.PathLike)):
        raise HubInputError(f'{code_prefix}_PATH_INVALID', 'path must be string or PathLike')
    text = os.fspath(raw_path)
    if not isinstance(text, str) or not text.strip():
        raise HubInputError(f'{code_prefix}_PATH_INVALID', 'path must not be empty')
    if len(text) >= 2 and text[1] == ':' and text[0].upper() == 'C':
        raise HubSecurityError(f'{code_prefix}_ON_SYSTEM_DRIVE', 'C: drive is forbidden')
    candidate = Path(text)
    if any(part == '..' for part in candidate.parts):
        raise HubSecurityError(f'{code_prefix}_TRAVERSAL', 'parent directory traversal is forbidden')
    if not candidate.is_absolute():
        raise HubSecurityError(f'{code_prefix}_NOT_ABSOLUTE', 'path must be absolute')
    if candidate.drive.upper().startswith('C'):
        raise HubSecurityError(f'{code_prefix}_ON_SYSTEM_DRIVE', 'C: drive is forbidden')
    return candidate


def validate_archive_dir(path, *, must_exist=False, create=False):
    """Validate that an archive directory is absolute, non-C:, and reparse-free."""
    candidate = _check_non_c_absolute(path, 'ARCHIVE')
    if candidate.exists():
        if not candidate.is_dir():
            raise HubInputError('ARCHIVE_ROOT_NOT_DIRECTORY', 'archive path is not a directory')
        for item in [candidate] + list(candidate.parents):
            if item.exists() and is_reparse(item):
                raise HubSecurityError('ARCHIVE_REPARSE_POINT', 'symlinks/reparse points are forbidden')
        resolved = candidate.resolve(strict=True)
        if os.path.normcase(str(resolved)) != os.path.normcase(str(candidate)):
            raise HubSecurityError('ARCHIVE_REPARSE_POINT', 'archive directory resolves through a link')
    elif must_exist:
        raise HubInputError('ARCHIVE_ROOT_MISSING', 'archive directory does not exist')
    else:
        for item in candidate.parents:
            if item.exists() and is_reparse(item):
                raise HubSecurityError('ARCHIVE_REPARSE_POINT', 'parent symlink/reparse point is forbidden')
            if item.exists():
                resolved_parent = item.resolve(strict=True)
                if os.path.normcase(str(resolved_parent)) != os.path.normcase(str(item)):
                    raise HubSecurityError('ARCHIVE_REPARSE_POINT', 'parent path resolves through a link')
                break
        if create:
            candidate.mkdir(parents=True, exist_ok=True)
            resolved = candidate.resolve(strict=True)
            if os.path.normcase(str(resolved)) != os.path.normcase(str(candidate)):
                raise HubSecurityError('ARCHIVE_REPARSE_POINT', 'created archive resolves through a link')
    return candidate


def validate_safe_file(path, *, allow_c_read=False, must_exist=True):
    """Validate a file path against traversal and symlink/reparse links."""
    if allow_c_read:
        if not isinstance(path, (str, os.PathLike)):
            raise HubInputError('FILE_PATH_INVALID', 'path must be string or PathLike')
        candidate = Path(os.fspath(path))
        if any(part == '..' for part in candidate.parts):
            raise HubSecurityError('FILE_TRAVERSAL', 'parent directory traversal is forbidden')
    else:
        candidate = _check_non_c_absolute(path, 'FILE')
    if is_reparse(candidate):
        raise HubSecurityError('FILE_REPARSE_POINT', 'symlinks/reparse points are forbidden')
    if must_exist:
        if not candidate.is_file():
            raise HubInputError('FILE_MISSING', f'file not found: {candidate.name}')
        resolved = candidate.resolve(strict=True)
        if os.path.normcase(str(resolved)) != os.path.normcase(str(candidate)):
            raise HubSecurityError('FILE_REPARSE_POINT', 'file resolves through a link')
    return candidate


def atomic_write_bytes(path, data):
    import tempfile
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix='.' + target.name + '.', suffix='.tmp', dir=str(target.parent))
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, str(target))
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return target


def atomic_write_json(path, payload):
    encoded = (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode('utf-8')
    return atomic_write_bytes(path, encoded)
