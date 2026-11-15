"""M10 integration checks over the real reader, agent tools and agent service.

The catalog is a throw-away SQLite ``:memory:`` database built from the real
``CatalogBase`` metadata.  Every row is synthetic: movie_id 9100xx, title starts
with "合成", TMDB ids start with 99000xx.  No real account, database, credential,
model call, browser or network endpoint is touched.

``CatalogReader``, ``rank_movies`` (through the reader), ``AgentTools``,
``verify_final`` and ``AgentService.run`` are the production objects.  Only the
behaviour snapshot, the BYOK vault, the billing ledger and the model provider
are injected at their documented boundaries; the ranking and verification
logic is never re-implemented here.
"""
from __future__ import annotations

import copy
import json
import math
import threading
import unittest
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine, event, select
from sqlalchemy.pool import StaticPool

from assistant.agent_provider import ProviderError, ScriptedAgentProvider
from assistant.agent_service import AgentService
from assistant.agent_tools import AgentTools, verify_final
from assistant.reader import CatalogReader
from assistant.store import tables
from catalog.models import CatalogBase
from catalog.recommendation_data import invalidate as invalidate_catalog
from catalog.recommendation_pipeline import RECOMMENDATION_VERSION
from m6_checks.m7_intent_fixtures import set_, update

NOW = datetime(2026, 1, 1, 12, 0, 0)
BYOK_CONFIG = {
    "generation": 1,
    "model": "mock-agent",
    "enabled": True,
    "verification": "VERIFIED",
    "daily_calls": None,
    "max_output_tokens": 2048,
    "cache_enabled": False,
}

# genre_id -> (Chinese label, English label, TMDB id)
GENRE_NAMES = {
    1: ("剧情", "Drama", "18"),
    2: ("科幻", "Science Fiction", "878"),
    3: ("动作", "Action", "28"),
    4: ("悬疑", "Mystery", "9648"),
    5: ("喜剧", "Comedy", "35"),
}

# movie_id, title, year, runtime, language, genre ids, director, actor,
# TMDB id (None = no source record), popularity/rating/votes for the snapshot,
# catalog_status, is_adult.
MOVIE_SPECS = [
    (910001, "合成剧情种子A", 1985, 112, "en", [1], 501, 601, 9900001, 10.0, 7.0, 100, "active", False),
    (910002, "合成剧情种子B", 2020, 118, "ja", [1], 502, 602, 9900002, 10.0, 7.0, 100, "active", False),
    (910003, "合成科幻动作片", 1986, 108, "en", [2, 3], 501, 601, 9900003, 10.0, 7.0, 100, "active", False),
    (910004, "合成科幻剧情片", 2020, 121, "ja", [2, 1], 502, 602, 9900004, 10.0, 7.0, 100, "active", False),
    (910005, "合成科幻悬疑片", 2005, 130, "en", [2, 4], 503, 603, 9900005, 10.0, 7.0, 100, "active", False),
    (910006, "合成科幻喜剧片", 1999, 95, "en", [2, 5], 504, 604, 9900006, 10.0, 7.0, 100, "active", False),
    (910007, "合成喜剧片", 2010, 100, "zh", [5], 505, 605, 9900007, 10.0, 7.0, 100, "active", False),
    (910008, "合成科幻无资料片", 2015, 105, "zh", [2], 506, 606, None, None, None, None, "active", False),
    (910009, "合成科幻年代片", 2004, 100, "zh", [2], 507, 607, 9900009, 10.0, 7.0, 100, "active", False),
    (910010, "合成成人科幻片", 2018, 99, "en", [2], 508, 608, 9900010, 10.0, 7.0, 100, "active", True),
    (910011, "合成待隐藏科幻片", 2017, 101, "en", [2], 509, 609, 9900011, 10.0, 7.0, 100, "active", False),
    (910012, "合成已下架科幻片", 2016, 96, "en", [2], 510, 610, None, None, None, None, "hidden", False),
]


def _collate(left, right):
    return (left > right) - (left < right)


def _sqlite_engine(*metadatas):
    """Create a shared in-memory SQLite engine for the given metadata objects.

    ``utf8mb4_bin``/``ascii_bin`` collations and ``CHAR_LENGTH`` exist only to
    let the frozen MySQL catalog metadata create its tables on SQLite; they do
    not change any tested query or ranking logic.
    """
    engine = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _configure(dbapi_connection, _record):
        dbapi_connection.create_collation("utf8mb4_bin", _collate)
        dbapi_connection.create_collation("ascii_bin", _collate)
        dbapi_connection.create_function(
            "CHAR_LENGTH", 1, lambda value: None if value is None else len(value)
        )

    for metadata in metadatas:
        metadata.create_all(engine)
    return engine


def _build_catalog_engine():
    engine = _sqlite_engine(CatalogBase.metadata)
    md = CatalogBase.metadata.tables
    movie_rows, genre_rows, link_rows = [], [], []
    external_rows, source_rows, credit_rows, asset_rows = [], [], [], []
    for genre_id, (name_zh, name_en, tmdb_genre_id) in GENRE_NAMES.items():
        genre_rows.append(
            {
                "genre_id": genre_id,
                "name_zh": name_zh,
                "name_en": name_en,
                "tmdb_genre_id": tmdb_genre_id,
            }
        )
    next_external = next_source = next_credit = next_asset = 1
    for spec in MOVIE_SPECS:
        (
            movie_id,
            title,
            year,
            runtime,
            language,
            genre_ids,
            director_id,
            actor_id,
            tmdb_id,
            popularity,
            rating,
            votes,
            status,
            is_adult,
        ) = spec
        movie_rows.append(
            {
                "movie_id": movie_id,
                "title": title,
                "original_title": title + " EN",
                "release_date": date(year, 1, 1),
                "release_year": year,
                "runtime_minutes": runtime,
                "original_language": language,
                "overview": None,  # keep lexical evidence out of the fixture
                "catalog_status": status,
                "is_adult": is_adult,
                "created_at": NOW,
                "updated_at": NOW,
            }
        )
        for genre_id in genre_ids:
            link_rows.append({"movie_id": movie_id, "genre_id": genre_id})
        if tmdb_id is not None:
            external_rows.append(
                {
                    "id": next_external,
                    "movie_id": movie_id,
                    "source": "tmdb",
                    "external_id": str(tmdb_id),
                    "verified_method": "SYNTHETIC_FIXTURE",
                    "verified_at": NOW,
                    "created_at": NOW,
                }
            )
            next_external += 1
            source_rows.append(
                {
                    "id": next_source,
                    "movie_id": movie_id,
                    "source": "tmdb",
                    "source_record_id": "tmdb:%d" % tmdb_id,
                    "fetched_at": NOW,
                    "source_updated_at": None,
                    "payload_hash": "0" * 64,
                    "metadata_json": {
                        "id": tmdb_id,
                        "popularity": popularity,
                        "vote_average": rating,
                        "vote_count": votes,
                    },
                    "ingest_batch_id": 1,
                    "license_status": "allowed",
                }
            )
            next_source += 1
        credit_rows.append(
            {
                "credit_id": next_credit,
                "movie_id": movie_id,
                "person_id": director_id,
                "source": "tmdb",
                "source_credit_key": "director-%d" % movie_id,
                "role_type": "crew",
                "job": "Director",
                "character_name": None,
                "sort_order": 0,
            }
        )
        next_credit += 1
        credit_rows.append(
            {
                "credit_id": next_credit,
                "movie_id": movie_id,
                "person_id": actor_id,
                "source": "tmdb",
                "source_credit_key": "actor-%d" % movie_id,
                "role_type": "cast",
                "job": None,
                "character_name": "Synthetic Role",
                "sort_order": 0,
            }
        )
        next_credit += 1
        if movie_id in (910003, 910004, 910005):
            asset_rows.append(
                {
                    "asset_id": next_asset,
                    "movie_id": movie_id,
                    "asset_type": "poster",
                    "source": "tmdb",
                    "source_path": "/synthetic-%d.jpg" % movie_id,
                    "language": "en",
                    "width": 342,
                    "height": 513,
                    "is_primary": True,
                    "license_status": "allowed",
                    "updated_at": NOW,
                }
            )
            next_asset += 1
    with engine.begin() as connection:
        connection.execute(md["movies"].insert(), movie_rows)
        connection.execute(md["genres"].insert(), genre_rows)
        connection.execute(md["movie_genres"].insert(), link_rows)
        connection.execute(md["movie_external_ids"].insert(), external_rows)
        connection.execute(md["movie_source_records"].insert(), source_rows)
        connection.execute(md["movie_credits"].insert(), credit_rows)
        connection.execute(md["movie_assets"].insert(), asset_rows)
    return engine


class _SnapshotBehavior:
    """Injected snapshot boundary: real behaviour storage is out of scope."""

    def __init__(self, states=None):
        self._states = {}
        for user, state in (states or {}).items():
            self._states[int(user)] = {
                str(movie_id): dict(value) for movie_id, value in state.items()
            }

    def snapshot(self, user):
        return copy.deepcopy(self._states.get(int(user), {}))

    def set_state(self, user, movie_id, state):
        self._states.setdefault(int(user), {})[str(movie_id)] = dict(state)


class _CatalogCase(unittest.TestCase):
    def setUp(self):
        self.engine = _build_catalog_engine()
        self.behavior = _SnapshotBehavior()
        self.reader = CatalogReader(
            self.engine, self.behavior, tmdb=None, cache_dir=None
        )

    @staticmethod
    def ids(rows):
        return [row["movie_id"] for row in rows]

    @staticmethod
    def item(rows, movie_id):
        return next(row for row in rows if row["movie_id"] == movie_id)

    def assert_grounded_item(self, item, snapshot):
        self.assertEqual(item["recommendation_version"], RECOMMENDATION_VERSION)
        self.assertTrue(math.isfinite(item["score"]))
        for key in ("baseline", "rating_confidence", "preference", "negative", "diversity"):
            self.assertTrue(math.isfinite(item["components"][key]))
        self.assertTrue(item["reason_codes"])
        row = snapshot[item["movie_id"]]
        if not item["evidence"]:
            # D1 made "no TMDB popularity" truly absent. A candidate with no
            # positive preference match and no reliable prior legitimately has
            # no evidence; it must say so and must not carry invented zeros.
            self.assertTrue(
                {"NO_MATCHING_FEATURES", "COLD_START_NO_EVIDENCE"}
                & set(item["reason_codes"])
            )
            self.assertEqual(item["score"], 0.0)
            for key in ("baseline", "rating_confidence", "preference", "negative"):
                self.assertEqual(item["components"][key], 0.0)
            return
        for evidence in item["evidence"]:
            self.assertIn(evidence["source"], ("CATALOG", "USER_BEHAVIOR"))
            self.assertTrue(math.isfinite(evidence["contribution"]))
            if evidence["source"] == "CATALOG":
                self.assertEqual(evidence["value"], row[evidence["field"]])
                if evidence["field"] == "tmdb_rating":
                    self.assertEqual(evidence["vote_count"], row["vote_count"])
            else:
                self.assertIn(evidence["seed_movie_id"], snapshot)
                seed = snapshot[evidence["seed_movie_id"]]
                match = evidence["match"]
                if match == "GENRE_MATCH":
                    self.assertTrue(set(evidence["value"]) <= set(item["genres"]))
                    self.assertTrue(set(evidence["value"]) <= set(seed["genres"]))
                elif match == "YEAR_NEAR":
                    self.assertEqual(
                        evidence["value"], [seed["release_year"], row["release_year"]]
                    )
                elif match == "LANGUAGE_MATCH":
                    self.assertEqual(evidence["value"], row["original_language"])
                    self.assertEqual(evidence["value"], seed["original_language"])
                elif match == "DIRECTOR_MATCH":
                    self.assertTrue(
                        set(evidence["value"])
                        <= set(row["director_ids"]) & set(seed["director_ids"])
                    )
                elif match == "ACTOR_OVERLAP":
                    self.assertTrue(
                        set(evidence["value"])
                        <= set(row["actor_ids"]) & set(seed["actor_ids"])
                    )
                elif match == "REWATCH_ALLOWED":
                    self.assertEqual(evidence["seed_movie_id"], item["movie_id"])


class CatalogSqlBoundaryTests(_CatalogCase):
    """The real SQL builders and CatalogReader.get_movies/search."""

    def test_get_movies_reads_active_rows_and_assembles_visible_facts(self):
        rows = {m["movie_id"]: m for m in self.reader.get_movies([910003, 910008, 910010, 910012])}
        self.assertEqual(set(rows), {910003, 910008, 910010})  # 910012 is hidden
        self.assertEqual(set(rows[910003]["genres"]), {"科幻", "动作"})
        self.assertEqual(rows[910003]["release_year"], 1986)
        self.assertTrue(rows[910003]["poster"].startswith("/api/media/poster?u="))
        self.assertIsNone(rows[910008]["poster"])
        self.assertEqual(self.reader.get_movies([]), [])
        for bad in ([910003, "x"], [True], list(range(1, 12))):
            with self.subTest(ids=bad), self.assertRaises(ValueError):
                self.reader.get_movies(bad)

    def test_search_uses_the_same_conditions_semantics(self):
        def search(user, filters, **kwargs):
            rows, truncated = self.reader.search(user, filters, **kwargs)
            return {m["movie_id"] for m in rows}, truncated

        all_scifi, _ = search(101, {"genre": "科幻"})
        self.assertEqual(
            all_scifi,
            {910003, 910004, 910005, 910006, 910008, 910009, 910010, 910011},
        )
        self.assertEqual(
            search(101, {"genres_any": ["喜剧", "动作"]})[0],
            {910003, 910006, 910007},
        )
        self.assertEqual(
            search(101, {"_genre_groups": [["科幻"], ["喜剧", "动作"]]})[0],
            {910003, 910006},
        )
        self.assertEqual(
            search(101, {"year_ranges_any": [[1980, 1990], [2015, 2020]]})[0],
            {910001, 910002, 910003, 910004, 910008, 910010, 910011},
        )
        self.assertEqual(
            search(101, {"genre": "科幻", "excluded_genres": ["动作"]})[0],
            {910004, 910005, 910006, 910008, 910009, 910010, 910011},
        )
        self.assertEqual(search(101, {}, keyword="动作")[0], {910003})
        self.assertEqual(search(101, {}, keyword="%")[0], set())  # autoescaped
        self.assertEqual(search(101, {}, keyword="合成科幻动作片")[0], {910003})

    def test_search_applies_watched_and_not_interested_policy(self):
        self.behavior.set_state(104, 910003, {"watched": True})
        self.behavior.set_state(105, 910004, {"not_interested": True})
        self.assertIn(
            910003,
            {m["movie_id"] for m in self.reader.search(104, {"genre": "科幻"})[0]},
        )
        ids, _ = self._search_ids(104, {"genre": "科幻", "unwatched": True})
        self.assertNotIn(910003, ids)
        ids, _ = self._search_ids(104, {"genre": "科幻", "unwatched": False})
        self.assertIn(910003, ids)  # explicit rewatch keeps watched rows visible
        ids, _ = self._search_ids(105, {"genre": "科幻", "unwatched": False})
        self.assertNotIn(910004, ids)  # not_interested never restored
        rows, truncated = self.reader.search(101, {"genre": "科幻"}, limit=2)
        self.assertEqual(len(rows), 2)
        self.assertTrue(truncated)

    def _search_ids(self, user, filters):
        rows, _ = self.reader.search(user, filters)
        return {m["movie_id"] for m in rows}, False

    def test_snapshot_does_not_invent_missing_tmdb_facts(self):
        movies, state, truncated = self.reader.snapshot(101)
        by_id = {m["movie_id"]: m for m in movies}
        self.assertFalse(state)
        self.assertFalse(truncated)
        self.assertNotIn(910012, by_id)  # hidden rows are not candidates
        known = by_id[910003]
        self.assertEqual(known["tmdb_rating"], 7.0)
        self.assertEqual(known["vote_count"], 100)
        self.assertEqual(known["catalog_popularity"], 1.0)
        self.assertEqual(known["director_ids"], [501])
        self.assertEqual(known["actor_ids"], [601])
        unknown = by_id[910008]
        self.assertIsNone(unknown["tmdb_rating"])
        self.assertIsNone(unknown["vote_count"])
        self.assertEqual(unknown["director_ids"], [506])
        self.assertEqual(unknown["actor_ids"], [606])


class ReaderRankingIntegrationTests(_CatalogCase):
    """CatalogReader.resolve / recommendation_evidence on real ranking code."""

    def test_two_users_same_request_get_different_explained_orders(self):
        self.behavior.set_state(101, 910001, {"favorite": True})
        self.behavior.set_state(102, 910002, {"favorite": True})
        rows_a = self.reader.resolve(101, "recommend", {"genre": "科幻"})[0]
        rows_b = self.reader.resolve(102, "recommend", {"genre": "科幻"})[0]
        self.assertEqual(rows_a[0]["movie_id"], 910003)
        self.assertEqual(rows_b[0]["movie_id"], 910004)
        self.assertNotEqual(self.ids(rows_a), self.ids(rows_b))
        self.assertNotIn(910001, self.ids(rows_a))  # seed is outside the filter
        top_a = rows_a[0]
        self.assertEqual(top_a["recommendation_version"], RECOMMENDATION_VERSION)
        self.assertIn("DIRECTOR_MATCH", top_a["reason_codes"])
        self.assertIn("YEAR_NEAR", top_a["reason_codes"])
        evidence = [e for e in top_a["evidence"] if e["source"] == "USER_BEHAVIOR"]
        self.assertIn(910001, {e["seed_movie_id"] for e in evidence})
        self.assertEqual(
            round(
                sum(
                    top_a["components"][key]
                    for key in ("baseline", "rating_confidence", "preference", "negative")
                ),
                6,
            ),
            round(top_a["score"], 6),
        )
        top_b = rows_b[0]
        self.assertIn(
            910002,
            {e["seed_movie_id"] for e in top_b["evidence"] if e["source"] == "USER_BEHAVIOR"},
        )

    def test_state_change_affects_the_next_ranking_and_negative_is_kept(self):
        self.behavior.set_state(101, 910001, {"favorite": True})
        before = self.reader.resolve(101, "recommend", {"genre": "科幻"})[0]
        self.behavior.set_state(101, 910003, {"not_interested": True})
        after = self.reader.resolve(101, "recommend", {"genre": "科幻"})[0]
        self.assertNotIn(910003, self.ids(after))
        self.assertNotEqual(self.ids(before), self.ids(after))
        self.assertEqual(after[0]["movie_id"], 910004)
        penalized = self.item(after, 910004)
        self.assertIn("NEGATIVE_SIMILARITY", penalized["reason_codes"])
        negative = [e for e in penalized["evidence"] if e["match"] == "NEGATIVE_SIMILARITY"]
        self.assertTrue(negative)
        self.assertEqual(negative[0]["seed_movie_id"], 910003)
        self.assertLess(negative[0]["contribution"], 0)
        # Explicit rewatch permission cannot restore not_interested.
        again = self.reader.resolve(101, "recommend", {"genre": "科幻", "unwatched": False})[0]
        self.assertNotIn(910003, self.ids(again))

    def test_watched_policy_and_explicit_rewatch(self):
        self.behavior.set_state(104, 910003, {"watched": True})
        default = self.reader.resolve(104, "recommend", {"genre": "科幻"})[0]
        self.assertNotIn(910003, self.ids(default))
        rewatch = self.reader.resolve(104, "recommend", {"genre": "科幻", "unwatched": False})[0]
        self.assertIn(910003, self.ids(rewatch))
        item = self.item(rewatch, 910003)
        self.assertIn("REWATCH_ALLOWED", item["reason_codes"])
        self.assertTrue(any("显式允许重温" in reason for reason in item["local_reasons"]))
        explicit = self.reader.resolve(104, "recommend", {"genre": "科幻", "unwatched": True})[0]
        self.assertNotIn(910003, self.ids(explicit))
        # Internal contract: allow_watched=True never overrides explicit unwatched=True.
        movies, state, _ = self.reader.snapshot(104)
        from catalog.recommendation_pipeline import rank_movies

        internal = rank_movies(
            movies,
            state,
            filters={"unwatched": True},
            allow_watched=True,
            limit=50,
        )
        self.assertNotIn(910003, [i["movie_id"] for i in internal["items"]])
        self.behavior.set_state(104, 910004, {"watched": True, "not_interested": True})
        mixed = self.reader.resolve(104, "recommend", {"genre": "科幻", "unwatched": False})[0]
        self.assertNotIn(910004, self.ids(mixed))

    def test_hard_conditions_and_internal_group_and_seed_outside_filters(self):
        simple = self.reader.resolve(
            103,
            "recommend",
            {"genre": "科幻", "year_from": 2000, "year_to": 2010, "runtime_max": 120},
        )[0]
        self.assertEqual(self.ids(simple), [910009])
        grouped = self.reader.resolve(
            103,
            "recommend",
            {
                "_genre_groups": [["科幻"], ["喜剧", "动作"]],
                "_year_groups": [[[1980, 1990], [1995, 2005]]],
                "excluded_genres": ["动作"],
            },
        )[0]
        self.assertEqual(self.ids(grouped), [910006])
        evidence = self.reader.recommendation_evidence(101, [910008], {"genre": "科幻"})
        self.assertEqual(set(evidence), {910008})
        self.assertEqual(evidence[910008]["recommendation_version"], RECOMMENDATION_VERSION)
        # Watched ids are not grounded evidence for a normal request.
        self.behavior.set_state(104, 910003, {"watched": True})
        evidence = self.reader.recommendation_evidence(104, [910003, 910008], {"genre": "科幻"})
        self.assertEqual(set(evidence), {910008})


class _HookedScriptedProvider(ScriptedAgentProvider):
    """Scripted provider with a hook before a chosen completion call."""

    def __init__(self, steps, before_call=None):
        super().__init__(steps)
        self.before_call = before_call or {}

    def complete(self, *args, **kwargs):
        hook = self.before_call.get(self.calls + 1)
        if hook:
            hook()
        return super().complete(*args, **kwargs)


class _RecordingLedger:
    """Billing boundary mock; durable MySQL accounting is out of scope."""

    def __init__(self):
        self.dispatches = []
        self.records = []
        self.finished = []

    def dispatch(self, key, user, model, daily, paid):
        self.dispatches.append(
            {"key": key, "user": user, "model": model, "daily": daily, "paid": paid}
        )
        return "synthetic-dispatch-%d" % len(self.dispatches)

    def record(self, key, call_id, usage, paid, status=None):
        self.records.append(
            {"call_id": call_id, "usage": usage, "paid": paid, "status": status}
        )

    def read(self, key):
        return {"cancel": False, "state": "DISPATCHED"}

    def cost(self, key):
        calls = len(self.dispatches)
        return {
            "provider_calls": calls,
            "input_tokens": 100 * calls,
            "output_tokens": 40 * calls,
        }

    def finish(self, key, result, state):
        self.finished.append({"result": result, "state": state})


class _VaultStub:
    """BYOK boundary mock; no key, ciphertext or account is real."""

    def __init__(self, config):
        self._config = copy.deepcopy(config)
        self.verified_calls = []

    def config(self, user, binding):
        return copy.deepcopy(self._config)

    def key(self, user, binding, generation):
        if not self._config:
            raise ValueError("BYOK_NOT_CONFIGURED")
        return "SYNTHETIC_ONLY_KEY"

    def verified(self, *args):
        self.verified_calls.append(args)


def _plan(payload, actions):
    return {
        "content": "",
        "tool_calls": [
            {
                "id": "plan-synthetic",
                "type": "function",
                "function": {
                    "name": "plan_movie_turn",
                    "arguments": json.dumps(
                        {"intent_update": payload, "actions": actions},
                        ensure_ascii=False,
                    ),
                },
            }
        ],
    }


def _final(movie_ids, answer="合成模型解释，依据工具返回的库内候选。"):
    return {
        "content": json.dumps(
            {"answer": answer, "movie_ids": movie_ids, "discoveries": []},
            ensure_ascii=False,
        )
    }


class _AgentCase(_CatalogCase):
    def run_agent(
        self,
        config,
        steps,
        *,
        hooks=None,
        user=102,
        message="推荐一些科幻电影",
        states=(),
    ):
        for user_id, movie_id, state in states:
            self.behavior.set_state(user_id, movie_id, state)
        md = tables()
        store_engine = _sqlite_engine(md)
        key = "synthetic-request-1"
        with store_engine.begin() as connection:
            connection.execute(
                md.tables["requests"].insert().values(
                    id=key,
                    user_id=user,
                    fingerprint="synthetic-fingerprint",
                    response={"state": "DISPATCHED", "cancel": False, "result": None},
                    expires_at=datetime.utcnow() + timedelta(days=1),
                )
            )
        provider = _HookedScriptedProvider(steps, hooks)
        ledger = _RecordingLedger()
        vault = _VaultStub(config)
        service = AgentService(
            store_engine, md, self.reader, vault, ledger, lambda *args: provider
        )
        runtime = {"draft": "", "cancel": threading.Event()}
        service.run(
            key,
            user,
            "synthetic-binding",
            message,
            None,
            "query",
            None,
            runtime,
        )
        with store_engine.connect() as connection:
            response = connection.execute(
                select(md.tables["requests"].c.response).where(
                    md.tables["requests"].c.id == key
                )
            ).scalar_one()
        self.assertEqual(response["state"], "DONE")
        return SimpleNamespace(
            result=response["result"],
            response=response,
            provider=provider,
            ledger=ledger,
            vault=vault,
            service=service,
            store_engine=store_engine,
            md=md,
            key=key,
        )


class AgentLocalPathTests(_AgentCase):
    def test_local_no_key_path_uses_real_reader_and_grounded_evidence(self):
        run = self.run_agent(
            None,
            [],
            user=101,
            message="推荐一些科幻电影",
            states=[(101, 910001, {"favorite": True})],
        )
        result = run.result
        self.assertEqual(result["source"], "LOCAL")
        self.assertTrue(result["items"])
        self.assertIn("未连接用户模型，使用本地兜底", result["limitations"])
        self.assertEqual(result["conditions"].get("genre"), "科幻")
        self.assertEqual(result["conditions"].get("excluded_genres"), [])
        snapshot = {m["movie_id"]: m for m in self.reader.snapshot(101)[0]}
        self.assertTrue(
            {item["movie_id"] for item in result["items"]} <= set(snapshot)
        )
        for item in result["items"]:
            self.assert_grounded_item(item, snapshot)
        unknown = self.item(result["items"], 910008)
        self.assertFalse(
            any(e.get("field") == "tmdb_rating" for e in unknown["evidence"])
        )
        self.assertTrue(result["conversation_id"])
        state, turns = run.service.conversation(
            101, "synthetic-binding", result["conversation_id"]
        )
        self.assertEqual(turns, 1)
        self.assertEqual(state["filters"].get("genre"), "科幻")
        self.assertEqual(len(state["history"]), 2)
        self.assertEqual(state["shown"], [item["movie_id"] for item in result["items"]])


class AgentByokFlowTests(_AgentCase):
    def test_scripted_provider_runs_real_plan_tools_and_final_verification(self):
        self.behavior.set_state(102, 910002, {"favorite": True})
        expected = self.reader.resolve(102, "recommend", {"genre": "科幻"})[0][0][
            "movie_id"
        ]
        payload = update([set_("genre", "科幻")], intent="recommend")
        steps = [
            _plan(
                payload,
                [{"name": "get_local_recommendations", "arguments": {"limit": 10}}],
            ),
            _final([expected], "合成模型解释：依据本人偏好与库内候选。"),
        ]
        run = self.run_agent(BYOK_CONFIG, steps, user=102, message="推荐科幻片")
        result = run.result
        self.assertEqual(result["source"], "MOCK")
        self.assertEqual(result["completion_protocol"], "LEGACY_JSON")
        self.assertEqual([item["movie_id"] for item in result["items"]], [expected])
        self.assertEqual(result["conditions"], {"genre": "科幻"})
        self.assertEqual(len(run.ledger.dispatches), 2)
        self.assertEqual(run.provider.calls, 2)
        self.assertTrue(all(d["paid"] is False for d in run.ledger.dispatches))
        self.assertTrue(all(r["paid"] is False for r in run.ledger.records))
        snapshot = {m["movie_id"]: m for m in self.reader.snapshot(102)[0]}
        self.assert_grounded_item(result["items"][0], snapshot)
        text = "\n".join(str(m.get("content") or "") for m in run.provider.last_messages)
        self.assertIn("Verified current-user behavior summary", text)
        self.assertIn('"favorite": 1', text)
        self.assertIn("剧情", text)  # current user's preferred genre is visible
        tool_text = "\n".join(
            str(m.get("content") or "")
            for m in run.provider.last_messages
            if m.get("role") == "tool"
        )
        self.assertIn("personalized_candidates", tool_text)
        self.assertIn('"name": "get_local_recommendations"', tool_text)
        self.assertIn(str(expected), tool_text)
        self.assertTrue(all(d["daily"] is None for d in run.ledger.dispatches))

    def test_unobserved_and_nonexistent_final_ids_are_rejected_without_retry(self):
        payload = update([set_("genre", "科幻")], intent="recommend")
        run = self.run_agent(
            BYOK_CONFIG, [_plan(payload, []), _final([999999])], user=102
        )
        result = run.result
        self.assertEqual(result["source"], "FALLBACK")
        self.assertEqual(result["failure_code"], "MODEL_OUTPUT_REJECTED")
        self.assertEqual(result["failure_details"]["reason"], "FINAL_VALUE_OR_EVIDENCE_INVALID")
        self.assertNotIn(999999, [item["movie_id"] for item in result["items"]])
        self.assertTrue(result["items"])
        self.assertEqual(len(run.ledger.dispatches), 2)
        self.assertEqual(run.provider.calls, 2)

    def test_observed_but_condition_violating_final_id_is_rejected(self):
        payload = update([set_("genre", "科幻")], intent="recommend")
        steps = [
            _plan(
                payload,
                [{"name": "get_movie_detail", "arguments": {"movie_id": 910007}}],
            ),
            _final([910007]),
        ]
        run = self.run_agent(BYOK_CONFIG, steps, user=102)
        result = run.result
        self.assertEqual(result["source"], "FALLBACK")
        self.assertEqual(result["failure_code"], "MODEL_CONDITION_REJECTED")
        self.assertEqual(result["failure_details"]["reason"], "FINAL_CONDITION_CONFLICT")
        self.assertNotIn(910007, [item["movie_id"] for item in result["items"]])
        self.assertEqual(len(run.ledger.dispatches), 2)
        self.assertEqual(run.provider.calls, 2)

    def test_id_changed_to_not_interested_after_observation_is_rejected(self):
        payload = update([set_("genre", "科幻")], intent="recommend")
        steps = [
            _plan(
                payload,
                [{"name": "get_movie_detail", "arguments": {"movie_id": 910003}}],
            ),
            _final([910003]),
        ]
        hooks = {2: lambda: self.behavior.set_state(102, 910003, {"not_interested": True})}
        run = self.run_agent(BYOK_CONFIG, steps, hooks=hooks, user=102)
        result = run.result
        self.assertEqual(result["source"], "FALLBACK")
        self.assertEqual(result["failure_code"], "MODEL_CONDITION_REJECTED")
        # The fallback items were computed at plan time, before the hook changed
        # state; the rejected final ID itself is never accepted.
        self.assertEqual(len(run.ledger.dispatches), 2)

    def test_id_hidden_after_observation_is_rejected(self):
        payload = update([set_("genre", "科幻")], intent="recommend")
        steps = [
            _plan(
                payload,
                [{"name": "get_movie_detail", "arguments": {"movie_id": 910011}}],
            ),
            _final([910011]),
        ]

        def hide():
            with self.engine.begin() as connection:
                connection.execute(
                    CatalogBase.metadata.tables["movies"]
                    .update()
                    .where(CatalogBase.metadata.tables["movies"].c.movie_id == 910011)
                    .values(catalog_status="hidden")
                )
            invalidate_catalog(self.engine)

        run = self.run_agent(BYOK_CONFIG, steps, hooks={2: hide}, user=102)
        result = run.result
        self.assertEqual(result["source"], "FALLBACK")
        self.assertEqual(result["failure_code"], "MODEL_OUTPUT_REJECTED")
        self.assertEqual(result["failure_details"]["reason"], "FINAL_VISIBILITY_CHANGED")
        self.assertEqual(len(run.ledger.dispatches), 2)

    def test_transport_unknown_is_recorded_once_without_retry(self):
        run = self.run_agent(
            BYOK_CONFIG,
            [ProviderError("LLM_UNAVAILABLE", True)],
            user=102,
            message="推荐科幻片",
        )
        result = run.result
        self.assertEqual(result["source"], "FALLBACK")
        self.assertEqual(result["failure_code"], "LLM_UNAVAILABLE")
        self.assertEqual(result["failure_details"]["stage"], "PROVIDER")
        self.assertEqual(len(run.ledger.dispatches), 1)
        self.assertEqual(run.provider.calls, 1)
        self.assertEqual(run.ledger.records[0]["status"], "UNKNOWN")
        self.assertFalse(run.ledger.records[0]["paid"])
        self.assertNotIn("SYNTHETIC_ONLY_KEY", json.dumps(result, ensure_ascii=False))


class BoardIntegrationTests(_CatalogCase):
    def test_personal_board_uses_m10_ranking_public_board_keeps_snapshot(self):
        self.behavior.set_state(101, 910001, {"favorite": True})
        personal = self.reader.recommendation_board(101, "personal", {}, limit=5)
        self.assertEqual(personal["paid_model_tokens"], 0)
        self.assertTrue(personal["items"])
        self.assertGreaterEqual(personal["candidate_count"], len(personal["items"]))
        self.assertEqual(
            personal["items"][0]["recommendation_version"], RECOMMENDATION_VERSION
        )
        self.assertTrue(personal["items"][0]["evidence"])
        self.assertEqual(personal["items"][0]["in_catalog"], True)
        public = self.reader.recommendation_board(101, "popular", {})
        self.assertEqual(public["paid_model_tokens"], 0)
        self.assertIn(public["status"], ("OK", "STALE"))
        self.assertEqual(public["source"], "TMDB_OFFICIAL_SNAPSHOT")
        self.assertTrue(public["items"])
        for item in public["items"]:
            self.assertEqual(item["recommendation_version"], "boards-m10-v2")
            self.assertFalse(item["in_catalog"])  # synthetic catalog has no real ids

    def test_daily_score_equals_components_without_diversity_and_bonus_is_evidenced(self):
        """D2 re-check: raw score is reproduced by components minus the positive
        diversity penalty, and the daily/weekly TMDB rating bonus is exposed as
        its own component, reason code and evidence row."""
        self.behavior.set_state(101, 910001, {"favorite": True})
        saw_diversity = False
        for channel in ("personal", "daily", "weekly"):
            result = self.reader.recommendation_board(101, channel, {}, limit=5)
            self.assertEqual(result["paid_model_tokens"], 0)
            self.assertTrue(result["items"])
            for item in result["items"]:
                self.assertEqual(item["score_basis"], "RAW_BEFORE_DIVERSITY")
                components = item["components"]
                raw = round(
                    sum(
                        value
                        for key, value in components.items()
                        if key != "diversity"
                    ),
                    8,
                )
                self.assertAlmostEqual(item["score"], raw, places=6)
                if components["diversity"] > 0:
                    saw_diversity = True
                rated = (
                    item.get("tmdb_rating") is not None
                    and (item.get("vote_count") or 0) > 0
                )
                if channel == "personal" or not rated:
                    self.assertNotIn("board_rating_prior", components)
                    continue
                bonus = components["board_rating_prior"]
                self.assertGreater(bonus, 0)
                self.assertIn("PERIOD_RATING_PRIOR", item["reason_codes"])
                evidence = [
                    row
                    for row in item["evidence"]
                    if row["source"] == "TMDB_DATABASE_SNAPSHOT"
                    and row["field"] == "tmdb_rating"
                ]
                self.assertEqual(len(evidence), 1)
                self.assertEqual(evidence[0]["value"], item["tmdb_rating"])
                self.assertEqual(evidence[0]["vote_count"], item["vote_count"])
                self.assertAlmostEqual(
                    evidence[0]["contribution"], bonus, places=8
                )
                without_bonus = round(
                    sum(
                        value
                        for key, value in components.items()
                        if key not in ("diversity", "board_rating_prior")
                    ),
                    8,
                )
                self.assertAlmostEqual(item["score"] - bonus, without_bonus, places=6)
        self.assertTrue(saw_diversity)
        self.assertEqual(
            self.reader.recommendation_board(101, "daily", {}, limit=3)["cache_status"],
            "DETERMINISTIC_ONLY",
        )


class EvidenceBoundaryTests(_CatalogCase):
    def test_model_prose_is_not_semantically_verified_but_cards_are_server_facts(self):
        """Pins the current boundary; a future prose checker should update this test."""
        tool = AgentTools(self.reader, 101)
        result = tool.execute(
            "get_local_recommendations", {"filters": {"genre": "科幻"}, "limit": 5}
        )
        top = result["items"][0]["movie_id"]
        false_prose = "这些其实都是恐怖片，而且每部评分都是 9.9 分。"
        final = verify_final(
            json.dumps(
                {"answer": false_prose, "movie_ids": [top], "discoveries": []},
                ensure_ascii=False,
            ),
            tool.observed,
            self.reader,
            101,
            {"genre": "科幻"},
        )
        self.assertEqual(final["answer"], false_prose)  # text is the model's, unverified
        item = final["items"][0]
        self.assertEqual(item["verification"], "LOCAL_DATABASE")
        self.assertNotIn("9.9", json.dumps(item["evidence"], ensure_ascii=False))
        self.assertNotIn(false_prose, item["reasons"])
        snapshot = {m["movie_id"]: m for m in self.reader.snapshot(101)[0]}
        self.assert_grounded_item(item, snapshot)

    def test_missing_popularity_stays_unprovided_and_real_zero_stays_zero(self):
        """D1 re-check: a missing TMDB popularity is None (no prior evidence),
        while a real 0 popularity without any positive maximum is 0.0."""
        movies = self.reader.snapshot(101)[0]
        self.assertIsNone(self.item(movies, 910008)["catalog_popularity"])
        self.assertEqual(self.item(movies, 910003)["catalog_popularity"], 1.0)
        evidence = self.reader.recommendation_evidence(
            101, [910008], {"genre": "科幻"}
        )[910008]
        self.assertFalse(
            any(
                row.get("field") == "catalog_popularity"
                for row in evidence["evidence"]
            )
        )
        table = CatalogBase.metadata.tables["movie_source_records"]
        with self.engine.begin() as connection:
            rows = connection.execute(select(table.c.id, table.c.metadata_json)).all()
            for record_id, payload in rows:
                payload = dict(payload or {})
                payload["popularity"] = 0
                connection.execute(
                    table.update()
                    .where(table.c.id == record_id)
                    .values(metadata_json=payload)
                )
        invalidate_catalog(self.engine)
        zeroed = {m["movie_id"]: m for m in self.reader.snapshot(101)[0]}
        self.assertEqual(zeroed[910003]["catalog_popularity"], 0.0)
        self.assertIsNone(zeroed[910008]["catalog_popularity"])


def setUpModule():
    global _NETWORK_PATCH
    _NETWORK_PATCH = patch(
        "urllib.request.build_opener",
        side_effect=AssertionError("network is forbidden in M10 integration checks"),
    )
    _NETWORK_PATCH.start()


def tearDownModule():
    if _NETWORK_PATCH is not None:
        _NETWORK_PATCH.stop()


if __name__ == "__main__":
    unittest.main()