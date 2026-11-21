"""Frozen v1 response comparisons and offline ranking performance checks."""

import gc
import hashlib
import json
import random
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from time import perf_counter
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine

from catalog import public_catalog_cache as cache
from catalog import recommendation_pipeline as pipeline
from m6_checks.test_m10_pipeline import movie


def _fingerprint(result):
    serialized = json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _catalog(size):
    rng = random.Random(62010)
    genres = ("剧情", "科幻", "动作", "悬疑", "喜剧", "战争", "奇幻", "动画", "家庭")
    vocabulary = [f"word{i}" for i in range(640)]
    return [
        movie(
            mid, genres=rng.sample(genres, rng.choice((1, 1, 2, 3))),
            release_year=rng.randrange(1970, 2027),
            original_language=rng.choice(("en", "en", "zh", "ja", "fr", "ko")),
            director_ids=rng.sample(range(1100), 2),
            actor_ids=rng.sample(range(2200), 7),
            overview=" ".join(rng.sample(vocabulary, 32) + ["and", "the", "the"]),
            catalog_popularity=rng.random(), tmdb_rating=rng.randrange(101) / 10,
            vote_count=rng.randrange(5000),
        )
        for mid in range(1, size + 1)
    ]


def _mixed_catalog():
    rows = _catalog(500)
    state = {}
    for mid, row in enumerate(rows, 1):
        if mid <= 45:
            state[str(mid)] = {"favorite": True, "rating": 5.0}
        elif mid <= 60:
            state[mid] = {"want": True, "rating": 1.5, "watched": True}
        elif mid <= 75:
            state[mid] = {"not_interested": True, "favorite": True, "rating": 5.0}
        if mid % 10 == 0:
            row.update(genres=[" 剧情 ", "剧情", {}, False], release_year=[2020],
                       original_language=123, overview={"invalid": True})
        if mid % 13 == 0:
            row.update(director_ids=[False, -1, [], 7], actor_ids="unknown",
                       catalog_popularity=float("nan"), tmdb_rating=float("inf"),
                       vote_count=10**400)
        if mid % 17 == 0:
            row.update(genres=("剧情",), runtime_minutes="invalid", title=False)
        if mid % 29 == 0:
            row["catalog_status"] = "hidden"
        if mid % 31 == 0:
            row["is_adult"] = True
    rows[88].update(genres=["历史"], release_year=1880)
    rows[89].update(genres=["冒险"], release_year=2100)
    state[91] = {"not_interested": "truthy"}
    profile = rows.copy()
    profile[0] = dict(rows[0], title="不同画像片名", genres=["历史"],
                      director_ids=[7], actor_ids=[0], release_year=1880,
                      original_language="en", overview="双重画像")
    profile[1] = dict(rows[1], title="未来种子", genres=["冒险"], release_year=2100)
    return rows, state, profile


def _ranking_scenarios():
    rows, state, profile = _mixed_catalog()
    ranked = [
        ("mixed", rows, state, {"profile_movies": profile, "allow_watched": True, "limit": 50}),
        ("hard-filters", rows, state, {"profile_movies": profile, "allow_watched": True, "limit": 15,
                                     "filters": {"year_from": 1990, "runtime_max": 120,
                                                 "_genre_groups": [["剧情", "动作"]], "unwatched": True}}),
        ("cold-true", rows, {}, {"limit": 50}),
        ("cold-negative", rows, {mid: {"rating": 1.0} for mid in range(1, 25)}, {"limit": 50}),
        ("explicit-empty-profile", rows, state, {"profile_movies": [], "limit": 9}),
    ]
    close = [
        movie(1, genres=["剧情", "科幻"], catalog_popularity=0.4),
        movie(2, genres=["科幻", "剧情"], catalog_popularity=0.400000001),
        movie(3, genres=["动作"], catalog_popularity=0.400000002),
        *[movie(mid, genres=["剧情", "科幻"], catalog_popularity=0.4) for mid in range(4, 53)],
    ]
    ranked.append(("rounded-ties", close, {}, {"limit": 50}))
    return ranked


# Generated with the original eager rank_movies at f4bc27c on these fixtures.
_V1_FINGERPRINTS = {
    "mixed": "57c5987484621fe1e321137cdb212893312c9915898dcbccef697c9c8d3a5109",
    "hard-filters": "b1c9c2e56f8e5a129305bfe013139d2b8d8c29bc618547cf577fa651200fb3da",
    "cold-true": "de6ee45fbbd71d046697277a4d1b25176c76d4a3136bb769a654068d720809e3",
    "cold-negative": "ed81f255f0eea683ab9030ff821455ec6dd2843e1150bf23b0954435bf2c48cb",
    "explicit-empty-profile": "61732bbe6fdabffe27b3c126f5194cf35fab746bb2b7bd7129fa9240ad0baa54",
    "rounded-ties": "84a65ca9f69db563143880104965e3d1aab567f763370db6c02dddba1061f547",
}
_V1_LARGE_FINGERPRINT = "a8cf94941b01b979cc008b5ad33b74ba701baef094a9ecc71f656881fdfdbf03"


@pytest.mark.parametrize("scenario", range(6))
def test_full_rank_matches_frozen_v1_response(scenario):
    name, rows, state, options = _ranking_scenarios()[scenario]
    result = pipeline.rank_movies(rows, state, **options)
    assert _fingerprint(result) == _V1_FINGERPRINTS[name]
    if name == "rounded-ties":
        assert [row["movie_id"] for row in result["items"][:3]] == [1, 3, 2]
        assert result["items"][-1]["diversity_penalty"] == pipeline.MAX_DIVERSITY_PENALTY


def _large_catalog():
    rows = _catalog(5600)
    state = {mid: ({"favorite": True} if mid <= 32 else {"rating": 1.0})
             for mid in range(1, 51)}
    return rows, state


def test_large_rank_outpaces_eager_matching_and_explains_only_selected_movies():
    rows, state = _large_catalog()
    pipeline._cached_features.cache_clear()
    with patch.object(pipeline, "_build_explanation", wraps=pipeline._build_explanation) as explain:
        reference = pipeline.rank_movies(rows, state, limit=50)
    assert explain.call_count == len(reference["items"]) == 50
    assert reference["candidate_count"] == 5600
    assert reference["profile_seed_count"] + reference["negative_seed_count"] == 50
    assert _fingerprint(reference) == _V1_LARGE_FINGERPRINT

    # Keep garbage from unrelated SQLite/Flask fixtures out of the timings.
    gc.collect()
    samples = []
    for _ in range(3):
        started = perf_counter()
        result = pipeline.rank_movies(rows, state, limit=50)
        samples.append(perf_counter() - started)
        assert result == reference

    # Even the matching stage alone in the eager pipeline generated match
    # details for every pair. This omits its scoring, final evidence assembly
    # and ranking, giving a portable lower bound for the same workload.
    features = [pipeline._with_overview_set(pipeline._features(row)) for row in rows]
    seeds = features[:50]
    started = perf_counter()
    for feature in features:
        for seed in seeds:
            pipeline._similarity(feature, seed)
    eager_matching = perf_counter() - started
    assert min(samples) * 2 < eager_matching, (samples, eager_matching)


@pytest.mark.parametrize("shared_overview", [False, True], ids=["sparse", "packed-16-bit"])
def test_indexed_scores_match_pairwise_with_sparse_and_wide_token_counts(
        monkeypatch, shared_overview):
    long_overview = " ".join(f"token{i}" for i in range(280))
    rows = [movie(
        mid, genres=[] if mid % 2 else ["科幻"], release_year=1880 + mid,
        original_language="en" if mid % 3 else "fr",
        overview=long_overview if shared_overview else f"word{mid % 80}",
        director_ids=[mid % 6], actor_ids=[mid % 5, mid % 7],
    ) for mid in range(1, 129)]
    state = {mid: {"favorite": True, "rating": 1.0} for mid in range(1, 21)}

    indexed = pipeline.rank_movies(rows, state, limit=12)
    monkeypatch.setattr(pipeline, "_INDEXED_PAIR_THRESHOLD", float("inf"))
    pairwise = pipeline.rank_movies(rows, state, limit=12)
    assert indexed == pairwise
    assert _fingerprint(indexed) == _fingerprint(pairwise)


def test_feature_cache_is_immutable_content_keyed_and_thread_safe():
    pipeline._cached_features.cache_clear()
    rows = [movie(1, overview="seed token", director_ids=[1]),
            movie(2, overview="seed token", director_ids=[1])]
    state = {1: {"favorite": True}}
    with patch.object(pipeline, "_overview_tokens", wraps=pipeline._overview_tokens) as tokenize:
        initial = pipeline.rank_movies(rows, state)
        assert tokenize.call_count == 2
        with ThreadPoolExecutor(max_workers=4) as executor:
            repeats = list(executor.map(lambda _: pipeline.rank_movies(deepcopy(rows), state), range(8)))
        assert all(result == initial for result in repeats)
        assert tokenize.call_count == 2

        changed = deepcopy(rows)
        changed[1]["director_ids"].append(3)
        changed[1]["overview"] = "different words"
        assert pipeline.rank_movies(changed, state) != initial
        assert pipeline.rank_movies(rows, state) == initial
        assert tokenize.call_count == 3
    assert pipeline._features(rows[1]) is pipeline._features(deepcopy(rows[1]))
    assert pipeline._features(rows[1]).director_ids == frozenset({1})


def test_fast_tokenization_preserves_the_v1_regex_and_token_limit():
    every_ascii_code = " ".join(f"AB{chr(code)}CD" for code in range(128))
    for text in (every_ascii_code, every_ascii_code + "中İK国", "Word " * 300 + "late"):
        expected = pipeline._TOKEN_RE.findall(text[:6000].lower())[:300]
        assert pipeline._overview_tokens(text) == tuple(dict.fromkeys(expected))


def test_public_catalog_has_one_snapshot_with_detached_mutable_clones(monkeypatch):
    engine = create_engine("sqlite://")
    monkeypatch.setattr(cache, "_watermarks", lambda _: {1: "version"})
    rows = [{"movie_id": 1, "genres": ["剧情"], "facts": [{"regions": ["CN"]}],
             "evidence_ids": {2, 3}}]
    try:
        readonly = cache.load(engine, lambda _: rows, copy_mode="readonly")["movies"]
        assert cache._MEMORY[engine]["readonly"] is readonly
        assert "movies" not in cache._MEMORY[engine]
        assert cache.peek(engine)["movies"] == len(readonly)
        rows[0]["facts"][0]["regions"].append("JP")
        clone = cache.load(engine, lambda _: rows, copy_mode="clone")["movies"]
        assert clone[0]["facts"] == [{"regions": ["CN"]}]
        assert clone[0]["evidence_ids"] == {2, 3}
        clone[0]["genres"].clear()
        clone[0]["facts"][0]["regions"].clear()
        clone[0]["evidence_ids"].clear()
        assert readonly[0]["genres"] == ("剧情",)
        assert readonly[0]["facts"][0]["regions"] == ("CN",)
        assert readonly[0]["evidence_ids"] == frozenset({2, 3})
    finally:
        engine.dispose()
