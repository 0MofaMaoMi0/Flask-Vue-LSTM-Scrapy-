"""TMDB supplement v1 writer kernel: per-topic decisions, plan and execution.

The module is deliberately split into three layers so that the integration
point cannot smuggle decisions into SQL execution:

``decide(existing, document, *, attempt_at)``
    Pure function.  Compares one normalized document against the retained
    per-topic state and returns one action per topic.

``build_plan(existing, document, *, now)``
    Translates the decisions into an explicit ``operations`` list.  It never
    invents a write: every statement that :func:`apply_plan` executes exists in
    the plan first.

``apply_plan(connection, plan)``
    Renders and executes exactly those operations.  It performs no comparison,
    no state lookup and no implicit cleanup.

Scope of the v1 writer
----------------------
* Only the twelve ``schema_manifest.json`` supplement tables are written.
* The sealed 16-table catalog stays read-only: the ``base`` topic advances the
  ``tmdb_movie_metadata`` bookkeeping only (``base_at`` / ``provenance_json``
  and the ``source_record_pk`` / ``payload_sha256`` projection columns).  The
  writer never updates ``movies`` unless the caller opts in with
  ``allow_master_fill=True`` **and** supplies the current ``movies`` snapshot
  under ``existing['master']``.
* ``credits`` facts live in the sealed ``movie_credits`` table.  The topic is
  therefore recorded on the attempt layer only (``skipped`` + a stable reason)
  instead of claiming retained credit facts that were never stored.

Per-topic rules implemented here follow ``schema_manifest.json``
``topic_state_model.rules`` one by one; see the ``_decide_topic`` docstring for
the exact ordering.
"""
import hashlib
import json
import re
from datetime import datetime, timezone

TOPICS = (
    "base",
    "countries",
    "companies",
    "spoken_languages",
    "keywords",
    "releases",
    "credits",
    "metrics",
)

STATUS_NOT_FETCHED = "NOT_FETCHED"
STATUS_MISSING = "MISSING"
STATUS_EMPTY = "EMPTY"
STATUS_PRESENT = "PRESENT"
STATUS_FAILED = "FAILED"
STATUSES = (STATUS_NOT_FETCHED, STATUS_MISSING, STATUS_EMPTY, STATUS_PRESENT, STATUS_FAILED)

SUCCESS_TIME_COLUMNS = {
    "base": "base_at",
    "countries": "countries_at",
    "companies": "companies_at",
    "spoken_languages": "spoken_languages_at",
    "keywords": "keywords_at",
    "releases": "releases_at",
    "credits": "credits_at",
    "metrics": "metrics_at",
}

RELATION_TABLES = {
    "countries": "tmdb_movie_countries",
    "companies": "tmdb_movie_companies",
    "spoken_languages": "tmdb_movie_languages",
    "keywords": "tmdb_movie_keywords",
    "releases": "tmdb_release_records",
    "metrics": "tmdb_metric_observations",
}

# Topics whose facts have no writable supplement table in v1.
DEFERRED_TOPICS = {"credits": "FROZEN_MOVIE_CREDITS_NEVER_WRITTEN"}

ATTEMPT_ONLY_STATUSES = (STATUS_FAILED, STATUS_MISSING, STATUS_NOT_FETCHED)

# Only these sealed ``movies`` columns may ever be filled, and only when the
# caller explicitly passes ``allow_master_fill=True`` with a snapshot.
MASTER_FILL_COLUMNS = {
    "title": "title",
    "original_title": "original_title",
    "release_date": "release_date",
    "runtime_minutes": "runtime",
    "original_language": "original_language",
    "overview": "overview",
}

METADATA_COLUMNS = (
    "movie_id",
    "tmdb_id",
    "source_record_pk",
    "payload_sha256",
    "field_status_json",
    "last_attempt_json",
    "provenance_json",
    "base_at",
    "countries_at",
    "companies_at",
    "spoken_languages_at",
    "keywords_at",
    "releases_at",
    "credits_at",
    "metrics_at",
    "fetched_at",
    "updated_at",
)

_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
_COUNTRY_RE = re.compile(r"[A-Z]{2}\Z")
_LANGUAGE_RE = re.compile(r"[a-z]{2,16}\Z")
_SEPARATOR = "\u001f"


class WriterInputError(ValueError):
    """Raised when a document, row or plan does not match the documented shape."""


# --------------------------------------------------------------------------- helpers


def _canonical(value):
    try:
        text = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise WriterInputError("NON_JSON_VALUE", "值不能规范化为 JSON") from exc
    return text


def _json_object(value, name):
    if value is None or value == "":
        return {}
    if isinstance(value, dict):
        return value
    if type(value) is str:
        try:
            parsed = json.loads(value)
        except ValueError as exc:
            raise WriterInputError("INVALID_" + name.upper(), name + " 不是合法 JSON") from exc
        if not isinstance(parsed, dict):
            raise WriterInputError("INVALID_" + name.upper(), name + " 必须是对象")
        return parsed
    raise WriterInputError("INVALID_" + name.upper(), name + " 必须是对象")


def _utc(value, name, *, required_zone=False):
    """Return a naive UTC datetime (the shape MySQL DATETIME(6) columns use)."""
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            if required_zone:
                raise WriterInputError("TIME_WITHOUT_TIMEZONE", name + " 必须带时区")
            return value
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    if type(value) is str and value:
        text = value[:-1] + "+00:00" if value.endswith("Z") else value
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise WriterInputError("INVALID_TIME", name + " 不是合法 ISO8601") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            if required_zone:
                raise WriterInputError("TIME_WITHOUT_TIMEZONE", name + " 必须带时区")
            return parsed
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)
    raise WriterInputError("INVALID_TIME", name + " 必须是时间或 ISO8601 字符串")


def _optional_time(value, name):
    if value is None:
        return None
    return _utc(value, name)


def _iso(value):
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc).isoformat()


def _positive_int(value, name):
    if type(value) is not int or value <= 0:
        raise WriterInputError("INVALID_" + name.upper(), name + " 必须是正整数")
    return value


def _hex64(value, name):
    if type(value) is not str or not _HEX64.fullmatch(value):
        raise WriterInputError("INVALID_" + name.upper(), name + " 必须是 64 位小写十六进制")
    return value


def release_fingerprint(movie_id, dedupe_key):
    """``sha256_hex(utf8(str(movie_id) + U+001F + dedupe_key))``.

    ``normalize._releases`` hashes the release tuple without ``movie_id``, so the
    same tuple in two different movies would collide.  Recomputing the
    fingerprint with a decimal ``movie_id`` prefix and a single U+001F separator
    keeps ``(movie_id, dedupe_key)`` content-idempotent across reruns while
    never merging two titles.
    """
    movie_id = _positive_int(movie_id, "movie_id")
    _hex64(dedupe_key, "dedupe_key")
    payload = (str(movie_id) + _SEPARATOR + dedupe_key).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _operation(table, action, key, values):
    return (table, action, dict(key), dict(values))


# --------------------------------------------------------------------------- state


def _sentinel_topic():
    return {
        "state": None,
        "fetched_at": None,
        "payload_sha256": None,
        "source_record_pk": None,
    }


def existing_state(row):
    """Normalize one ``tmdb_movie_metadata`` row (or ``None``) for comparisons.

    Accepts a mapping or ``None``; JSON columns may arrive as ``dict`` (driver
    returns JSON) or as ``str``.  Datetimes are normalized to naive UTC.
    """
    if row is None:
        row = {}
    if not hasattr(row, "get"):
        raise WriterInputError("INVALID_EXISTING_ROW", "existing 必须是映射或 None")

    movie_id = row.get("movie_id")
    if movie_id is not None:
        movie_id = _positive_int(movie_id, "movie_id")
    tmdb_id = row.get("tmdb_id")
    if tmdb_id is not None:
        tmdb_id = _positive_int(tmdb_id, "tmdb_id")

    statuses = _json_object(row.get("field_status_json"), "field_status_json")
    attempts = _json_object(row.get("last_attempt_json"), "last_attempt_json")
    provenance = _json_object(row.get("provenance_json"), "provenance_json")

    parsed_attempts = {}
    for topic in TOPICS:
        entry = attempts.get(topic)
        parsed_attempts[topic] = _parse_attempt(entry) if isinstance(entry, dict) else None

    parsed_provenance = {}
    for topic in TOPICS:
        entry = provenance.get(topic)
        parsed_provenance[topic] = _parse_provenance(entry) if isinstance(entry, dict) else None

    success_time = {}
    for topic in TOPICS:
        parsed = _optional_time(row.get(SUCCESS_TIME_COLUMNS[topic]), SUCCESS_TIME_COLUMNS[topic])
        if parsed is None:
            retained = parsed_provenance.get(topic)
            parsed = retained["fetched_at"] if retained else None
        success_time[topic] = parsed

    master = row.get("master")
    if master is not None and not hasattr(master, "get"):
        raise WriterInputError("INVALID_MASTER_ROW", "master 快照必须是映射")

    return {
        "movie_id": movie_id,
        "tmdb_id": tmdb_id,
        "fetched_at": _optional_time(row.get("fetched_at"), "fetched_at"),
        "field_status": {topic: statuses.get(topic) for topic in TOPICS},
        "last_attempt": parsed_attempts,
        "provenance": parsed_provenance,
        "success_time": success_time,
        "source_record_pk": row.get("source_record_pk"),
        "payload_sha256": row.get("payload_sha256"),
        "master": dict(master) if master is not None else None,
        # Untouched JSON view of the retained columns, used for change detection.
        "raw": {
            "field_status_json": statuses,
            "last_attempt_json": attempts,
            "provenance_json": provenance,
        },
    }


def _parse_attempt(entry):
    attempt_at = entry.get("attempt_at")
    return {
        "status": entry.get("status"),
        "attempt_at": _optional_time(attempt_at, "attempt_at") if attempt_at is not None else None,
        "source_record_pk": entry.get("source_record_pk"),
        "payload_sha256": entry.get("payload_sha256"),
        "error_code": entry.get("error_code"),
        "conflict_details": entry.get("conflict_details"),
    }


def _parse_provenance(entry):
    fetched_at = entry.get("fetched_at")
    return {
        "state": entry.get("state"),
        "fetched_at": _optional_time(fetched_at, "fetched_at") if fetched_at is not None else None,
        "payload_sha256": entry.get("payload_sha256"),
        "source_record_pk": entry.get("source_record_pk"),
    }


def _coerce_existing(value):
    if value is None:
        return existing_state(None)
    if not hasattr(value, "get"):
        raise WriterInputError("INVALID_EXISTING", "existing 必须是映射或 None")
    if all(key in value for key in ("success_time", "provenance", "last_attempt", "raw")):
        return value
    return existing_state(value)


# --------------------------------------------------------------------------- document


def _coerce_document(document):
    if not hasattr(document, "get"):
        raise WriterInputError("INVALID_DOCUMENT", "document 必须是 normalize_movie 的结果")
    movie_id = _positive_int(document.get("movie_id"), "movie_id")
    tmdb_id = _positive_int(document.get("tmdb_id"), "tmdb_id")
    if "fetched_at" not in document:
        raise WriterInputError("MISSING_FETCHED_AT", "document 缺少 fetched_at")
    fetched_at = _utc(document.get("fetched_at"), "fetched_at", required_zone=True)
    payload_sha256 = _hex64(document.get("payload_sha256"), "payload_sha256")

    source_record_pk = document.get("source_record_pk")
    if source_record_pk is not None:
        source_record_pk = _positive_int(source_record_pk, "source_record_pk")

    field_status = document.get("field_status")
    if not hasattr(field_status, "get"):
        raise WriterInputError("MISSING_TOPIC_STATUS", "document.field_status 必须是映射")
    statuses = {}
    for topic in TOPICS:
        if topic not in field_status:
            raise WriterInputError("MISSING_TOPIC_STATUS", "缺少主题状态 " + topic)
        status = field_status.get(topic)
        if status not in STATUSES:
            raise WriterInputError("INVALID_TOPIC_STATUS", "主题状态不在词表内: " + topic)
        statuses[topic] = status

    facts = document.get("facts")
    if not hasattr(facts, "get"):
        raise WriterInputError("MISSING_FACTS", "document.facts 必须是映射")

    return {
        "movie_id": movie_id,
        "tmdb_id": tmdb_id,
        "fetched_at": fetched_at,
        "payload_sha256": payload_sha256,
        "source_record_pk": source_record_pk,
        "status": statuses,
        "facts": facts,
    }


# --------------------------------------------------------------------------- decide


def _decide_topic(topic, status, retained, retained_time, incoming_time, incoming_sha):
    """Return ``(action, reason)`` for one topic.

    Ordering follows ``topic_state_model.rules``: failure first (attempt only),
    then first success, then the strict time comparison, then the equal-time
    payload comparison, then the older-response branch.
    """
    if topic in DEFERRED_TOPICS:
        return "skipped", DEFERRED_TOPICS[topic]
    if status in ATTEMPT_ONLY_STATUSES:
        return "skipped", "ATTEMPT_ONLY_" + status
    if retained_time is None:
        if status == STATUS_PRESENT:
            return "replace", "FIRST_SUCCESS"
        return "clear", "FIRST_SUCCESS"
    if incoming_time > retained_time:
        if status == STATUS_PRESENT:
            return "replace", "NEWER_FETCHED_AT"
        return "clear", "NEWER_FETCHED_AT"
    if incoming_time == retained_time:
        retained_sha = retained.get("payload_sha256") if retained else None
        if retained_sha == incoming_sha:
            return "noop", "SAME_FETCHED_AT_SAME_PAYLOAD"
        return "conflict", "SAME_FETCHED_AT_DIFFERENT_PAYLOAD"
    return "stale", "OLDER_RESPONSE"


def _conflict_entry(attempt_at, source_record_pk, payload_sha256, retained):
    retained = retained or _sentinel_topic()
    return {
        "status": STATUS_FAILED,
        "error_code": "TOPIC_VERSION_CONFLICT",
        "attempt_at": _iso(attempt_at),
        "source_record_pk": source_record_pk,
        "payload_sha256": payload_sha256,
        "conflict_details": {
            "retained_payload_sha256": retained.get("payload_sha256"),
            "incoming_payload_sha256": payload_sha256,
            "retained_source_record_pk": retained.get("source_record_pk"),
            "incoming_source_record_pk": source_record_pk,
            "attempt_at": _iso(attempt_at),
        },
    }


def _attempt_entry(previous, attempt_at, status, source_record_pk, payload_sha256, retained):
    entry = {
        "status": status,
        "attempt_at": _iso(attempt_at),
        "source_record_pk": source_record_pk,
        "payload_sha256": payload_sha256,
        "error_code": None,
    }
    previous_at = previous.get("attempt_at") if previous else None
    if previous_at is None:
        return entry
    if attempt_at > previous_at:
        return entry
    if attempt_at < previous_at:
        return None
    # Same attempt instant: identical payload is a rerun, anything else is a conflict.
    if previous.get("payload_sha256") == payload_sha256 and previous.get("status") == status:
        return None
    return _conflict_entry(attempt_at, source_record_pk, payload_sha256, retained)


def decide(existing, document, *, attempt_at):
    """Compare one normalized document against the retained state, per topic.

    ``existing`` is :func:`existing_state` output (a raw row is accepted too).
    ``attempt_at`` is the moment this attempt runs and only gates the attempt
    layer; success replacement is gated by each topic's own ``*_at`` time.
    """
    state = _coerce_existing(existing)
    doc = _coerce_document(document)
    if state["movie_id"] is not None and state["movie_id"] != doc["movie_id"]:
        raise WriterInputError("MOVIE_ID_MISMATCH", "existing.movie_id 与 document 不一致")
    if state["tmdb_id"] is not None and state["tmdb_id"] != doc["tmdb_id"]:
        raise WriterInputError("TMDB_ID_MISMATCH", "existing.tmdb_id 与 document 不一致")
    if attempt_at is None:
        raise WriterInputError("MISSING_ATTEMPT_AT", "attempt_at 不能为空")
    attempt_dt = _utc(attempt_at, "attempt_at")

    field_status = dict(state["field_status"])
    last_attempt = dict(state["last_attempt"])
    provenance = dict(state["provenance"])
    success_time = dict(state["success_time"])
    source_record_pk = state["source_record_pk"]
    payload_sha256 = state["payload_sha256"]

    topics = {}
    attempts = {}
    for topic in TOPICS:
        status = doc["status"][topic]
        retained = provenance.get(topic)
        retained_time = success_time.get(topic)
        action, reason = _decide_topic(
            topic, status, retained, retained_time, doc["fetched_at"], doc["payload_sha256"]
        )

        if action == "conflict":
            entry = _conflict_entry(
                attempt_dt, doc["source_record_pk"], doc["payload_sha256"], retained
            )
            previous = last_attempt.get(topic)
            previous_at = previous.get("attempt_at") if previous else None
            if previous_at is not None and attempt_dt < previous_at:
                entry = None  # an older attempt never rewrites a newer record
                reason = reason + "_ATTEMPT_TOO_OLD"
            elif (
                previous_at is not None
                and attempt_dt == previous_at
                and previous.get("error_code") == "TOPIC_VERSION_CONFLICT"
                and previous.get("payload_sha256") == doc["payload_sha256"]
            ):
                entry = None  # identical conflict already recorded
        else:
            entry = _attempt_entry(
                last_attempt.get(topic),
                attempt_dt,
                status,
                doc["source_record_pk"],
                doc["payload_sha256"],
                retained,
            )

        if entry is not None:
            last_attempt[topic] = _parse_attempt(entry)
            field_status[topic] = entry["status"]
        attempts[topic] = entry

        if action in ("replace", "clear"):
            success_time[topic] = doc["fetched_at"]
            provenance[topic] = {
                "state": STATUS_PRESENT if action == "replace" else STATUS_EMPTY,
                "fetched_at": doc["fetched_at"],
                "payload_sha256": doc["payload_sha256"],
                "source_record_pk": doc["source_record_pk"],
            }
            if topic == "base":
                source_record_pk = doc["source_record_pk"]
                payload_sha256 = doc["payload_sha256"]

        topics[topic] = {"action": action, "reason": reason, "status": status}

    global_fetched_at = doc["fetched_at"]
    if state["fetched_at"] is not None and state["fetched_at"] > global_fetched_at:
        global_fetched_at = state["fetched_at"]

    return {
        "movie_id": doc["movie_id"],
        "tmdb_id": doc["tmdb_id"],
        "topics": topics,
        "attempts": attempts,
        "metadata": {
            "fetched_at": global_fetched_at,
            "field_status": field_status,
            "last_attempt": last_attempt,
            "provenance": provenance,
            "success_time": success_time,
            "source_record_pk": source_record_pk,
            "payload_sha256": payload_sha256,
        },
    }


# --------------------------------------------------------------------------- plan


def _topic_list(value, topic):
    if type(value) is not list:
        raise WriterInputError("INVALID_FACTS_" + topic.upper(), topic + " facts 必须是列表")
    return value


def _country_code(value, topic):
    if type(value) is not str or not _COUNTRY_RE.fullmatch(value):
        raise WriterInputError("INVALID_FACTS_" + topic.upper(), "ISO 3166-1 代码必须是两位大写字母")
    return value


def _language_code(value):
    if type(value) is not str or not _LANGUAGE_RE.fullmatch(value):
        raise WriterInputError("INVALID_FACTS_SPOKEN_LANGUAGES", "语言代码必须是 2..16 位小写代码")
    return value


def _optional_text(value, topic, maximum):
    if value is None:
        return None
    if type(value) is not str or len(value) > maximum:
        raise WriterInputError("INVALID_FACTS_" + topic.upper(), "文本缺失或超长")
    return value


def _replace_operations(topic, movie_id, facts, source_record_pk, observed_at):
    """Relationship rows for one ``replace`` decision, plus stale-row cleanup."""
    if topic == "countries":
        rows = _topic_list(facts, topic)
        operations, keep = [], []
        for item in rows:
            if not hasattr(item, "get"):
                raise WriterInputError("INVALID_FACTS_COUNTRIES", "国家元素必须是映射")
            code = _country_code(item.get("iso_3166_1"), topic)
            if code in keep:
                continue
            keep.append(code)
            values = {"movie_id": movie_id, "country_iso2": code,
                      "country_name": _optional_text(item.get("name"), topic, 255)}
            operations.append(_operation(
                "tmdb_movie_countries", "upsert", {"movie_id": movie_id, "country_iso2": code}, values))
        operations.append(_cleanup("tmdb_movie_countries", movie_id, keep))
        return operations

    if topic == "companies":
        rows = _topic_list(facts, topic)
        operations, keep = [], []
        for item in rows:
            if not hasattr(item, "get"):
                raise WriterInputError("INVALID_FACTS_COMPANIES", "公司元素必须是映射")
            company_id = _positive_int(item.get("id"), "company_id")
            if company_id in keep:
                continue
            keep.append(company_id)
            name = item.get("name")
            if type(name) is not str or not name.strip() or len(name) > 255:
                raise WriterInputError("INVALID_FACTS_COMPANIES", "公司名称必须是 1..255 文本")
            origin = item.get("origin_country")
            if origin is not None:
                origin = _country_code(origin, topic)
            values = {"tmdb_company_id": company_id, "name": name,
                      "origin_country": origin,
                      "logo_path": _optional_text(item.get("logo_path"), topic, 500)}
            operations.append(_operation(
                "tmdb_companies", "upsert", {"tmdb_company_id": company_id}, values))
            operations.append(_operation(
                "tmdb_movie_companies", "link", {"movie_id": movie_id},
                {"tmdb_company_id": company_id}))
        operations.append(_cleanup("tmdb_movie_companies", movie_id, keep))
        return operations

    if topic == "keywords":
        rows = _topic_list(facts, topic)
        operations, keep = [], []
        for item in rows:
            if not hasattr(item, "get"):
                raise WriterInputError("INVALID_FACTS_KEYWORDS", "关键词元素必须是映射")
            keyword_id = _positive_int(item.get("id"), "keyword_id")
            if keyword_id in keep:
                continue
            keep.append(keyword_id)
            name = item.get("name")
            if type(name) is not str or not name.strip() or len(name) > 500:
                raise WriterInputError("INVALID_FACTS_KEYWORDS", "关键词名称必须是 1..500 文本")
            operations.append(_operation(
                "tmdb_keywords", "upsert", {"tmdb_keyword_id": keyword_id},
                {"tmdb_keyword_id": keyword_id, "name": name}))
            operations.append(_operation(
                "tmdb_movie_keywords", "link", {"movie_id": movie_id},
                {"tmdb_keyword_id": keyword_id}))
        operations.append(_cleanup("tmdb_movie_keywords", movie_id, keep))
        return operations

    if topic == "spoken_languages":
        rows = _topic_list(facts, topic)
        operations, keep = [], []
        for item in rows:
            if not hasattr(item, "get"):
                raise WriterInputError("INVALID_FACTS_SPOKEN_LANGUAGES", "语言元素必须是映射")
            code = _language_code(item.get("iso_639_1"))
            if code in keep:
                continue
            keep.append(code)
            values = {"movie_id": movie_id, "language_code": code,
                      "english_name": _optional_text(item.get("english_name"), topic, 500),
                      "name": _optional_text(item.get("name"), topic, 500)}
            operations.append(_operation(
                "tmdb_movie_languages", "upsert", {"movie_id": movie_id, "language_code": code}, values))
        operations.append(_cleanup("tmdb_movie_languages", movie_id, keep))
        return operations

    if topic == "releases":
        operations, keep = [], []
        for region in _topic_list(facts, topic):
            if not hasattr(region, "get"):
                raise WriterInputError("INVALID_FACTS_RELEASES", "发行地区必须是映射")
            code = _country_code(region.get("iso_3166_1"), topic)
            for item in _topic_list(region.get("release_dates"), topic):
                if not hasattr(item, "get"):
                    raise WriterInputError("INVALID_FACTS_RELEASES", "发行记录必须是映射")
                fingerprint = release_fingerprint(movie_id, item.get("dedupe_key"))
                if fingerprint in keep:
                    continue
                keep.append(fingerprint)
                release_type = item.get("type")
                if type(release_type) is not int or not 1 <= release_type <= 6:
                    raise WriterInputError("INVALID_FACTS_RELEASES", "发行类型必须是 1..6")
                release_date = item.get("release_date")
                if release_date is not None and (
                        type(release_date) is not str or not _ISO_DATE.fullmatch(release_date)):
                    raise WriterInputError("INVALID_FACTS_RELEASES", "发行日期必须是 YYYY-MM-DD 或 null")
                language = item.get("iso_639_1")
                values = {
                    "movie_id": movie_id,
                    "country_iso2": code,
                    "release_type": release_type,
                    "release_date": release_date,
                    "release_date_raw": _optional_text(item.get("release_date_raw"), topic, 64),
                    "certification": _optional_text(item.get("certification"), topic, 64),
                    "note": _optional_text(item.get("note"), topic, 512),
                    "certification_language": _language_code(language) if language is not None else None,
                    "source_record_pk": source_record_pk,
                    "record_fingerprint": fingerprint,
                }
                operations.append(_operation(
                    "tmdb_release_records", "upsert",
                    {"record_fingerprint": fingerprint}, values))
        operations.append(_cleanup("tmdb_release_records", movie_id, keep))
        return operations

    if topic == "metrics":
        metrics = facts
        if not hasattr(metrics, "get"):
            raise WriterInputError("INVALID_FACTS_METRICS", "metrics facts 必须是映射")
        values = {"movie_id": movie_id,
                  "observed_at": observed_at,
                  "source_record_pk": source_record_pk,
                  "vote_average": _metric_value(metrics.get("vote_average"), "vote_average"),
                  "vote_count": _metric_value(metrics.get("vote_count"), "vote_count", integer=True),
                  "popularity": _metric_value(metrics.get("popularity"), "popularity")}
        return [_operation("tmdb_metric_observations", "upsert",
                           {"movie_id": movie_id, "observed_at": observed_at}, values)]

    return []


def _metric_value(value, name, *, integer=False):
    if value is None:
        return None
    if type(value) is bool or type(value) not in (int, float):
        raise WriterInputError("INVALID_FACTS_METRICS", name + " 必须是数值或 null")
    if integer and type(value) is not int:
        raise WriterInputError("INVALID_FACTS_METRICS", name + " 必须是整数")
    if value < 0:
        raise WriterInputError("INVALID_FACTS_METRICS", name + " 不能为负")
    return int(value) if integer else value


def _cleanup(table, movie_id, keep):
    if not keep:
        return (table, "delete_all", {"movie_id": movie_id}, {})
    return (table, "delete_missing", {"movie_id": movie_id}, {"keep": tuple(keep)})


def _clear_operations(topic, movie_id):
    if topic in ("base", "credits", "metrics"):
        # base/credits have no writable relation rows; metric history is append only.
        return []
    return [(RELATION_TABLES[topic], "delete_all", {"movie_id": movie_id}, {})]


def _serialize_attempt(entry):
    if entry is None:
        return None
    out = {
        "status": entry["status"],
        "attempt_at": _iso(entry["attempt_at"]),
        "source_record_pk": entry["source_record_pk"],
        "payload_sha256": entry["payload_sha256"],
        "error_code": entry["error_code"],
    }
    if entry.get("conflict_details"):
        out["conflict_details"] = dict(entry["conflict_details"])
    return out


def _serialize_provenance(entry):
    if entry is None:
        return None
    return {
        "state": entry["state"],
        "fetched_at": _iso(entry["fetched_at"]),
        "payload_sha256": entry["payload_sha256"],
        "source_record_pk": entry["source_record_pk"],
    }


def _metadata_row(decided, now):
    metadata = decided["metadata"]
    attempts, provenance = {}, {}
    for topic in TOPICS:
        entry = _serialize_attempt(metadata["last_attempt"].get(topic))
        if entry is not None:
            attempts[topic] = entry
        entry = _serialize_provenance(metadata["provenance"].get(topic))
        if entry is not None:
            provenance[topic] = entry
    row = {
        "movie_id": decided["movie_id"],
        "tmdb_id": decided["tmdb_id"],
        "source_record_pk": metadata["source_record_pk"],
        "payload_sha256": metadata["payload_sha256"],
        "field_status_json": _canonical(
            {topic: metadata["field_status"].get(topic) for topic in TOPICS}),
        "last_attempt_json": _canonical(attempts),
        "provenance_json": _canonical(provenance),
        "fetched_at": metadata["fetched_at"],
        "updated_at": now,
    }
    for topic in TOPICS:
        row[SUCCESS_TIME_COLUMNS[topic]] = metadata["success_time"].get(topic)
    return row


def _metadata_changed(existing, row):
    if existing["movie_id"] is None:
        return True
    if existing["fetched_at"] != row["fetched_at"]:
        return True
    if existing["source_record_pk"] != row["source_record_pk"]:
        return True
    if existing["payload_sha256"] != row["payload_sha256"]:
        return True
    for topic in TOPICS:
        if existing["success_time"].get(topic) != row[SUCCESS_TIME_COLUMNS[topic]]:
            return True
    raw = existing.get("raw") or {}
    for key in ("field_status_json", "last_attempt_json", "provenance_json"):
        try:
            retained = _canonical(raw.get(key) or {})
        except WriterInputError:
            return True
        if retained != row[key]:
            return True
    return False


def _master_fill_operations(facts, movie_id, master):
    """Opt-in fill of NULL sealed ``movies`` columns; never a default write."""
    if master is None:
        raise WriterInputError("MASTER_SNAPSHOT_REQUIRED",
                               "allow_master_fill=True 时必须提供 existing['master'] 快照")
    base = facts.get("base") if hasattr(facts, "get") else None
    if not hasattr(base, "get"):
        return []
    fills = {}
    for column, fact in MASTER_FILL_COLUMNS.items():
        if column not in master or master.get(column) is not None:
            continue
        value = base.get(fact)
        if value is None:
            continue
        if column == "release_date":
            if type(value) is not str or not _ISO_DATE.fullmatch(value):
                continue
        fills[column] = value
    if not fills:
        return []
    return [("movies", "fill_missing", {"movie_id": movie_id}, fills)]


def build_plan(existing, document, *, now, allow_master_fill=False):
    """Turn decisions into an explicit list of ``(table, action, key, values)``.

    ``now`` doubles as ``attempt_at`` and ``updated_at``.  The plan never
    contains an operation that a decision did not justify; an idempotent rerun
    (same ``fetched_at`` and payload) produces an empty plan.
    """
    state = _coerce_existing(existing)
    decided = decide(state, document, attempt_at=now)
    now_dt = _utc(now, "now")
    doc = _coerce_document(document)

    operations = []
    for topic in TOPICS:
        action = decided["topics"][topic]["action"]
        if action == "replace":
            if topic in ("releases", "metrics") and doc["source_record_pk"] is None:
                raise WriterInputError("SOURCE_RECORD_REQUIRED",
                                       topic + " 替换必须提供 source_record_pk")
            operations.extend(_replace_operations(
                topic, doc["movie_id"], doc["facts"].get(topic),
                doc["source_record_pk"], doc["fetched_at"]))
        elif action == "clear":
            operations.extend(_clear_operations(topic, doc["movie_id"]))

    if allow_master_fill:
        operations.extend(_master_fill_operations(
            doc["facts"], doc["movie_id"], state.get("master")))

    row = _metadata_row(decided, now_dt)
    if _metadata_changed(state, row):
        operations.append(
            ("tmdb_movie_metadata", "upsert", {"movie_id": doc["movie_id"]}, row))

    return {
        "movie_id": doc["movie_id"],
        "tmdb_id": doc["tmdb_id"],
        "decisions": decided["topics"],
        "attempts": decided["attempts"],
        "operations": operations,
        "metadata": row,
        "deferred": [topic for topic in TOPICS if topic in DEFERRED_TOPICS],
        "now": now_dt,
    }


# --------------------------------------------------------------------------- apply


def _placeholders(count):
    return ",".join(["%s"] * count)


def _statement(table, action, key, values, plan):
    now = plan["now"]

    if table == "tmdb_companies" and action == "upsert":
        sql = ("INSERT INTO tmdb_companies (tmdb_company_id,name,origin_country,logo_path,"
               "created_at,updated_at) VALUES (" + _placeholders(6) + ") "
               "ON DUPLICATE KEY UPDATE name=VALUES(name),origin_country=VALUES(origin_country),"
               "logo_path=VALUES(logo_path),updated_at=VALUES(updated_at)")
        return sql, (values["tmdb_company_id"], values["name"], values["origin_country"],
                     values["logo_path"], now, now)

    if table == "tmdb_keywords" and action == "upsert":
        sql = ("INSERT INTO tmdb_keywords (tmdb_keyword_id,name,created_at,updated_at) "
               "VALUES (" + _placeholders(4) + ") "
               "ON DUPLICATE KEY UPDATE name=VALUES(name),updated_at=VALUES(updated_at)")
        return sql, (values["tmdb_keyword_id"], values["name"], now, now)

    if table == "tmdb_movie_countries" and action == "upsert":
        sql = ("INSERT INTO tmdb_movie_countries (movie_id,country_iso2,country_name) "
               "VALUES (" + _placeholders(3) + ") "
               "ON DUPLICATE KEY UPDATE country_name=VALUES(country_name)")
        return sql, (values["movie_id"], values["country_iso2"], values["country_name"])

    if table == "tmdb_movie_languages" and action == "upsert":
        sql = ("INSERT INTO tmdb_movie_languages (movie_id,language_code,english_name,name) "
               "VALUES (" + _placeholders(4) + ") "
               "ON DUPLICATE KEY UPDATE english_name=VALUES(english_name),name=VALUES(name)")
        return sql, (values["movie_id"], values["language_code"], values["english_name"],
                     values["name"])

    if table == "tmdb_movie_companies" and action == "link":
        sql = ("INSERT INTO tmdb_movie_companies (movie_id,company_id) "
               "SELECT %s, company_id FROM tmdb_companies WHERE tmdb_company_id=%s "
               "ON DUPLICATE KEY UPDATE movie_id=movie_id")
        return sql, (key["movie_id"], values["tmdb_company_id"])

    if table == "tmdb_movie_keywords" and action == "link":
        sql = ("INSERT INTO tmdb_movie_keywords (movie_id,keyword_id) "
               "SELECT %s, keyword_id FROM tmdb_keywords WHERE tmdb_keyword_id=%s "
               "ON DUPLICATE KEY UPDATE movie_id=movie_id")
        return sql, (key["movie_id"], values["tmdb_keyword_id"])

    if table == "tmdb_release_records" and action == "upsert":
        sql = ("INSERT INTO tmdb_release_records (movie_id,country_iso2,release_type,release_date,"
               "release_date_raw,certification,note,certification_language,source_record_pk,"
               "record_fingerprint,created_at) VALUES (" + _placeholders(11) + ") "
               "ON DUPLICATE KEY UPDATE release_type=VALUES(release_type),"
               "release_date=VALUES(release_date),release_date_raw=VALUES(release_date_raw),"
               "certification=VALUES(certification),note=VALUES(note),"
               "certification_language=VALUES(certification_language),"
               "source_record_pk=VALUES(source_record_pk)")
        return sql, (values["movie_id"], values["country_iso2"], values["release_type"],
                     values["release_date"], values["release_date_raw"], values["certification"],
                     values["note"], values["certification_language"], values["source_record_pk"],
                     values["record_fingerprint"], now)

    if table == "tmdb_metric_observations" and action == "upsert":
        sql = ("INSERT INTO tmdb_metric_observations (movie_id,observed_at,vote_average,"
               "vote_count,popularity,source_record_pk,created_at) VALUES ("
               + _placeholders(7) + ") "
               "ON DUPLICATE KEY UPDATE vote_average=VALUES(vote_average),"
               "vote_count=VALUES(vote_count),popularity=VALUES(popularity),"
               "source_record_pk=VALUES(source_record_pk)")
        return sql, (values["movie_id"], values["observed_at"], values["vote_average"],
                     values["vote_count"], values["popularity"], values["source_record_pk"], now)

    if table == "tmdb_movie_metadata" and action == "upsert":
        sql = ("INSERT INTO tmdb_movie_metadata (" + ",".join(METADATA_COLUMNS) + ") VALUES ("
               + _placeholders(len(METADATA_COLUMNS)) + ") ON DUPLICATE KEY UPDATE "
               + ",".join(column + "=VALUES(" + column + ")"
                          for column in METADATA_COLUMNS if column != "movie_id"))
        return sql, tuple(values[column] for column in METADATA_COLUMNS)

    if action == "delete_all":
        return "DELETE FROM " + table + " WHERE movie_id=%s", (key["movie_id"],)

    if action == "delete_missing":
        keep = tuple(values.get("keep") or ())
        if table == "tmdb_movie_companies":
            sql = ("DELETE mc FROM tmdb_movie_companies AS mc JOIN tmdb_companies AS c "
                   "ON c.company_id=mc.company_id WHERE mc.movie_id=%s AND c.tmdb_company_id NOT IN ("
                   + _placeholders(len(keep)) + ")")
            return sql, (key["movie_id"],) + keep
        if table == "tmdb_movie_keywords":
            sql = ("DELETE mk FROM tmdb_movie_keywords AS mk JOIN tmdb_keywords AS k "
                   "ON k.keyword_id=mk.keyword_id WHERE mk.movie_id=%s AND k.tmdb_keyword_id NOT IN ("
                   + _placeholders(len(keep)) + ")")
            return sql, (key["movie_id"],) + keep
        column = {"tmdb_movie_countries": "country_iso2",
                  "tmdb_movie_languages": "language_code",
                  "tmdb_release_records": "record_fingerprint"}.get(table)
        if column is None:
            raise WriterInputError("UNSUPPORTED_OPERATION", "不支持的表: " + str(table))
        sql = ("DELETE FROM " + table + " WHERE movie_id=%s AND " + column + " NOT IN ("
               + _placeholders(len(keep)) + ")")
        return sql, (key["movie_id"],) + keep

    if table == "movies" and action == "fill_missing":
        columns = sorted(values)
        unknown = [column for column in columns if column not in MASTER_FILL_COLUMNS]
        if unknown:
            raise WriterInputError("UNSUPPORTED_MASTER_COLUMN", "不允许填充的列: " + ",".join(unknown))
        sql = ("UPDATE movies SET " + ",".join(column + "=%s" for column in columns)
               + " WHERE movie_id=%s")
        return sql, tuple(values[column] for column in columns) + (key["movie_id"],)

    raise WriterInputError("UNSUPPORTED_OPERATION", "未知操作: " + str(table) + "/" + str(action))


def apply_plan(connection, plan):
    """Execute exactly the operations in ``plan``; decide nothing here.

    ``connection`` must expose DB-API ``execute(sql, params)`` (for example a
    ``pymysql`` connection).  Transaction control belongs to the caller.
    """
    if not hasattr(plan, "get") or not isinstance(plan.get("operations"), (list, tuple)):
        raise WriterInputError("INVALID_PLAN", "plan 必须包含 operations 列表")
    if "now" not in plan:
        raise WriterInputError("INVALID_PLAN", "plan 缺少 now")

    executed = 0
    by_table = {}
    for operation in plan["operations"]:
        if not isinstance(operation, (list, tuple)) or len(operation) != 4:
            raise WriterInputError("INVALID_OPERATION", "操作必须是 (table, action, key, values)")
        table, action, key, values = operation
        if not isinstance(key, dict) or not isinstance(values, dict):
            raise WriterInputError("INVALID_OPERATION", "操作的 key/values 必须是映射")
        sql, params = _statement(table, action, key, values, plan)
        connection.execute(sql, params)
        executed += 1
        by_table[table] = by_table.get(table, 0) + 1
    return {"executed": executed, "by_table": by_table}


__all__ = [
    "WriterInputError",
    "TOPICS",
    "SUCCESS_TIME_COLUMNS",
    "release_fingerprint",
    "existing_state",
    "decide",
    "build_plan",
    "apply_plan",
]