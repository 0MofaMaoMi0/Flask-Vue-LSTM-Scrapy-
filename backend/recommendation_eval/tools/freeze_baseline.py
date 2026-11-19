"""Freeze the current catalog.recommendation.recommend() verbatim.

This is a one-shot provenance tool, not a runtime dependency. It refuses to
run if the upstream file is not the exact byte snapshot recorded below, so a
later Codex edit cannot silently change Baseline V1.
"""
from __future__ import annotations

import ast
import hashlib
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve()
BACKEND = HERE.parents[2]
SOURCE = BACKEND / "catalog" / "recommendation.py"
TARGET = BACKEND / "recommendation_eval" / "baseline_v1.py"

EXPECTED_SOURCE_SHA256 = "d75483891c0d892e12ed1719c583107230dd784b8ff495767afa6a7d420225f2"
EXPECTED_FUNCTION_SHA256 = "ad255c8f379a53a9028d2174f7641e238da2caf6bf104447fb65e392db867a61"
EXPECTED_LINES = (53, 89)

HEADER = '''"""Frozen Baseline V1: catalog.recommendation.recommend().

This file is a byte-for-byte copy of the pure ranking function as it existed
on 2026-11-15. It is executable for offline comparison; it is not a rewrite,
not a trained model, and not the whole product channel pipeline.

Provenance
----------
baseline_id: {baseline_id}
source_path: {source_path}
source_sha256: {source_sha256}
function_name: recommend
function_lines: {first_line}-{last_line}
function_source_sha256: {function_sha256}
frozen_at: 2026-11-15
license_status: NOT_DECLARED_IN_REPO
license_note: 仓库根目录及 backend 未发现 LICENSE/COPYING/NOTICE；不推断或改写许可，
              使用或再分发前须由项目所有者确认原始权利。

Scope
-----
Only the pure function is frozen. `recommendation_boards.board()` adds channel
specific candidate filters, TMDB Bayesian blending, period caches and channel
rankers; those are documented in boards_reference.py and are NOT reproduced
here. This baseline is therefore not evidence that every old channel is
covered.

Default parameters (unchanged)
-------------------------------
limit=12, weights=None -> {{'favorite': 3.0, 'want': 2.0, 'rating': 1.0}},
baseline=False, baseline_field='demo_popularity',
mode='SYNTHETIC_RECOMMENDATION', cold_start_reason='合成热门冷启动基线',
profile_movies=None.

Behavior (unchanged)
--------------------
Only catalog_status == 'active' movies are candidates. Watched and
not_interested movies are excluded. A profile seed contributes
favorite*3 + want*2 + rating_delta*1. Content similarity is
0.7*genre Jaccard + 0.2*year closeness + 0.1*language, then 0.2*popularity is
added. Up to `limit` items are selected by a deterministic greedy repeat-genre
penalty, with movie_id as the tie break. `paid_model_tokens` is always 0.
"""
import math

BASELINE_ID = "{baseline_id}"
BASELINE_VERSION = "v1"
FROZEN_AT = "2026-11-15"
SOURCE_PATH = "{source_path}"
SOURCE_SHA256 = "{source_sha256}"
FUNCTION_NAME = "recommend"
FUNCTION_LINES = ({first_line}, {last_line})
FUNCTION_SHA256 = "{function_sha256}"
LICENSE_STATUS = "NOT_DECLARED_IN_REPO"
LICENSE_NOTE = (
    "仓库根目录及 backend 未发现 LICENSE/COPYING/NOTICE；不推断许可，"
    "使用或再分发前须由项目所有者确认。"
)
DEFAULT_WEIGHTS = {{"favorite": 3.0, "want": 2.0, "rating": 1.0}}
PARAMETERS = {{
    "limit": 12,
    "weights": DEFAULT_WEIGHTS,
    "baseline": False,
    "baseline_field": "demo_popularity",
    "mode": "SYNTHETIC_RECOMMENDATION",
    "cold_start_reason": "合成热门冷启动基线",
    "profile_movies": None,
}}
SCOPE_NOTE = (
    "仅冻结纯 recommend() 规则函数；不包含 boards 的频道候选过滤、"
    "TMDB 贝叶斯融合、周期缓存与频道排序，不能声称覆盖全部旧频道。"
)
NOT_COVERED = (
    "public / trending_day / trending_week 官方快照",
    "top_rated 贝叶斯口碑排序",
    "revenue 累计收入排序",
    "because 同导演/同演员/类型种子逻辑",
    "daily / weekly 的周期扰动、缓存与额外融合",
    "SQL 读取、HTTP、模型调用与用户会话",
)

# ---- begin verbatim frozen source (do not edit) ----
{function_source}
# ---- end verbatim frozen source ----


def recommend_baseline(movies, state, **kwargs):
    """Call the frozen function without altering its algorithm."""
    return recommend(movies, state, **kwargs)


__all__ = [
    "BASELINE_ID",
    "BASELINE_VERSION",
    "FROZEN_AT",
    "SOURCE_PATH",
    "SOURCE_SHA256",
    "FUNCTION_NAME",
    "FUNCTION_LINES",
    "FUNCTION_SHA256",
    "LICENSE_STATUS",
    "LICENSE_NOTE",
    "DEFAULT_WEIGHTS",
    "PARAMETERS",
    "SCOPE_NOTE",
    "NOT_COVERED",
    "recommend",
    "recommend_baseline",
]
'''


def main() -> int:
    source_bytes = SOURCE.read_bytes()
    source_sha = hashlib.sha256(source_bytes).hexdigest()
    if source_sha != EXPECTED_SOURCE_SHA256:
        print(json.dumps({"status": "REFUSED", "reason": "source bytes changed",
                          "expected": EXPECTED_SOURCE_SHA256, "actual": source_sha},
                         ensure_ascii=False))
        return 2
    # pathlib.read_text() normalised newlines when the recorded hashes were
    # taken; decode alone would preserve CRLF and change the segment hash.
    source_text = source_bytes.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    tree = ast.parse(source_text)
    nodes = [node for node in tree.body
             if isinstance(node, ast.FunctionDef) and node.name == "recommend"]
    if len(nodes) != 1:
        raise SystemExit("expected exactly one top-level recommend()")
    node = nodes[0]
    function_source = ast.get_source_segment(source_text, node)
    if function_source is None:
        raise SystemExit("cannot extract function source")
    function_sha = hashlib.sha256(function_source.encode("utf-8")).hexdigest()
    if function_sha != EXPECTED_FUNCTION_SHA256:
        raise SystemExit("extracted function hash changed")
    if (node.lineno, node.end_lineno) != EXPECTED_LINES:
        raise SystemExit(f"unexpected function lines: {node.lineno}-{node.end_lineno}")
    rendered = HEADER.format(
        baseline_id="baseline-v1-catalog-recommend-20261113",
        source_path="backend/catalog/recommendation.py",
        source_sha256=source_sha,
        function_sha256=function_sha,
        first_line=node.lineno,
        last_line=node.end_lineno,
        function_source=function_source,
    )
    TARGET.write_bytes(rendered.encode("utf-8"))
    print(json.dumps({
        "status": "FROZEN",
        "target": str(TARGET),
        "source_sha256": source_sha,
        "function_sha256": function_sha,
        "function_lines": [node.lineno, node.end_lineno],
        "bytes": len(rendered.encode("utf-8")),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())