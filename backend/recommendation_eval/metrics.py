"""Pure metric functions used by the offline evaluator.

All functions return None rather than inventing a zero when a denominator is
undefined. The evaluator records the corresponding reason.
"""
from __future__ import annotations

import math
from typing import Iterable


def unique_ids(values: Iterable[int]) -> list[int]:
    """Keep first occurrence of each integer id."""
    result, seen = [], set()
    for value in values:
        if type(value) is int and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def recall_at_k(ranked_ids: Iterable[int], relevant_ids: Iterable[int],
                k: int = 10) -> float | None:
    """Macro per-scenario recall: hits in top-k / all labelled relevant ids.

    The denominator is the full relevant set for this scenario, not k and not
    the number of returned items.
    """
    relevant = set(relevant_ids)
    if not relevant:
        return None
    ranked = unique_ids(ranked_ids)[:k]
    return len(relevant.intersection(ranked)) / len(relevant)


def ndcg_at_k(ranked_ids: Iterable[int], relevant_ids: Iterable[int],
              k: int = 10) -> float | None:
    """Binary-relevance NDCG@k.

    DCG = sum(rel_i / log2(rank_i + 1)); IDCG uses min(k, |relevant|) ideal
    positions. The denominator is the ideal ranking for the same labelled set,
    not the actual list length.
    """
    relevant = set(relevant_ids)
    if not relevant:
        return None
    ranked = unique_ids(ranked_ids)[:k]
    dcg = sum(1.0 / math.log2(position + 1)
              for position, movie_id in enumerate(ranked, 1)
              if movie_id in relevant)
    ideal_count = min(k, len(relevant))
    idcg = sum(1.0 / math.log2(position + 1)
               for position in range(1, ideal_count + 1))
    return dcg / idcg if idcg else None


def catalog_coverage(recommended_ids: Iterable[int],
                     catalog_ids: Iterable[int]) -> float | None:
    """Unique recommended catalog ids / unique active catalog ids.

    External/unknown ids must be filtered out by the caller. An empty catalog
    has no defined denominator and returns None.
    """
    catalog = set(catalog_ids)
    if not catalog:
        return None
    return len(set(recommended_ids).intersection(catalog)) / len(catalog)


def intra_list_genre_diversity(genre_sets: Iterable[Iterable[str]]) -> float | None:
    """Mean pairwise 1 - Jaccard distance for one ranked list.

    Pairs with two empty genre sets are ignored. If fewer than two usable
    pairs exist, the value is None (a single item is not evidence of
    diversity).
    """
    sets = []
    for value in genre_sets:
        genres = {item for item in value if type(item) is str and item}
        if genres:
            sets.append(genres)
    if len(sets) < 2:
        return None
    total, pairs = 0.0, 0
    for left in range(len(sets)):
        for right in range(left):
            union = sets[left] | sets[right]
            total += 1.0 - len(sets[left] & sets[right]) / len(union)
            pairs += 1
    return total / pairs if pairs else None


def genre_coverage(recommended_genres: Iterable[str],
                   catalog_genres: Iterable[str]) -> float | None:
    """Unique recommended genre labels / unique catalog genre labels."""
    catalog = {item for item in catalog_genres if type(item) is str and item}
    if not catalog:
        return None
    recommended = {item for item in recommended_genres if type(item) is str and item}
    return len(recommended.intersection(catalog)) / len(catalog)


def mean_or_none(values: Iterable[float | None]) -> float | None:
    clean = [value for value in values if value is not None]
    return sum(clean) / len(clean) if clean else None


def rate_or_none(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return numerator / denominator


__all__ = [
    "unique_ids",
    "recall_at_k",
    "ndcg_at_k",
    "catalog_coverage",
    "intra_list_genre_diversity",
    "genre_coverage",
    "mean_or_none",
    "rate_or_none",
]