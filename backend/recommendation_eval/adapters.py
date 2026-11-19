"""Ranker adapters. The evaluator only depends on the small rank() contract."""
from __future__ import annotations

from . import baseline_v1
from .schema import condition_violations


class AdapterError(ValueError):
    pass


class BaselineV1Adapter:
    """Wrap the frozen recommend() with an explicit candidate policy.

    `prefiltered` applies the scenario's hard conditions and negative feedback
    before calling the frozen function, mirroring the boards' candidate
    filtering. `raw` calls the same function on all active movies and lets the
    evaluator measure the function's own hard-condition violations. Neither
    mode changes recommend() itself.
    """

    name = "baseline-v1"

    def __init__(self, *, mode: str = "prefiltered",
                 baseline_field: str = "catalog_popularity",
                 limit: int = 50, weights: dict | None = None):
        if mode not in ("prefiltered", "raw"):
            raise AdapterError("mode must be prefiltered or raw")
        if type(limit) is not int or not 1 <= limit <= 50:
            raise AdapterError("limit must be 1..50")
        self.mode = mode
        self.baseline_field = baseline_field
        self.limit = limit
        self.weights = weights
        self.name = "baseline-v1-%s" % mode

    def rank(self, movies, state, scenario):
        active = [movie for movie in movies
                  if movie.get("catalog_status") == "active"
                  and movie.get("is_adult") is not True]
        if self.mode == "prefiltered":
            eligible = []
            for movie in active:
                mid = str(movie["movie_id"])
                behaviour = state.get(mid, {})
                if behaviour.get("watched") or behaviour.get("not_interested"):
                    continue
                if condition_violations(movie, behaviour, scenario["hard_conditions"]):
                    continue
                eligible.append(movie)
        else:
            eligible = active
        # The legacy production loader used 0 for a missing heat value. Keep
        # that numeric input policy at this adapter boundary, while the frozen
        # source snapshot and M10 evidence retain None (unknown, not cold).
        legacy_input = [dict(movie, **{self.baseline_field: 0})
                        if movie.get(self.baseline_field) is None else movie
                        for movie in eligible]
        ranking = baseline_v1.recommend(
            legacy_input,
            state,
            limit=self.limit,
            weights=self.weights,
            baseline_field=self.baseline_field,
            profile_movies=active,
            mode="BASELINE_V1_OFFLINE",
            cold_start_reason="Baseline V1 冷启动：可用热度与多样性",
        )
        return {
            "items": ranking["items"],
            "candidate_count": ranking["candidate_count"],
            "paid_model_tokens": 0,
            "metadata": {
                "adapter": self.name,
                "baseline_id": baseline_v1.BASELINE_ID,
                "source_sha256": baseline_v1.SOURCE_SHA256,
                "candidate_policy": self.mode,
                "baseline_field": self.baseline_field,
                "eligible_count": len(eligible),
                "active_count": len(active),
                "filters": scenario.get("hard_conditions", {}),
            },
        }


class DemoAdapter(BaselineV1Adapter):
    """Synthetic-only demo wrapper: baseline for catalog, external placeholder.

    The external result is explicitly labelled SYNTHETIC and has
    movie_id=None, so it can never be counted as a real internal movie id.
    """

    name = "demo-baseline-plus-synthetic-external"

    def rank(self, movies, state, scenario):
        result = super().rank(movies, state, scenario)
        if (scenario.get("category") == "out_of_catalog"
                and scenario.get("allow_external")):
            result["items"] = list(result["items"]) + [{
                "movie_id": None,
                "external_id": "SYNTHETIC_EXTERNAL_900X",
                "title": "SYNTHETIC 库外发现占位（非真实 TMDB 影片）",
                "score": None,
                "components": {},
                "reasons": ["SYNTHETIC 演示：库外发现占位，不是真实 ID"],
            }]
            result.setdefault("metadata", {})["external_placeholder"] = True
        return result


class CallableAdapter:
    """Adapt a caller-supplied function with signature (movies, state, scenario)."""

    def __init__(self, function, *, name: str = "callable"):
        if not callable(function):
            raise AdapterError("function must be callable")
        self.function = function
        self.name = name

    def rank(self, movies, state, scenario):
        return self.function(movies, state, scenario)


__all__ = [
    "AdapterError",
    "BaselineV1Adapter",
    "DemoAdapter",
    "CallableAdapter",
]
