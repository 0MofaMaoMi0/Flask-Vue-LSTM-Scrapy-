"""Pure, read-only audit for TMDB supplement normalized documents.

Public input is either an in-memory iterable of normalized
``tmdb-supplement-v1`` documents, or an explicit JSON/JSONL path.  This module
never opens a database, reads application configuration, accesses the network,
or mutates documents.

The audit is intentionally independent of the normalizer/aggregator.  It
derives coverage from facts as well as labels, so a failed or merely truncated
record cannot look like a successful empty result.  A local coverage
denominator is a unique positive ``movie_id``; a TMDB-only board entry is put
in the identity review list and is never counted as a locally catalogued movie.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path

AUDIT_SCHEMA = "tmdb-supplement-audit-v1"
DOCUMENT_SCHEMA = "tmdb-supplement-v1"
STATUSES = ("NOT_FETCHED", "MISSING", "EMPTY", "PRESENT", "FAILED")
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
LIST_TOPICS = frozenset(
    ("countries", "companies", "spoken_languages", "keywords", "releases", "credits")
)
_ISO2 = re.compile(r"^[A-Z]{2}$")
_ISO_LANG = re.compile(r"^[a-z]{2,3}$")
_HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")


class AuditInputError(ValueError):
    """The explicit input source itself could not be read or decoded."""


def _is_int(value):
    return type(value) is int


def _is_number(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, ValueError):
        return False


def _status(value):
    return value if value in STATUSES else None


def _empty_status_counts():
    return {name: 0 for name in STATUSES}


def _parse_time(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, OverflowError):
        return None
    if stamp.tzinfo is None:
        return None
    try:
        return stamp.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None


def _topic_items(topic, facts):
    value = facts.get(topic)
    if topic == "base":
        if not isinstance(value, dict):
            return [], []
        meaningful = any(
            item is not None and item != "" and item != [] and item != {}
            for item in value.values()
        )
        return ([value] if meaningful else []), []
    if topic == "metrics":
        if not isinstance(value, dict):
            return [], ([] if value is None else [value])
        if not value:
            return [], []
        valid_values = 0
        invalid = []
        for key in ("vote_average", "vote_count", "popularity"):
            if key not in value:
                continue
            item = value[key]
            if item is None:
                continue
            if key == "vote_count":
                if _is_int(item) and item >= 0:
                    valid_values += 1
                else:
                    invalid.append(item)
            elif _is_number(item):
                valid_values += 1
            else:
                invalid.append(item)
        return ([value] if valid_values else []), invalid
    if not isinstance(value, list):
        return [], ([] if value is None else [value])
    valid, invalid = [], []
    for item in value:
        ok = False
        if isinstance(item, dict):
            if topic == "countries":
                code = item.get("iso_3166_1")
                ok = isinstance(code, str) and bool(_ISO2.fullmatch(code))
            elif topic == "companies":
                ok = (
                    _is_int(item.get("id"))
                    and item["id"] > 0
                    and isinstance(item.get("name"), str)
                    and bool(item["name"].strip())
                )
            elif topic == "spoken_languages":
                code = item.get("iso_639_1")
                ok = isinstance(code, str) and bool(_ISO_LANG.fullmatch(code))
            elif topic == "keywords":
                ok = (
                    _is_int(item.get("id"))
                    and item["id"] > 0
                    and isinstance(item.get("name"), str)
                    and bool(item["name"].strip())
                )
            elif topic == "releases":
                code = item.get("iso_3166_1")
                ok = isinstance(code, str) and bool(_ISO2.fullmatch(code))
            elif topic == "credits":
                person = item.get("person_id", item.get("id"))
                ok = (
                    isinstance(item.get("credit_id"), str)
                    and bool(item["credit_id"].strip())
                    and _is_int(person)
                    and person > 0
                    and item.get("role_type") in ("cast", "crew")
                )
        (valid if ok else invalid).append(item)
    return valid, invalid


def _metrics_values(facts):
    value = facts.get("metrics")
    if not isinstance(value, dict):
        return {}, [value] if value is not None else []
    invalid = [v for v in value.values() if v is not None and not _is_number(v)]
    return value, invalid


def _derive_topic(topic, document):
    """Return effective status, raw status, mismatch flag, item counts.

    Facts are allowed to preserve a prior successful value when the incoming
    label says FAILED/MISSING/NOT_FETCHED.  Conversely, a PRESENT label with no
    usable facts is not allowed to masquerade as successful coverage.
    """
    facts = document.get("facts")
    facts = facts if isinstance(facts, dict) else {}
    field_status = document.get("field_status")
    field_status = field_status if isinstance(field_status, dict) else {}
    raw = _status(field_status.get(topic))
    valid, invalid = _topic_items(topic, facts)
    has_value = bool(valid)
    has_invalid = bool(invalid)

    if raw is None:
        code = "MISSING_STATUS" if topic not in field_status else "INVALID_STATUS"
        return "FAILED", None, False, len(valid), len(invalid), code
    mismatch = False
    if raw == "PRESENT":
        if has_value:
            return "PRESENT", raw, False, len(valid), len(invalid), None
        if has_invalid:
            return "FAILED", raw, True, len(valid), len(invalid), "ALL_ITEMS_INVALID"
        return "FAILED", raw, True, 0, 0, "PRESENT_WITHOUT_FACTS"
    if raw == "EMPTY":
        if topic == "metrics":
            return "EMPTY", raw, False, 0, len(invalid), None
        if has_value:
            return "PRESENT", raw, True, len(valid), len(invalid), "EMPTY_WITH_FACTS"
        if has_invalid:
            return "FAILED", raw, True, 0, len(invalid), "EMPTY_WITH_INVALID_ITEMS"
        return "EMPTY", raw, False, 0, 0, None
    # FAILED / MISSING / NOT_FETCHED.  Usable facts are stronger evidence than
    # a transport/field error and must not be erased by it.
    if has_value:
        return "PRESENT", raw, True, len(valid), len(invalid), "PRESENT_FACTS_DESPITE_STATUS"
    if has_invalid:
        return "FAILED", raw, False, 0, len(invalid), "INVALID_ITEMS"
    return raw, raw, False, 0, 0, None


def _input_records(documents):
    """Return raw records plus a stable source description."""
    if isinstance(documents, (str, os.PathLike)):
        path = Path(documents)
        try:
            data = path.read_bytes()
        except OSError as error:
            raise AuditInputError("cannot read explicit audit input") from error
        source = {
            "kind": "jsonl_file" if path.suffix.lower() == ".jsonl" else "json_file",
            "name": path.name,
            "sha256": hashlib.sha256(data).hexdigest(),
        }
        text = data.decode("utf-8-sig")
        if source["kind"] == "jsonl_file":
            records = []
            for line in text.splitlines():
                if not line.strip():
                    continue
                try:
                    records.append(json.loads(line))
                except ValueError:
                    records.append({"_audit_parse_error": "invalid JSONL record"})
            return records, source
        try:
            value = json.loads(text)
        except ValueError as error:
            raise AuditInputError("explicit JSON input is not valid JSON") from error
        return _container_records(value), source

    if hasattr(documents, "read"):
        data = documents.read()
        if isinstance(data, str):
            data = data.encode("utf-8")
        source = {
            "kind": "stream",
            "name": Path(getattr(documents, "name", "") or "").name or None,
            "sha256": hashlib.sha256(data).hexdigest(),
        }
        try:
            value = json.loads(data.decode("utf-8-sig"))
        except ValueError as error:
            raise AuditInputError("stream input is not valid JSON") from error
        return _container_records(value), source

    if isinstance(documents, dict):
        if documents.get("schema_version") == DOCUMENT_SCHEMA:
            return [documents], {"kind": "inline", "name": None, "sha256": None}
        if isinstance(documents.get("documents"), (list, tuple)):
            return list(documents["documents"]), {"kind": "inline", "name": None, "sha256": None}
        return [documents], {"kind": "inline", "name": None, "sha256": None}

    try:
        records = list(documents)
    except TypeError as error:
        raise AuditInputError("documents must be an iterable, document, or explicit path") from error
    return records, {"kind": "inline", "name": None, "sha256": None}


def _container_records(value):
    if isinstance(value, dict) and isinstance(value.get("documents"), (list, tuple)):
        return list(value["documents"])
    if isinstance(value, list):
        return value
    return [value]


def _valid_document(document):
    if not isinstance(document, dict):
        return False, "NOT_AN_OBJECT"
    if document.get("schema_version") != DOCUMENT_SCHEMA:
        return False, "UNSUPPORTED_SCHEMA"
    movie_id = document.get("movie_id")
    tmdb_id = document.get("tmdb_id")
    if not (_is_int(movie_id) and movie_id > 0):
        return False, "INVALID_MOVIE_ID"
    if not (_is_int(tmdb_id) and tmdb_id > 0):
        return False, "INVALID_TMDB_ID"
    if not isinstance(document.get("field_status"), dict):
        return False, "MISSING_FIELD_STATUS"
    if not isinstance(document.get("facts"), dict):
        return False, "MISSING_FACTS"
    return True, None


def _add_example(bucket, value, limit):
    if value is not None and len(bucket) < limit:
        bucket.append(value)


def audit_documents(documents, *, min_votes=50, max_details=20):
    """Audit normalized v1 documents and return a deterministic JSON object.

    ``documents`` may be a single document, an iterable of documents, a JSON
    list/``{"documents": [...]}`` container, a JSONL path, or a JSON path.
    Explicit file input is the only filesystem access; a malformed whole JSON
    file raises :class:`AuditInputError`.  Individual malformed JSONL records
    and malformed documents are counted, not turned into zero coverage.

    ``min_votes`` is the effective-rating threshold (default 50).  Details are
    bounded by ``max_details`` so a large broken batch cannot become a report.
    """
    if not _is_int(min_votes) or min_votes < 0:
        raise ValueError("min_votes must be a non-negative integer")
    if not _is_int(max_details) or max_details < 1 or max_details > 100:
        raise ValueError("max_details must be an integer between 1 and 100")

    records, source = _input_records(documents)
    source["record_count"] = len(records)
    bad = []
    valid = []
    invalid_documents = 0
    invalid_status_documents = 0
    invalid_hash_documents = 0
    invalid_time_documents = 0
    incomplete_documents = 0
    payload_id_conflicts = []
    identity_review = []
    sample_details = []

    for index, raw in enumerate(records):
        if isinstance(raw, dict) and "_audit_parse_error" in raw:
            invalid_documents += 1
            _add_example(bad, {"index": index, "reason": raw["_audit_parse_error"]}, max_details)
            continue
        ok, reason = _valid_document(raw)
        if not ok:
            invalid_documents += 1
            review = {"index": index, "reason": reason}
            if isinstance(raw, dict):
                if _is_int(raw.get("movie_id")) and raw["movie_id"] > 0:
                    review["movie_id"] = raw["movie_id"]
                if _is_int(raw.get("tmdb_id")) and raw["tmdb_id"] > 0:
                    review["tmdb_id"] = raw["tmdb_id"]
            _add_example(bad, review, max_details)
            continue
        valid.append((index, raw))
        if not isinstance(raw.get("issues", []), list):
            incomplete_documents += 1
        if not isinstance(raw.get("payload_sha256"), str) or not _HEX64.fullmatch(raw["payload_sha256"]):
            invalid_hash_documents += 1
        if _parse_time(raw.get("fetched_at")) is None:
            invalid_time_documents += 1
        field_status = raw["field_status"]
        if any(_status(value) is None for value in field_status.values()):
            invalid_status_documents += 1
        base = raw.get("facts", {}).get("base")
        if isinstance(base, dict) and "id" in base and _is_int(base["id"]) and base["id"] != raw["tmdb_id"]:
            payload_id_conflicts.append(
                {"index": index, "movie_id": raw["movie_id"], "tmdb_id": raw["tmdb_id"], "payload_id": base["id"]}
            )

    by_movie = {}
    pairs = set()
    movie_to_tmdb = {}
    tmdb_to_movie = {}
    raw_reported = {topic: _empty_status_counts() for topic in TOPICS}
    for index, document in valid:
        movie_id = document["movie_id"]
        tmdb_id = document["tmdb_id"]
        pairs.add((movie_id, tmdb_id))
        by_movie.setdefault(movie_id, []).append((index, document))
        movie_to_tmdb.setdefault(movie_id, set()).add(tmdb_id)
        tmdb_to_movie.setdefault(tmdb_id, set()).add(movie_id)
        for topic in TOPICS:
            reported = _status(document["field_status"].get(topic))
            if reported is None:
                raw_reported[topic]["FAILED"] += 1
            else:
                raw_reported[topic][reported] += 1

    def order_key(item):
        index, document = item
        stamp = _parse_time(document.get("fetched_at"))
        if stamp:
            return (
                stamp.toordinal(), stamp.hour, stamp.minute, stamp.second,
                stamp.microsecond, index,
            )
        return (-1, 0, 0, 0, 0, index)

    for movie_id in by_movie:
        by_movie[movie_id].sort(key=order_key)

    denominator = len(by_movie)
    topic_result = {}
    mismatch_examples = {}
    quality_counts = {}
    for topic in TOPICS:
        counts = _empty_status_counts()
        quality = {
            "partial_present_documents": 0,
            "all_invalid_documents": 0,
            "mismatched_documents": 0,
            "invalid_item_count": 0,
            "missing_status_documents": 0,
            "invalid_status_documents": 0,
        }
        for movie_id, docs in by_movie.items():
            selected = None
            fallback = None
            selected_items = 0
            for _index, document in reversed(docs):
                effective, _reported, mismatch, item_valid, item_invalid, code = _derive_topic(topic, document)
                if mismatch:
                    quality["mismatched_documents"] += 1
                    _add_example(mismatch_examples.setdefault(topic, []), {"movie_id": movie_id, "code": code}, max_details)
                if item_invalid:
                    quality["invalid_item_count"] += item_invalid
                if effective == "PRESENT" and item_invalid:
                    quality["partial_present_documents"] += 1
                if code == "ALL_ITEMS_INVALID":
                    quality["all_invalid_documents"] += 1
                if code == "INVALID_STATUS":
                    quality["invalid_status_documents"] += 1
                if code == "MISSING_STATUS":
                    quality["missing_status_documents"] += 1
                if effective in ("PRESENT", "EMPTY") and selected is None:
                    # A reported PRESENT that actually has no facts is a malformed
                    # success; do not let it clear an older valid observation.
                    if code == "PRESENT_WITHOUT_FACTS":
                        fallback = fallback or "FAILED"
                        continue
                    selected = effective
                    selected_items = item_valid
                elif selected is None and fallback is None:
                    fallback = effective
            counts[selected or fallback or "NOT_FETCHED"] += 1
            if selected == "PRESENT":
                quality["valid_item_count"] = quality.get("valid_item_count", 0) + selected_items
            elif selected == "EMPTY":
                quality["valid_item_count"] = quality.get("valid_item_count", 0)
        quality.setdefault("valid_item_count", 0)
        present = counts["PRESENT"]
        topic_result[topic] = {
            "status_counts": counts,
            "reported_document_counts": raw_reported[topic],
            "denominator": denominator,
            "present": present,
            "empty": counts["EMPTY"],
            "missing": counts["MISSING"],
            "not_fetched": counts["NOT_FETCHED"],
            "failed": counts["FAILED"],
            "uncovered": denominator - present,
            "coverage_rate": round(present / denominator, 6) if denominator else None,
            "quality": quality,
            "mismatch_examples": mismatch_examples.get(topic, []),
        }
        quality_counts[topic] = quality

    movie_conflicts = []
    for movie_id in sorted(movie_to_tmdb):
        values = sorted(movie_to_tmdb[movie_id])
        if len(values) > 1:
            movie_conflicts.append({"movie_id": movie_id, "tmdb_ids": values})
    tmdb_conflicts = []
    for tmdb_id in sorted(tmdb_to_movie):
        values = sorted(tmdb_to_movie[tmdb_id])
        if len(values) > 1:
            tmdb_conflicts.append({"tmdb_id": tmdb_id, "movie_ids": values})
    duplicate_pairs = len(valid) - len(pairs)
    total_movie_ids = len(movie_to_tmdb)
    total_tmdb_ids = len(tmdb_to_movie)
    total_pairs = len(pairs)

    # A document with only a TMDB id (for example a board entry) is not a local
    # movie.  It stays in the bounded review list and is excluded above.
    for index, raw in enumerate(records):
        if not isinstance(raw, dict) or "_audit_parse_error" in raw:
            continue
        if _is_int(raw.get("movie_id")) and raw["movie_id"] > 0:
            continue
        if _is_int(raw.get("tmdb_id")) and raw["tmdb_id"] > 0:
            _add_example(
                identity_review,
                {"index": index, "movie_id": None, "tmdb_id": raw["tmdb_id"], "reason": "TMDB_ONLY_NOT_LOCAL"},
                max_details,
            )

    ratings = {"threshold": min_votes, "basis": "latest_valid_document_per_movie"}
    budget = {"basis": "latest_valid_document_per_movie"}
    revenue = {"basis": "latest_valid_document_per_movie"}
    rating_samples = []
    money_counts = {"budget": {"valid": 0, "zero_unknown": 0, "missing": 0, "invalid": 0},
                    "revenue": {"valid": 0, "zero_unknown": 0, "missing": 0, "invalid": 0}}
    invalid_metric_documents = 0
    for movie_id, docs in by_movie.items():
        # Metrics and money use the latest valid document; they are current
        # observations, not a union of historical values.
        _index, document = docs[-1]
        facts = document["facts"]
        metrics, invalid_metrics = _metrics_values(facts)
        if invalid_metrics:
            invalid_metric_documents += 1
        score = metrics.get("vote_average")
        votes = metrics.get("vote_count")
        score_valid = _is_number(score) and 0 <= score <= 10
        votes_valid = _is_int(votes) and votes >= 0
        if score is None:
            ratings["score_missing"] = ratings.get("score_missing", 0) + 1
        elif not score_valid:
            ratings["score_invalid"] = ratings.get("score_invalid", 0) + 1
        if votes is None:
            ratings["votes_missing"] = ratings.get("votes_missing", 0) + 1
        elif not votes_valid:
            ratings["votes_invalid"] = ratings.get("votes_invalid", 0) + 1
        elif votes == 0:
            ratings["votes_zero"] = ratings.get("votes_zero", 0) + 1
        if score_valid and votes_valid and votes >= min_votes:
            ratings["valid_samples"] = ratings.get("valid_samples", 0) + 1
            rating_samples.append(float(score))
        elif score_valid and votes_valid:
            ratings["below_threshold_samples"] = ratings.get("below_threshold_samples", 0) + 1
        elif score_valid and not votes_valid:
            ratings["score_without_effective_votes"] = ratings.get("score_without_effective_votes", 0) + 1
        if score == 0 and votes_valid and votes >= min_votes:
            ratings["valid_zero_scores"] = ratings.get("valid_zero_scores", 0) + 1

        for name in ("budget", "revenue"):
            base = facts.get("base") if isinstance(facts.get("base"), dict) else {}
            value = base.get(name)
            if value is None:
                value = metrics.get(name)
            bucket = money_counts[name]
            if value is None:
                bucket["missing"] += 1
            elif _is_int(value) and value == 0:
                bucket["zero_unknown"] += 1
            elif _is_int(value) and value > 0:
                bucket["valid"] += 1
            else:
                bucket["invalid"] += 1

    ratings["valid_samples"] = ratings.get("valid_samples", 0)
    ratings["below_threshold_samples"] = ratings.get("below_threshold_samples", 0)
    ratings["score_missing"] = ratings.get("score_missing", 0)
    ratings["score_invalid"] = ratings.get("score_invalid", 0)
    ratings["votes_missing"] = ratings.get("votes_missing", 0)
    ratings["votes_invalid"] = ratings.get("votes_invalid", 0)
    ratings["votes_zero"] = ratings.get("votes_zero", 0)
    ratings["valid_zero_scores"] = ratings.get("valid_zero_scores", 0)
    ratings["score_without_effective_votes"] = ratings.get("score_without_effective_votes", 0)
    ratings["average_valid_score"] = round(sum(rating_samples) / len(rating_samples), 6) if rating_samples else None
    ratings["min_valid_score"] = min(rating_samples) if rating_samples else None
    ratings["max_valid_score"] = max(rating_samples) if rating_samples else None

    for name in ("budget", "revenue"):
        money_counts[name]["denominator"] = denominator
        money_counts[name]["coverage_rate"] = round(money_counts[name]["valid"] / denominator, 6) if denominator else None
    budget.update(money_counts["budget"])
    revenue.update(money_counts["revenue"])

    if not records:
        state = "EMPTY_INPUT"
    elif not valid:
        state = "NO_VALID_DOCUMENTS"
    else:
        state = "OK"

    issues = []
    for index, raw in enumerate(records):
        if not isinstance(raw, dict) or "_audit_parse_error" in raw:
            continue
        doc_issues = raw.get("issues")
        if not isinstance(doc_issues, list):
            continue
        for issue in doc_issues:
            if isinstance(issue, str) and issue.strip():
                issues.append(issue.strip())
    issue_counts = {}
    for issue in issues:
        if isinstance(issue, str):
            text = issue.strip()
            if len(text) <= 80 and re.fullmatch(r"[A-Za-z0-9_:-]+", text):
                issue_counts[text] = issue_counts.get(text, 0) + 1
            else:
                issue_counts["OTHER_ISSUE"] = issue_counts.get("OTHER_ISSUE", 0) + 1
    issue_rows = [{"code": code, "count": issue_counts[code]} for code in sorted(issue_counts)]

    identity = {
        "unique_movies": total_movie_ids,
        "unique_tmdb_ids": total_tmdb_ids,
        "unique_mappings": total_pairs,
        "duplicate_document_pairs": duplicate_pairs,
        "movie_id_to_multiple_tmdb_ids": movie_conflicts[:max_details],
        "tmdb_id_to_multiple_movie_ids": tmdb_conflicts[:max_details],
        "conflict_count": len(movie_conflicts) + len(tmdb_conflicts),
        "conflicts_truncated": len(movie_conflicts) > max_details or len(tmdb_conflicts) > max_details,
        "payload_id_conflicts": payload_id_conflicts[:max_details],
        "payload_id_conflict_count": len(payload_id_conflicts),
        "review_required": identity_review[:max_details],
        "review_required_count": len(identity_review),
    }
    totals = {
        "documents": len(records),
        "valid_documents": len(valid),
        "invalid_documents": invalid_documents,
        "unique_movies": total_movie_ids,
        "unique_tmdb_ids": total_tmdb_ids,
        "unique_mappings": total_pairs,
        "duplicate_document_pairs": duplicate_pairs,
        "identity_conflicts": identity["conflict_count"],
    }
    reliability = {
        "state": state,
        "empty_input": state == "EMPTY_INPUT",
        "no_valid_documents": state == "NO_VALID_DOCUMENTS",
        "bad_documents": bad,
        "bad_document_count": invalid_documents,
        "identity_review": identity["review_required"],
        "identity_review_count": identity["review_required_count"],
        "invalid_payload_sha256_documents": invalid_hash_documents,
        "invalid_fetched_at_documents": invalid_time_documents,
        "invalid_field_status_documents": invalid_status_documents,
        "incomplete_documents": incomplete_documents,
        "invalid_metric_documents": invalid_metric_documents,
        "status_mismatch_count": sum(q["mismatched_documents"] for q in quality_counts.values()),
        "invalid_topic_item_count": sum(q["invalid_item_count"] for q in quality_counts.values()),
        "issues": issue_rows[:max_details],
        "issues_truncated": len(issue_rows) > max_details,
        "known_boundaries": [
            "status labels are checked against facts; a failed/truncated record is not an empty result",
            "coverage denominator is unique positive movie_id; a TMDB-only board entry is not local coverage",
            "co-production countries may sum across films; ratings use a configurable vote threshold",
            "budget/revenue 0 is unknown, not verified earnings; no profit is computed",
            "no database, network, configuration, or live catalog was accessed by this audit",
        ],
    }
    result = {
        "schema_version": AUDIT_SCHEMA,
        "document_schema_version": DOCUMENT_SCHEMA,
        "input": source,
        "totals": totals,
        "identity": identity,
        "topics": topic_result,
        "ratings": ratings,
        "money": {"budget": budget, "revenue": revenue},
        "reliability": reliability,
        "method": {
            "coverage_denominator": "unique valid positive movie_id",
            "document_deduplication": "latest successful status per movie/topic; failure does not clear prior success; explicit EMPTY does",
            "reported_vs_effective": "status_counts are effective coverage; reported_document_counts retain source labels",
            "minimum_votes": min_votes,
        },
    }
    return result


def audit_to_json(documents, *, min_votes=50, max_details=20):
    """Return the audit as canonical, stable UTF-8 JSON text."""
    return json.dumps(
        audit_documents(documents, min_votes=min_votes, max_details=max_details),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Audit normalized TMDB supplement v1 documents offline.")
    parser.add_argument("path", help="explicit .json or .jsonl input")
    parser.add_argument("--min-votes", type=int, default=50)
    args = parser.parse_args()
    print(audit_to_json(args.path, min_votes=args.min_votes))