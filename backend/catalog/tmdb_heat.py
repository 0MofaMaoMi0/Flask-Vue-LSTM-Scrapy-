"""Read-only TMDB popularity lookup over already-published public board snapshots.

No TMDB credential, refresh, network request, database query or file write belongs
in this module.  A snapshot's top-level ``captured_at`` is the observation time
for every item in that snapshot; it is deliberately not confused with catalog
feature timestamps or with another channel's capture time.
"""
from datetime import datetime, timezone
import math

PUBLIC_CHANNELS = ('popular', 'trending_day', 'trending_week')
STALE_AFTER_SECONDS = 6 * 60 * 60
OK = 'OK'
STALE = 'STALE'
NOT_FOUND = 'NOT_FOUND'


def _valid_id(value):
    return type(value) is int and value >= 1


def _valid_popularity(value):
    # bool is an int subclass; reject it explicitly.  Zero is a valid observation.
    if type(value) not in (int, float):
        return None
    if not math.isfinite(value) or value < 0:
        return None
    return float(value)


def _valid_captured(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        stamp = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        return None
    return stamp


def _empty(tmdb_id):
    return {
        'tmdb_id': tmdb_id,
        'status': NOT_FOUND,
        'popularity': None,
        'observed_at': None,
        'source': None,
        'channel': None,
        'stale': False,
        'age_seconds': None,
        'stale_after_seconds': STALE_AFTER_SECONDS,
    }


def heat_for(tmdb_id, *, read_snapshot=None, now=None):
    """Return the newest valid heat observation for one positive TMDB ID.

    ``read_snapshot`` is injectable for offline tests and must accept the same
    ``(channel, online=False)`` call as ``recommendation_boards.public_snapshot``.
    Equal capture times keep the fixed ``PUBLIC_CHANNELS`` order (popular first).
    Malformed or unavailable channels are skipped; a missing observation is a
    clear NOT_FOUND rather than a popularity of zero.
    """
    if not _valid_id(tmdb_id):
        raise ValueError('TMDB ID')
    if now is None:
        now = datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('now must be timezone-aware')
    if read_snapshot is None:
        from .recommendation_boards import public_snapshot as read_snapshot

    best = None
    for channel in PUBLIC_CHANNELS:
        try:
            snapshot = read_snapshot(channel, online=False)
        except Exception:
            # A broken scheduled/cache snapshot must not take down movie detail
            # or hide valid observations from the other public channels.
            continue
        if not isinstance(snapshot, dict):
            continue
        captured = _valid_captured(snapshot.get('captured_at'))
        items = snapshot.get('items')
        if captured is None or not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict) or not _valid_id(item.get('id')) or item.get('id') != tmdb_id:
                continue
            popularity = _valid_popularity(item.get('popularity'))
            if popularity is None:
                continue
            if best is None or captured > best[0]:
                best = (captured, channel, popularity)
            # The first valid item in one channel is authoritative; later
            # duplicates cannot silently replace it or change the capture time.
            break

    if best is None:
        return _empty(tmdb_id)

    captured, channel, popularity = best
    age = (now - captured).total_seconds()
    status = STALE if age > STALE_AFTER_SECONDS else OK
    age_seconds = max(0, int(age))
    return {
        'tmdb_id': tmdb_id,
        'status': status,
        'popularity': popularity,
        'observed_at': captured.isoformat(),
        'source': channel,
        'channel': channel,
        'stale': status == STALE,
        'age_seconds': age_seconds,
        'stale_after_seconds': STALE_AFTER_SECONDS,
    }