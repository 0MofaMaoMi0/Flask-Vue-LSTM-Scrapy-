"""Currently attributed TMDB facts, per topic.

``tmdb_movie_metadata`` records, for every movie and every topic, the newest
*successful* fact (``PRESENT`` or ``EMPTY``) together with the source record
that carried it.  A newer failed attempt never overwrites that entry, so a
retained success keeps its own observation time.  This module:

* reads those per-topic attributions (``base`` / ``countries`` / ``metrics``);
* fetches the referenced payloads in bounded ID batches;
* validates identity in three places before any fact is used: the source row
  belongs to the movie and to ``tmdb``, the attributed ``tmdb_id`` matches the
  movie's real external mapping, and the payload ``id`` matches that mapping;
* provides a bounded, identity-first revenue fallback and country lookup.

No table here stores movie facts twice: payloads stay in
``movie_source_records``, this module only resolves which row is current.
"""
import json

from sqlalchemy import bindparam, inspect, text

BASE_TOPIC = "base"
COUNTRIES_TOPIC = "countries"
METRICS_TOPIC = "metrics"
TOPICS = (BASE_TOPIC, COUNTRIES_TOPIC, METRICS_TOPIC)
SUCCESS_STATES = frozenset(("PRESENT", "EMPTY"))
CURRENT_SOURCE = "TMDB_CURRENT_STATE"
HISTORICAL_SOURCE = "TMDB_HISTORICAL_DATABASE_SNAPSHOT"
REVENUE_CANDIDATES = 8
REVENUE_CHUNK = 200
CHUNK_SIZE = 300


def json_mapping(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, (str, bytes, bytearray)):
        try:
            parsed = json.loads(value)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def has_metadata_table(engine):
    if engine is None:
        return False
    try:
        return inspect(engine).has_table("tmdb_movie_metadata")
    except Exception:  # inspector cannot answer (offline sqlite stub)
        return False


def load_topics(connection, *, movie_ids=None):
    """``{movie_id: {"tmdb_id", "topics": {topic: entry}}}`` from metadata."""
    columns = {column["name"] for column in
               inspect(connection).get_columns("tmdb_movie_metadata")}
    wanted = [name for name in ("movie_id", "tmdb_id", "source_record_pk",
                                "provenance_json", "base_at", "countries_at",
                                "metrics_at") if name in columns]
    statement = "SELECT %s FROM tmdb_movie_metadata" % ", ".join(wanted)
    if movie_ids is None:
        rows = connection.execute(text(statement)).mappings()
    else:
        ids = sorted({int(movie_id) for movie_id in movie_ids})
        bounded = text(statement + " WHERE movie_id IN :ids").bindparams(
            bindparam("ids", expanding=True))
        rows = (row for offset in range(0, len(ids), CHUNK_SIZE)
                for row in connection.execute(bounded, {"ids": ids[offset:offset + CHUNK_SIZE]}).mappings())
    result = {}
    for row in rows:
        movie_id = row["movie_id"]
        if movie_id is None:
            continue
        provenance = json_mapping(row.get("provenance_json"))
        topics = {}
        for topic in TOPICS:
            entry = provenance.get(topic)
            parsed = {}
            if isinstance(entry, dict):
                state = entry.get("state")
                if state in SUCCESS_STATES:
                    parsed = {"state": state,
                              "source_record_pk": entry.get("source_record_pk"),
                              "fetched_at": _stamp(entry.get("fetched_at"))}
            base_pk = row.get("source_record_pk")
            base_at = row.get("base_at")
            if not parsed and topic == BASE_TOPIC and base_pk is not None and base_at is not None:
                # Older rows predate provenance_json; the base columns are the
                # same attribution in a compact form.
                parsed = {"state": "PRESENT", "source_record_pk": base_pk,
                          "fetched_at": _stamp(base_at)}
            if not parsed:
                continue
            if parsed.get("source_record_pk") is None and topic == BASE_TOPIC:
                parsed["source_record_pk"] = base_pk
            if parsed.get("fetched_at") is None:
                parsed["fetched_at"] = _stamp(row.get(topic + "_at"))
            parsed.setdefault("source_record_pk", None)
            parsed.setdefault("state", "EMPTY" if parsed["source_record_pk"] is None else "PRESENT")
            if parsed["source_record_pk"] is not None or parsed["state"] == "EMPTY":
                topics[topic] = parsed
        if topics:
            result[int(movie_id)] = {"tmdb_id": row["tmdb_id"], "topics": topics}
    return result


def _stamp(value):
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat() + ("Z" if value.tzinfo is None else "")
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    if value.endswith("+00:00"):
        return value[:-6] + "Z"
    if value.endswith("Z") or "+" in value[10:]:
        return value
    return value.replace(" ", "T") + "Z"


def fetch_records(connection, source_pks, batch_size=500):
    """``{pk: (movie_id, source, payload)}`` for the referenced records only."""
    wanted = sorted({int(pk) for pk in source_pks if pk is not None})
    records = {}
    for offset in range(0, len(wanted), batch_size):
        batch = wanted[offset:offset + batch_size]
        statement = text(
            "SELECT id, movie_id, source, metadata_json FROM movie_source_records"
            " WHERE id IN :ids").bindparams(bindparam("ids", expanding=True))
        for pk, movie_id, source, payload in connection.execute(statement, {"ids": batch}).all():
            if isinstance(payload, (str, bytes, bytearray)):
                try:
                    payload = json.loads(payload)
                except ValueError:
                    payload = None
            records[int(pk)] = (movie_id, source, payload if isinstance(payload, dict) else None)
    return records


def identity_ok(movie_id, tmdb_id, mapping, record):
    """A fact is usable only when ownership, mapping and payload agree."""
    if mapping is None or tmdb_id is None or record is None:
        return False
    record_movie, source, payload = record
    if record_movie is None or int(record_movie) != int(movie_id):
        return False
    if str(source or "") != "tmdb" or not isinstance(payload, dict):
        return False
    return str(mapping) == str(tmdb_id) and str(payload.get("id")) == str(mapping)


def revenue_fallbacks(connection, movie_ids, mappings, tmdb_ids, *, candidates=REVENUE_CANDIDATES):
    """Identity-first historical revenue.

    Candidate rows are ordered newest first per movie, fetched in bounded batches
    and validated one by one: row ownership, ``tmdb`` source, real mapping and a
    strictly positive non-boolean numeric ``revenue``.  A wrong-identity newer
    row therefore cannot hide an older valid amount, and the returned time is
    that record's own ``fetched_at``.
    """
    ids = sorted({int(mid) for mid in movie_ids if mid is not None and mappings.get(mid)})
    if not ids:
        return {}, 0
    selected = []
    for offset in range(0, len(ids), REVENUE_CHUNK):
        chunk = ids[offset:offset + REVENUE_CHUNK]
        # Pre-filter on a positive numeric revenue so a movie with no known
        # amount never triggers candidate fetches; identity is still verified
        # on each fetched row below.  Dialects without JSON1 fall back to the
        # bounded newest-N scan.
        dialect = connection.dialect.name
        payload_id = ("JSON_UNQUOTE(JSON_EXTRACT(s.metadata_json,'$.id'))"
                      if dialect == 'mysql' else
                      "CAST(JSON_EXTRACT(s.metadata_json,'$.id') AS TEXT)")
        revenue_type = ("JSON_TYPE(JSON_EXTRACT(s.metadata_json,'$.revenue'))"
                        if dialect == 'mysql' else
                        "JSON_TYPE(s.metadata_json,'$.revenue')")
        filtered = (
            "SELECT id, movie_id, fetched_at FROM ("
            " SELECT s.id, s.movie_id, s.fetched_at,"
            " ROW_NUMBER() OVER (PARTITION BY s.movie_id"
            " ORDER BY s.fetched_at DESC, s.id DESC) AS rn"
            " FROM movie_source_records s JOIN movies m ON m.movie_id=s.movie_id"
            " JOIN movie_external_ids e ON e.movie_id=s.movie_id AND e.source='tmdb'"
            " WHERE s.source='tmdb' AND m.catalog_status='active'"
            " AND s.movie_id IN :ids AND " + payload_id + "=e.external_id"
            " AND JSON_EXTRACT(s.metadata_json,'$.revenue') > 0"
            " AND LOWER(" + revenue_type + ") IN ('integer','real','double','decimal')) ranked"
            " WHERE rn <= :candidates ORDER BY movie_id, fetched_at DESC, id DESC")
        statement = text(filtered).bindparams(bindparam("ids", expanding=True))
        params = {"ids": chunk, "candidates": candidates}
        selected.extend(connection.execute(statement, params).all())
    fetched = fetch_records(connection, [row[0] for row in selected])
    result = {}
    for pk, movie_id, observed in selected:
        movie_id = int(movie_id)
        if movie_id in result:
            continue
        record = fetched.get(int(pk))
        if not identity_ok(movie_id, tmdb_ids.get(movie_id), mappings.get(movie_id), record):
            continue
        revenue = record[2].get("revenue")
        if type(revenue) not in (int, float) or isinstance(revenue, bool) or revenue <= 0:
            continue
        result[movie_id] = {"source_record_pk": int(pk),
                            "fetched_at": _stamp(observed),
                            "revenue": revenue}
    return result, len(fetched)


def apply_base(row, payload, stamp, source=CURRENT_SOURCE):
    """Base-topic facts: poster/revenue/budget/genres; missing stays missing."""
    revenue = payload.get("revenue")
    row["revenue"] = int(revenue) if _positive_number(revenue) else None
    row["revenue_as_of"] = stamp if "revenue" in payload else None
    row["revenue_source"] = source if row["revenue"] is not None else None
    row["base_observed_at"] = stamp
    if "poster_path" in payload:
        row["tmdb_poster_path"] = payload.get("poster_path")
    return row


def apply_metrics(row, payload, stamp, source=CURRENT_SOURCE):
    """Metrics-topic facts only; EMPTY yields None, never a historical guess."""
    popularity = payload.get("popularity")
    score = payload.get("vote_average")
    votes = payload.get("vote_count")
    row["tmdb_popularity"] = float(popularity) if _number(popularity) else None
    row["vote_count"] = votes if type(votes) is int and votes >= 0 else None
    row["tmdb_rating"] = (float(score) if _number(score) and 0 <= score <= 10
                          and type(votes) is int and votes > 0 else None)
    row["popularity_observed_at"] = stamp
    return row


def apply_countries(row, payload, stamp):
    countries = []
    seen = set()
    for entry in payload.get("production_countries") or []:
        if not isinstance(entry, dict):
            continue
        iso2 = entry.get("iso_3166_1")
        if not isinstance(iso2, str) or not iso2 or iso2 in seen:
            continue
        seen.add(iso2)
        countries.append({"iso2": iso2, "name": entry.get("name")})
    row["production_countries"] = countries
    row["countries_observed_at"] = stamp
    return row


def _number(value):
    return type(value) in (int, float) and value == value and value not in (float("inf"), float("-inf"))


def _positive_number(value):
    return type(value) in (int, float) and not isinstance(value, bool) and value == value \
        and value not in (float("inf"), float("-inf")) and value > 0

def fetch_countries(connection, source_pks, batch_size=500):
    """``{pk: (payload_id, countries)}`` without transferring full movie JSON.

    Identity still needs the payload id; country entries are returned raw so the
    caller applies the same dedupe/normalisation as the base path.
    """
    wanted = sorted({int(pk) for pk in source_pks if pk is not None})
    result = {}
    for offset in range(0, len(wanted), batch_size):
        batch = wanted[offset:offset + batch_size]
        statement = text(
            "SELECT id, JSON_EXTRACT(metadata_json,'$.id'),"
            " JSON_EXTRACT(metadata_json,'$.production_countries'), movie_id, source"
            " FROM movie_source_records WHERE id IN :ids").bindparams(
            bindparam("ids", expanding=True))
        for pk, payload_id, countries, owner, source in connection.execute(statement, {"ids": batch}).all():
            if isinstance(countries, (str, bytes, bytearray)):
                try:
                    countries = json.loads(countries)
                except ValueError:
                    countries = None
            if isinstance(payload_id, str):
                payload_id = payload_id.strip('"')
            result[int(pk)] = (payload_id, countries if isinstance(countries, list) else [], owner, source)
    return result


def countries_ok(movie_id, tmdb_id, mapping, entry):
    if mapping is None or tmdb_id is None or entry is None:
        return False
    payload_id, _, owner, source = entry
    return (owner == movie_id and source == "tmdb" and
            str(mapping) == str(tmdb_id) and str(payload_id) == str(mapping))
