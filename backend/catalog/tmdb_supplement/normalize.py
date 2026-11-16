"""Pure TMDB movie-detail normalization for the supplement v1 contract.

This module deliberately has no Flask, SQLAlchemy, filesystem, network or
configuration dependency.  It turns one already-captured TMDB response into a
deterministic document; it never fetches or writes anything.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import date, datetime, timezone

SCHEMA_VERSION = "tmdb-supplement-v1"
FIELD_NAMES = (
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
STATUSES = (
    STATUS_NOT_FETCHED,
    STATUS_MISSING,
    STATUS_EMPTY,
    STATUS_PRESENT,
    STATUS_FAILED,
)

MAX_MOVIE_ID = 9223372036854775807
MAX_TMDB_ID = 9999999999
MAX_COUNT = 10 ** 15
MAX_TEXT = 2_000_000
JOB_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
COUNTRY_RE = re.compile(r"[A-Z]{2}\Z")
LANGUAGE_RE = re.compile(r"[a-z]{2,3}\Z")
IMAGE_RE = re.compile(r"/[A-Za-z0-9_-]+\.(?:jpg|jpeg|png|webp)\Z", re.I)
IMDB_RE = re.compile(r"tt\d{7,10}\Z")
CREDIT_RE = re.compile(r"[A-Za-z0-9_-]{1,100}\Z")
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
DATETIME_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:\d{2})\Z"
)
RELEASE_TYPE_NAMES = {
    1: "Premiere",
    2: "Theatrical (limited)",
    3: "Theatrical",
    4: "Digital",
    5: "Physical",
    6: "TV",
}


class NormalizeError(ValueError):
    """Base class for a caller-actionable normalization failure."""

    code = "NORMALIZE_ERROR"

    def __init__(self, code=None, message=None):
        self.code = code or self.code
        super().__init__(message or self.code)


class InvalidInput(NormalizeError):
    code = "INVALID_INPUT"


class IdentityConflict(NormalizeError):
    code = "TMDB_ID_MISMATCH"


class NonJsonNumber(NormalizeError):
    code = "NON_JSON_NUMBER"


class UpstreamError(NormalizeError):
    code = "UPSTREAM_DETAIL_FAILED"


def _issue(issues, code, field, message, *, severity="error", **extra):
    row = {
        "code": code,
        "field": field,
        "severity": severity,
        "message": message,
    }
    row.update(extra)
    issues.append(row)


# Upstream occasionally serialises a locale object as a flat Ruby hash inspect
# string, e.g. ``{"folded_name"=>"English", "iso_639_1"=>"en", ...}``.  The
# pattern below accepts only that literal shape: bounded length, no escapes,
# no nested braces, ``"key"=>"value"`` pairs separated by ``", "``.
_RUBY_FLAT_LOCALE = re.compile(
    r'\{(?:"[A-Za-z0-9_]{1,64}"\s*=>\s*"[^"\\{}\r\n]{0,200}")'
    r'(?:,\s*"[A-Za-z0-9_]{1,64}"\s*=>\s*"[^"\\{}\r\n]{0,200}")*\}'
)
_RUBY_PAIR = re.compile(r'"([A-Za-z0-9_]{1,64})"\s*=>\s*"([^"\\{}\r\n]{0,200})"')
_MAX_LOCALE_TEXT = 400


def _release_language(value):
    """Return ``(code, evidence)`` for a release locale, or ``(None, None)``.

    A plain 2..3 letter code is accepted as-is.  For the single known upstream
    serialisation shape the explicit ``iso_639_1`` value is used, but only when
    the whole string matches the strict literal pattern and carries exactly one
    such key whose value is itself a valid code.  Nothing is evaluated and no
    code is inferred from a language name.
    """
    if type(value) is str and LANGUAGE_RE.fullmatch(value):
        return value, None
    if type(value) is str and len(value) <= _MAX_LOCALE_TEXT and _RUBY_FLAT_LOCALE.fullmatch(value):
        pairs = _RUBY_PAIR.findall(value)
        keys = [key for key, _ in pairs]
        codes = [item for key, item in pairs if key == "iso_639_1"]
        if keys.count("iso_639_1") == 1 and len(codes) == 1 and LANGUAGE_RE.fullmatch(codes[0]):
            return codes[0], {"converted_from": "ruby_hash", "raw": value}
    return None, None


def _partial(issues, field, valid_count, invalid_count):
    if invalid_count:
        _issue(
            issues,
            "PARTIAL_INVALID",
            field,
            "部分元素无效；只保留可用事实",
            valid_count=valid_count,
            invalid_count=invalid_count,
        )


def _canonical(value):
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except ValueError as exc:
        # json.dumps(..., allow_nan=False) rejects NaN/Infinity by design.
        raise NonJsonNumber("NON_JSON_NUMBER", "payload 含 NaN 或 Infinity") from exc
    except TypeError as exc:
        raise InvalidInput("PAYLOAD_NOT_JSON", "payload 不是可规范化 JSON") from exc


def _positive_int(value, name):
    if type(value) is not int or not 1 <= value <= (MAX_TMDB_ID if name == "tmdb_id" else MAX_MOVIE_ID):
        raise InvalidInput("INVALID_" + name.upper(), f"{name} 必须是范围内的正整数")
    return value


def _fetched_at(value):
    if type(value) is not str or not value:
        raise InvalidInput("INVALID_FETCHED_AT", "fetched_at 必须是非空 ISO8601 字符串")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError as exc:
        raise InvalidInput("INVALID_FETCHED_AT", "fetched_at 不是合法 ISO8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise InvalidInput("FETCHED_AT_WITHOUT_TIMEZONE", "fetched_at 必须带时区")
    return parsed.astimezone(timezone.utc).isoformat()


def _job_id(value):
    if type(value) is not str or not JOB_RE.fullmatch(value):
        raise InvalidInput("INVALID_JOB_ID", "job_id 必须是 1..128 位有界标识")
    return value


def _text(value, field, issues, *, required=False, max_len=500):
    if value is None or value == "":
        if required:
            _issue(issues, "INVALID_BASE_FIELD", field, "必填文本缺失")
        return None
    if type(value) is not str or len(value) > max_len or (required and not value.strip()):
        _issue(issues, "INVALID_BASE_FIELD", field, "文本为空、类型错误或超长")
        return None
    return value


def _optional_text(value, field, issues, *, max_len=500):
    if value is None or value == "":
        return None
    if type(value) is not str or len(value) > max_len:
        _issue(issues, "INVALID_BASE_FIELD", field, "文本类型错误或超长")
        return None
    return value


def _optional_date(value, field, issues):
    if value is None or value == "":
        return None
    if type(value) is not str or not DATE_RE.fullmatch(value):
        _issue(issues, "INVALID_BASE_FIELD", field, "日期必须是 YYYY-MM-DD")
        return None
    try:
        date.fromisoformat(value)
    except ValueError:
        _issue(issues, "INVALID_BASE_FIELD", field, "日期不是有效日历日")
        return None
    return value


def _optional_int(value, field, issues, *, maximum=MAX_MOVIE_ID):
    if value is None:
        return None
    if type(value) is not int or not 0 <= value <= maximum:
        _issue(issues, "INVALID_BASE_FIELD", field, "整数越界或类型错误")
        return None
    return value


def _optional_bool(value, field, issues):
    if value is None:
        return None
    if type(value) is not bool:
        _issue(issues, "INVALID_BASE_FIELD", field, "布尔字段类型错误")
        return None
    return value


def _optional_language(value, field, issues):
    if value is None or value == "":
        return None
    if type(value) is not str or not LANGUAGE_RE.fullmatch(value):
        _issue(issues, "INVALID_LANGUAGE_CODE", field, "语言代码缺失或格式错误；不从名称猜测")
        return None
    return value


def _optional_image(value, field, issues):
    if value is None or value == "":
        return None
    if type(value) is not str or not IMAGE_RE.fullmatch(value):
        _issue(issues, "INVALID_BASE_FIELD", field, "图片路径格式错误")
        return None
    return value


def _status(rows, invalid):
    if rows:
        return STATUS_FAILED if invalid else STATUS_PRESENT
    return STATUS_FAILED if invalid else STATUS_EMPTY


def _subresource_error(raw):
    if type(raw) is not dict:
        return None
    status = raw.get("status_code")
    failed = raw.get("success") is False or (type(status) is int and not 200 <= status < 300)
    if failed:
        out = {}
        if type(status) is int:
            out["status_code"] = status
        message = raw.get("status_message")
        if type(message) is str and len(message) <= 500:
            out["status_message"] = message
        return out
    errors = raw.get("errors")
    if errors:
        return {"errors": "upstream errors present"}
    return None


def _genres(value, issues):
    if value is None or value == "":
        return []
    if type(value) is not list:
        _issue(issues, "INVALID_GENRES", "genres", "genres 必须是列表")
        return []
    out, seen, invalid = [], {}, 0
    for index, row in enumerate(value):
        if type(row) is not dict:
            invalid += 1
            _issue(issues, "INVALID_GENRE", "genres", "genre 元素不是对象", index=index)
            continue
        gid, name = row.get("id"), row.get("name")
        if type(gid) is not int or gid <= 0:
            invalid += 1
            _issue(issues, "INVALID_GENRE_ID", "genres", "genre id 必须是正整数", index=index)
            continue
        if type(name) is not str or not name.strip() or len(name) > 500:
            invalid += 1
            _issue(issues, "INVALID_GENRE_NAME", "genres", "genre name 必须是非空文本", index=index)
            continue
        if gid in seen:
            if seen[gid]["name"] != name:
                invalid += 1
                _issue(issues, "DUPLICATE_GENRE_CONFLICT", "genres", "同一 genre id 名称冲突", index=index)
            continue
        seen[gid] = {"id": gid, "name": name}
        out.append(seen[gid])
    if invalid:
        _partial(issues, "genres", len(out), invalid)
    return out


def _countries(value, present, issues):
    if not present:
        return STATUS_MISSING, []
    if value is None:
        return STATUS_MISSING, []
    if type(value) is not list:
        _issue(issues, "INVALID_COUNTRIES", "countries", "production_countries 必须是列表")
        return STATUS_FAILED, []
    if not value:
        return STATUS_EMPTY, []
    out, seen, invalid = [], {}, 0
    for index, row in enumerate(value):
        if type(row) is not dict:
            invalid += 1
            _issue(issues, "INVALID_COUNTRY", "countries", "国家元素不是对象", index=index)
            continue
        code, name = row.get("iso_3166_1"), row.get("name")
        if type(code) is not str or not COUNTRY_RE.fullmatch(code):
            invalid += 1
            _issue(issues, "INVALID_COUNTRY_CODE", "countries", "制作国家代码必须是两位大写 ISO 3166-1", index=index)
            continue
        if type(name) is not str or not name.strip() or len(name) > 255:
            # Keep the usable ISO identity but never invent a display name.
            invalid += 1
            _issue(issues, "INVALID_COUNTRY_NAME", "countries", "制作国家名称缺失", index=index)
            name = None
        if code in seen:
            if seen[code]["name"] != name:
                invalid += 1
                _issue(issues, "DUPLICATE_COUNTRY_CONFLICT", "countries", "同一国家代码名称冲突", index=index)
            continue
        seen[code] = {"iso_3166_1": code, "name": name}
        out.append(seen[code])
    if invalid:
        _partial(issues, "countries", len(out), invalid)
    return _status(out, invalid), out


def _companies(value, present, issues):
    if not present:
        return STATUS_MISSING, []
    if value is None:
        return STATUS_MISSING, []
    if type(value) is not list:
        _issue(issues, "INVALID_COMPANIES", "companies", "production_companies 必须是列表")
        return STATUS_FAILED, []
    if not value:
        return STATUS_EMPTY, []
    out, seen, invalid = [], {}, 0
    for index, row in enumerate(value):
        if type(row) is not dict:
            invalid += 1
            _issue(issues, "INVALID_COMPANY", "companies", "公司元素不是对象", index=index)
            continue
        cid, name = row.get("id"), row.get("name")
        if type(cid) is not int or cid <= 0:
            invalid += 1
            _issue(issues, "INVALID_COMPANY_ID", "companies", "公司 id 必须是正整数", index=index)
            continue
        if type(name) is not str or not name.strip() or len(name) > 255:
            invalid += 1
            _issue(issues, "INVALID_COMPANY_NAME", "companies", "公司名称必须是非空文本", index=index)
            name = None
        origin = row.get("origin_country")
        if origin in (None, ""):
            origin = None
        elif type(origin) is not str or not COUNTRY_RE.fullmatch(origin):
            invalid += 1
            _issue(issues, "INVALID_COMPANY_COUNTRY", "companies", "公司来源国家必须是两位大写 ISO 代码", index=index)
            origin = None
        logo = _optional_image(row.get("logo_path"), "companies.logo_path", issues)
        if logo is None and row.get("logo_path") not in (None, ""):
            invalid += 1
        item = {"id": cid, "name": name, "origin_country": origin, "logo_path": logo}
        if cid in seen:
            if seen[cid] != item:
                invalid += 1
                _issue(issues, "DUPLICATE_COMPANY_CONFLICT", "companies", "同一公司 id 内容冲突", index=index)
            continue
        seen[cid] = item
        out.append(item)
    if invalid:
        _partial(issues, "companies", len(out), invalid)
    return _status(out, invalid), out


def _languages(value, present, issues):
    if not present:
        return STATUS_MISSING, []
    if value is None:
        return STATUS_MISSING, []
    if type(value) is not list:
        _issue(issues, "INVALID_LANGUAGES", "spoken_languages", "spoken_languages 必须是列表")
        return STATUS_FAILED, []
    if not value:
        return STATUS_EMPTY, []
    out, seen, invalid = [], {}, 0
    for index, row in enumerate(value):
        if type(row) is not dict:
            invalid += 1
            _issue(issues, "INVALID_LANGUAGE", "spoken_languages", "语言元素不是对象", index=index)
            continue
        code = row.get("iso_639_1")
        if type(code) is not str or not LANGUAGE_RE.fullmatch(code):
            invalid += 1
            _issue(issues, "INVALID_LANGUAGE_CODE", "spoken_languages", "iso_639_1 缺失或格式错误；不从名称猜测", index=index)
            continue
        english_name = _optional_text(row.get("english_name"), "spoken_languages.english_name", issues)
        name = _optional_text(row.get("name"), "spoken_languages.name", issues)
        if row.get("english_name") not in (None, "") and english_name is None:
            invalid += 1
        if row.get("name") not in (None, "") and name is None:
            invalid += 1
        item = {"iso_639_1": code, "english_name": english_name, "name": name}
        if code in seen:
            if seen[code] != item:
                invalid += 1
                _issue(issues, "DUPLICATE_LANGUAGE_CONFLICT", "spoken_languages", "同一语言代码内容冲突", index=index)
            continue
        seen[code] = item
        out.append(item)
    if invalid:
        _partial(issues, "spoken_languages", len(out), invalid)
    return _status(out, invalid), out


def _keywords(raw, present, issues):
    if not present:
        return STATUS_NOT_FETCHED, []
    if raw is None:
        return STATUS_MISSING, []
    error = _subresource_error(raw)
    if error:
        _issue(issues, "UPSTREAM_SUBRESOURCE_FAILED", "keywords", "keywords 子接口失败", **error)
        return STATUS_FAILED, []
    if type(raw) is list:
        rows = raw
    elif type(raw) is dict:
        if "keywords" not in raw:
            _issue(issues, "INVALID_KEYWORDS", "keywords", "keywords 对象缺少 keywords 列表")
            return STATUS_FAILED, []
        rows = raw.get("keywords")
        if rows is None:
            return STATUS_MISSING, []
    else:
        _issue(issues, "INVALID_KEYWORDS", "keywords", "keywords 必须是列表或对象")
        return STATUS_FAILED, []
    if type(rows) is not list:
        _issue(issues, "INVALID_KEYWORDS", "keywords", "keywords 元素必须是列表")
        return STATUS_FAILED, []
    if not rows:
        return STATUS_EMPTY, []
    out, seen, invalid = [], {}, 0
    for index, row in enumerate(rows):
        if type(row) is not dict:
            invalid += 1
            _issue(issues, "INVALID_KEYWORD", "keywords", "keyword 元素不是对象", index=index)
            continue
        kid, name = row.get("id"), row.get("name")
        if type(kid) is not int or kid <= 0:
            invalid += 1
            _issue(issues, "INVALID_KEYWORD_ID", "keywords", "keyword id 必须是正整数", index=index)
            continue
        if type(name) is not str or not name.strip() or len(name) > 500:
            invalid += 1
            _issue(issues, "INVALID_KEYWORD_NAME", "keywords", "keyword name 必须是非空文本", index=index)
            continue
        item = {"id": kid, "name": name}
        if kid in seen:
            if seen[kid] != item:
                invalid += 1
                _issue(issues, "DUPLICATE_KEYWORD_CONFLICT", "keywords", "同一 keyword id 名称冲突", index=index)
            continue
        seen[kid] = item
        out.append(item)
    if invalid:
        _partial(issues, "keywords", len(out), invalid)
    return _status(out, invalid), out


def _release_time(raw):
    if type(raw) is not str:
        raise ValueError("release_date 不是字符串")
    if DATE_RE.fullmatch(raw):
        date.fromisoformat(raw)
        return {
            "release_date": raw,
            "release_date_raw": raw,
            "release_date_utc": None,
            "precision": "day",
        }
    if not DATETIME_RE.fullmatch(raw):
        raise ValueError("release_date 不是 YYYY-MM-DD 或带时区 ISO8601")
    parsed = datetime.fromisoformat(raw[:-1] + "+00:00" if raw.endswith("Z") else raw)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("release_date 缺少时区")
    utc = parsed.astimezone(timezone.utc)
    time_part = raw[:-1] if raw.endswith("Z") else re.sub(r"[+-]\d{2}:\d{2}\Z", "", raw)
    time_part = time_part.split("T", 1)[1]
    if "." in time_part:
        digits = len(time_part.split(".", 1)[1])
        precision = "millisecond" if digits <= 3 else "microsecond"
    elif time_part.count(":") == 1:
        precision = "minute"
    else:
        precision = "second"
    return {
        "release_date": utc.date().isoformat(),
        "release_date_raw": raw,
        "release_date_utc": utc.isoformat(),
        "precision": precision,
    }


def _releases(raw, present, issues):
    if not present:
        return STATUS_NOT_FETCHED, []
    if raw is None:
        return STATUS_MISSING, []
    error = _subresource_error(raw)
    if error:
        _issue(issues, "UPSTREAM_SUBRESOURCE_FAILED", "releases", "release_dates 子接口失败", **error)
        return STATUS_FAILED, []
    if type(raw) is dict:
        if "results" not in raw:
            _issue(issues, "INVALID_RELEASES", "releases", "release_dates 对象缺少 results")
            return STATUS_FAILED, []
        regions = raw.get("results")
        if regions is None:
            return STATUS_MISSING, []
    elif type(raw) is list:
        regions = raw
    else:
        _issue(issues, "INVALID_RELEASES", "releases", "release_dates 必须是列表或对象")
        return STATUS_FAILED, []
    if type(regions) is not list:
        _issue(issues, "INVALID_RELEASES", "releases", "release_dates results 必须是列表")
        return STATUS_FAILED, []
    if not regions:
        return STATUS_EMPTY, []
    out, seen, invalid = [], set(), 0
    for region_index, region in enumerate(regions):
        if type(region) is not dict:
            invalid += 1
            _issue(issues, "INVALID_RELEASE_REGION", "releases", "发行地区不是对象", index=region_index)
            continue
        code = region.get("iso_3166_1")
        if type(code) is not str or not COUNTRY_RE.fullmatch(code):
            invalid += 1
            _issue(issues, "INVALID_RELEASE_COUNTRY", "releases", "发行地区代码必须是两位大写 ISO 代码", index=region_index)
            continue
        rows = region.get("release_dates")
        if type(rows) is not list:
            invalid += 1
            _issue(issues, "INVALID_RELEASE_DATES", "releases", "发行地区 release_dates 必须是列表", index=region_index)
            continue
        normalized = []
        for release_index, row in enumerate(rows):
            if type(row) is not dict:
                invalid += 1
                _issue(issues, "INVALID_RELEASE", "releases", "发行记录不是对象", region=code, index=release_index)
                continue
            rtype = row.get("type")
            if type(rtype) is not int or rtype not in RELEASE_TYPE_NAMES:
                invalid += 1
                _issue(issues, "INVALID_RELEASE_TYPE", "releases", "发行类型必须是 1..6", region=code, index=release_index)
                continue
            try:
                when = _release_time(row.get("release_date"))
            except (ValueError, TypeError) as exc:
                invalid += 1
                _issue(issues, "INVALID_RELEASE_DATE", "releases", str(exc), region=code, index=release_index)
                continue
            cert = row.get("certification")
            if cert in (None, ""):
                cert = None
            elif type(cert) is not str or len(cert) > 64:
                invalid += 1
                _issue(issues, "INVALID_RELEASE_CERTIFICATION", "releases", "认证字段类型错误或超长", region=code, index=release_index)
                cert = None
            note = row.get("note")
            if note in (None, ""):
                note = None
            elif type(note) is not str or len(note) > 500:
                invalid += 1
                _issue(issues, "INVALID_RELEASE_NOTE", "releases", "note 类型错误或超长", region=code, index=release_index)
                note = None
            raw_language = row.get("iso_639_1")
            language, converted = _release_language(raw_language)
            if raw_language in (None, ""):
                language = None
            elif language is None:
                invalid += 1
                _issue(issues, "INVALID_RELEASE_LANGUAGE", "releases", "发行语言代码格式错误", region=code, index=release_index)
            elif converted is not None:
                _issue(issues, "RELEASE_LANGUAGE_CONVERTED", "releases",
                       "语言代码取自上游已知序列化格式中的 iso_639_1 明示值",
                       severity="warning", region=code, index=release_index, **converted)
            item = {
                "type": rtype,
                "type_name": RELEASE_TYPE_NAMES[rtype],
                **when,
                "certification": cert,
                "note": note,
                "iso_639_1": language,
            }
            fingerprint_payload = {
                "iso_3166_1": code,
                "type": item["type"],
                "release_date_raw": item["release_date_raw"],
                "certification": item["certification"],
                "note": item["note"],
                "iso_639_1": item["iso_639_1"],
            }
            item["dedupe_key"] = hashlib.sha256(
                _canonical(fingerprint_payload).encode("utf-8")
            ).hexdigest()
            if item["dedupe_key"] in seen:
                continue
            seen.add(item["dedupe_key"])
            normalized.append(item)
        if normalized:
            out.append({"iso_3166_1": code, "release_dates": normalized})
    if invalid:
        _partial(issues, "releases", sum(len(r["release_dates"]) for r in out), invalid)
    return _status(out, invalid), out


def _credits(raw, present, issues):
    if not present:
        return STATUS_NOT_FETCHED, []
    if raw is None:
        return STATUS_MISSING, []
    error = _subresource_error(raw)
    if error:
        _issue(issues, "UPSTREAM_SUBRESOURCE_FAILED", "credits", "credits 子接口失败", **error)
        return STATUS_FAILED, []
    if type(raw) is not dict:
        _issue(issues, "INVALID_CREDITS", "credits", "credits 必须是对象")
        return STATUS_FAILED, []
    cast, crew = raw.get("cast"), raw.get("crew")
    if cast is None and crew is None:
        if "cast" in raw and "crew" in raw:
            return STATUS_MISSING, []
        _issue(issues, "INVALID_CREDITS", "credits", "credits 缺少 cast/crew 列表")
        return STATUS_FAILED, []
    invalid = 0
    if type(cast) is not list:
        invalid += 1
        _issue(issues, "INVALID_CREDITS", "credits", "credits.cast 必须是列表")
        cast = []
    if type(crew) is not list:
        invalid += 1
        _issue(issues, "INVALID_CREDITS", "credits", "credits.crew 必须是列表")
        crew = []
    if not cast and not crew and not invalid:
        return STATUS_EMPTY, []
    out, seen = [], {}
    for role, rows in (("cast", cast), ("crew", crew)):
        for index, row in enumerate(rows):
            row_issue_start = len(issues)
            invalid_before_row = invalid
            if type(row) is not dict:
                invalid += 1
                _issue(issues, "INVALID_CREDIT", "credits", f"{role} 元素不是对象", role=role, index=index)
                continue
            person_id, credit_id = row.get("id"), row.get("credit_id")
            name = row.get("name")
            if type(person_id) is not int or not 1 <= person_id <= MAX_TMDB_ID:
                invalid += 1
                _issue(issues, "INVALID_PERSON_ID", "credits", "person id 必须是范围内的正整数", role=role, index=index)
                continue
            if type(credit_id) is not str or not CREDIT_RE.fullmatch(credit_id):
                invalid += 1
                _issue(issues, "INVALID_CREDIT_ID", "credits", "credit_id 缺失或格式错误", role=role, index=index)
                continue
            if type(name) is not str or not name.strip() or len(name) > 500:
                invalid += 1
                _issue(issues, "INVALID_PERSON_NAME", "credits", "演职员姓名必须是非空文本", role=role, index=index)
                continue
            original_name = _optional_text(row.get("original_name"), "credits.original_name", issues)
            profile = _optional_image(row.get("profile_path"), "credits.profile_path", issues)
            if profile is None and row.get("profile_path") not in (None, ""):
                invalid += 1
            item = {
                "credit_id": credit_id,
                "person_id": person_id,
                "name": name,
                "original_name": original_name,
                "role_type": role,
                "character": None,
                "order": None,
                "job": None,
                "department": None,
                "profile_path": profile,
            }
            if role == "cast":
                character = row.get("character")
                if character not in (None, ""):
                    if type(character) is not str or len(character) > 500:
                        invalid += 1
                        _issue(issues, "INVALID_CHARACTER", "credits", "角色名类型错误或超长", index=index)
                    else:
                        item["character"] = character
                order = row.get("order")
                if order is not None:
                    if type(order) is not int or not 0 <= order <= 100000:
                        invalid += 1
                        _issue(issues, "INVALID_CREDIT_ORDER", "credits", "演员 order 必须是 0..100000 整数", index=index)
                    else:
                        item["order"] = order
            else:
                job, department = row.get("job"), row.get("department")
                if type(job) is not str or not job.strip():
                    invalid += 1
                    _issue(issues, "INVALID_CREW_JOB", "credits", "crew job 必须是非空文本", index=index)
                else:
                    item["job"] = job
                if type(department) is not str or not department.strip():
                    invalid += 1
                    _issue(issues, "INVALID_CREW_DEPARTMENT", "credits", "crew department 必须是非空文本", index=index)
                else:
                    item["department"] = department
            if len(issues) > row_issue_start and invalid == invalid_before_row:
                invalid += 1
            key = (role, credit_id)
            if key in seen:
                # A repeated identical key is harmless; a differing row is not.
                if seen[key] != item:
                    invalid += 1
                    _issue(issues, "DUPLICATE_CREDIT_CONFLICT", "credits", "同一 credit_id 内容冲突", role=role, index=index)
                continue
            seen[key] = item
            out.append(item)
    if invalid:
        _partial(issues, "credits", len(out), invalid)
    return _status(out, invalid), out


def _metrics(payload, issues):
    keys = ("vote_average", "vote_count", "popularity")
    present = [key for key in keys if key in payload]
    if not present:
        return STATUS_MISSING, {key: None for key in keys}
    facts = {key: None for key in keys}
    invalid = 0
    for key in keys:
        if key not in payload:
            invalid += 1
            _issue(issues, "MISSING_METRIC", "metrics", f"{key} 未提供")
            continue
        value = payload.get(key)
        if value is None:
            continue
        if key == "vote_count":
            if type(value) is not int or not 0 <= value <= MAX_COUNT:
                invalid += 1
                _issue(issues, "INVALID_VOTE_COUNT", "metrics", "vote_count 必须是非负整数或 null")
            else:
                facts[key] = value
            continue
        if type(value) not in (int, float) or type(value) is bool or not math.isfinite(value):
            invalid += 1
            _issue(issues, "INVALID_" + key.upper(), "metrics", f"{key} 必须是有限数值或 null")
            continue
        maximum = 10 if key == "vote_average" else MAX_COUNT
        if not 0 <= value <= maximum:
            invalid += 1
            _issue(issues, "INVALID_" + key.upper(), "metrics", f"{key} 超出范围")
            continue
        facts[key] = value
    if invalid:
        return STATUS_FAILED, facts
    return STATUS_PRESENT, facts


def normalize_movie(payload, *, movie_id, tmdb_id, fetched_at, job_id):
    """Normalize one TMDB movie-detail/append response.

    Status meanings are intentionally strict:

    * NOT_FETCHED: an append sub-interface key was never present.
    * MISSING: a field exists but is null, or a root field is absent.
    * EMPTY: the upstream explicitly returned a successful empty collection.
    * PRESENT: every supplied element/field was valid.
    * FAILED: malformed, partial-invalid, or upstream sub-resource failure;
      usable valid facts are still returned alongside error issues.
    """
    movie_id = _positive_int(movie_id, "movie_id")
    tmdb_id = _positive_int(tmdb_id, "tmdb_id")
    fetched_at = _fetched_at(fetched_at)
    job_id = _job_id(job_id)
    if type(payload) is not dict:
        raise InvalidInput("PAYLOAD_NOT_OBJECT", "payload 根必须是对象")
    if payload.get("success") is False or (
        type(payload.get("status_code")) is int and payload["status_code"] >= 400
    ):
        raise UpstreamError("UPSTREAM_DETAIL_FAILED", "TMDB 详情响应是错误响应")
    payload_id = payload.get("id")
    if type(payload_id) is not int:
        raise InvalidInput("INVALID_TMDB_ID", "payload.id 必须是正整数")
    if payload_id != tmdb_id:
        # A title/year is never used to repair an identity conflict.
        raise IdentityConflict("TMDB_ID_MISMATCH", f"payload.id={payload_id} 与 tmdb_id={tmdb_id} 不一致")
    payload_sha256 = hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()

    issues = []
    base_errors = 0
    before = len(issues)
    base = {
        "id": tmdb_id,
        "title": _text(payload.get("title"), "title", issues, required=True),
        "original_title": _optional_text(payload.get("original_title"), "original_title", issues, max_len=500),
        "release_date": _optional_date(payload.get("release_date"), "release_date", issues),
        "runtime": _optional_int(payload.get("runtime"), "runtime", issues, maximum=100000),
        "original_language": _optional_language(payload.get("original_language"), "original_language", issues),
        "overview": _optional_text(payload.get("overview"), "overview", issues, max_len=MAX_TEXT),
        "genres": _genres(payload.get("genres"), issues),
        "imdb_id": None,
        "poster_path": _optional_image(payload.get("poster_path"), "poster_path", issues),
        "backdrop_path": _optional_image(payload.get("backdrop_path"), "backdrop_path", issues),
        "adult": _optional_bool(payload.get("adult"), "adult", issues),
        "budget": _optional_int(payload.get("budget"), "budget", issues),
        "revenue": _optional_int(payload.get("revenue"), "revenue", issues),
    }
    imdb = payload.get("imdb_id")
    if imdb in (None, ""):
        base["imdb_id"] = None
    elif type(imdb) is not str or not IMDB_RE.fullmatch(imdb):
        base_errors += 1
        _issue(issues, "INVALID_IMDB_ID", "base", "imdb_id 格式错误")
    else:
        base["imdb_id"] = imdb
    base["budget_known"] = base["budget"] is not None and base["budget"] > 0
    base["revenue_known"] = base["revenue"] is not None and base["revenue"] > 0
    # No profit calculation: 0 budget/revenue means unknown, not verified zero.
    if len(issues) != before:
        base_errors += 1
    base_status = STATUS_FAILED if base_errors else STATUS_PRESENT

    countries_status, countries = _countries(
        payload.get("production_countries"), "production_countries" in payload, issues
    )
    companies_status, companies = _companies(
        payload.get("production_companies"), "production_companies" in payload, issues
    )
    languages_status, spoken_languages = _languages(
        payload.get("spoken_languages"), "spoken_languages" in payload, issues
    )
    keywords_status, keywords = _keywords(
        payload.get("keywords"), "keywords" in payload, issues
    )
    releases_status, releases = _releases(
        payload.get("release_dates"), "release_dates" in payload, issues
    )
    credits_status, credits = _credits(
        payload.get("credits"), "credits" in payload, issues
    )
    metrics_status, metrics = _metrics(payload, issues)

    field_status = {
        "base": base_status,
        "countries": countries_status,
        "companies": companies_status,
        "spoken_languages": languages_status,
        "keywords": keywords_status,
        "releases": releases_status,
        "credits": credits_status,
        "metrics": metrics_status,
    }
    facts = {
        "base": base,
        "countries": countries,
        "companies": companies,
        "spoken_languages": spoken_languages,
        "keywords": keywords,
        "releases": releases,
        "credits": credits,
        "metrics": metrics,
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "movie_id": movie_id,
        "tmdb_id": tmdb_id,
        "fetched_at": fetched_at,
        "job_id": job_id,
        "payload_sha256": payload_sha256,
        "field_status": field_status,
        "facts": facts,
        "issues": issues,
    }


__all__ = [
    "SCHEMA_VERSION",
    "FIELD_NAMES",
    "STATUSES",
    "NormalizeError",
    "InvalidInput",
    "IdentityConflict",
    "NonJsonNumber",
    "UpstreamError",
    "normalize_movie",
]