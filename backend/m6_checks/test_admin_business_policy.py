"""Isolated admin business-policy checks.

No real account, password, database, model, network or .env file is touched.
The HTTP checks use the actual auth session/CSRF helpers and the actual
assistant_v2 guard; only the M7 service dependencies and budget engine are
replaced with in-memory/SQLite boundaries.  The AgentService checks use the
real service and real Ledger over SQLite, with only the M7 MySQL upsert adapted
for SQLite and a scripted provider.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask
from sqlalchemy import create_engine, event
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app import create_app
from app.auth import hashed
from app.models import AuthSession, Base, User
from assistant.agent_provider import ScriptedAgentProvider
from assistant.agent_service import AgentService
from assistant.api_v2 import install as install_assistant
from assistant.ledger_v2 import Ledger
from assistant.store import tables

ORIGIN = "http://127.0.0.1:5174"
ADMIN_NAMES = ["zhang", "deepseek", "codex"]
NORMAL_NAME = "synthetic_ordinary"


def _collate(left, right):
    return (left > right) - (left < right)


def _sqlite_engine(*metadatas):
    engine = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
        hide_parameters=True,
    )

    @event.listens_for(engine, "connect")
    def _configure(dbapi_connection, _record):
        dbapi_connection.create_collation("utf8mb4_bin", _collate)

    for metadata in metadatas:
        metadata.create_all(engine)
    return engine


def _hash(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class _BudgetResult:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value

    def scalar_one_or_none(self):
        return self.value


class _BudgetConnection:
    def __init__(self, engine):
        self.engine = engine

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def execute(self, *_args, **_kwargs):
        return _BudgetResult(self.engine.count)


class _BudgetEngine:
    """Minimal engine boundary for the guard's MySQL upsert/select statements."""

    def __init__(self, count):
        self.count = count
        self.begin_calls = 0
        self.connect_calls = 0

    def begin(self):
        self.begin_calls += 1
        return _BudgetConnection(self)

    def connect(self):
        self.connect_calls += 1
        return _BudgetConnection(self)


class _FakeVault:
    def __init__(self, store=None):
        self.store = store
        self.last_body = None

    def status(self, _user, _binding):
        return {
            "configured": True,
            "enabled": True,
            "provider": "deepseek",
            "model": "synthetic-model",
            "verification": "VERIFIED",
            "expires_in_seconds": None,
            "daily_calls": 5,
            "max_output_tokens": 2048,
            "cache_enabled": False,
            "custody": "SYNTHETIC",
            "persistent": False,
        }

    def put(self, _user, _binding, body):
        self.last_body = body
        return self.status(_user, _binding)


class _FakeLedger:
    def __init__(self, _engine, _md):
        pass

    def cleanup(self):
        return None

    def claim(self, user, binding, rid, fingerprint):
        return "synthetic-key", {"state": "DONE"}, True

    def read(self, _key):
        return {"state": "DONE", "result": {}}

    def cost(self, _key):
        return {"provider_calls": 0}

    def key(self, user, rid):
        return "synthetic-key"


class _FakeService:
    launches = []

    def __init__(self, *_args, **_kwargs):
        pass

    def conversation(self, *_args, **_kwargs):
        return {}

    def launch(self, *args, **kwargs):
        self.__class__.launches.append({"args": args, "kwargs": kwargs})


class _FakeReader:
    def __init__(self, *_args, **_kwargs):
        pass


class _HttpCase(unittest.TestCase):
    def _app(self, budget_count=61):
        auth_engine = _sqlite_engine(Base.metadata)
        auth_db = sessionmaker(auth_engine, expire_on_commit=False)
        now = datetime.utcnow()
        sessions = {}
        with auth_db.begin() as db:
            for index, name in enumerate(ADMIN_NAMES + [NORMAL_NAME], start=1):
                user = User(
                    id=index,
                    username=name,
                    username_normalized=name,
                    password_hash="SYNTHETIC_NOT_A_REAL_HASH",
                    status="active",
                    created_at=now,
                    updated_at=now,
                )
                db.add(user)
                db.flush()
                sid = "sid_" + name + "_" + ("x" * 32)
                csrf = "csrf_" + name + "_" + ("y" * 32)
                db.add(
                    AuthSession(
                        id=index,
                        sid_hash=_hash(sid),
                        user_id=user.id,
                        csrf_token_hash=_hash(csrf),
                        created_at=now,
                        last_seen_at=now,
                        idle_expires_at=now + timedelta(hours=1),
                        absolute_expires_at=now + timedelta(hours=2),
                    )
                )
                sessions[name] = (sid, csrf)

        budget_engine = _BudgetEngine(budget_count)
        _FakeService.launches = []
        app = Flask(__name__)
        app.config.update(
            TESTING=True,
            M7_DATABASE_URL="sqlite://",
            M6_SERVER_UUID="synthetic-server",
            MEDIA_CACHE_DIR=str(Path(".")),
            AUTH_COOKIE_NAME="mi2_sid",
            AUTH_COOKIE_PATH="/api/v1",
            IDLE_SECONDS=7200,
            ABSOLUTE_SECONDS=86400,
            COOKIE_SECURE=False,
            ALLOWED_ORIGINS=[ORIGIN],
            ADMIN_USERS=list(ADMIN_NAMES),
        )
        app.extensions["auth_engine"] = auth_engine
        app.extensions["auth_db"] = auth_db
        app.extensions["m7_engine"] = budget_engine
        app.extensions["m6_behavior"] = None
        app.extensions["tmdb_connection"] = None
        with patch.multiple(
            "assistant.api_v2",
            CatalogReader=_FakeReader,
            AgentService=_FakeService,
            Vault=_FakeVault,
            Ledger=_FakeLedger,
        ):
            install_assistant(app)
        return app, budget_engine, sessions

    def _client(self, app, sessions, name):
        client = app.test_client()
        sid, _csrf = sessions[name]
        client.set_cookie("mi2_sid", sid, path="/api/v1")
        return client

    @staticmethod
    def _headers(sessions, name):
        _sid, csrf = sessions[name]
        return {"Origin": ORIGIN, "X-CSRF-Token": csrf}

    def test_three_default_admins_skip_budget_and_report_unlimited_quota(self):
        app, budget, sessions = self._app(budget_count=61)
        for name in ADMIN_NAMES:
            with self.subTest(admin=name):
                client = self._client(app, sessions, name)
                before = budget.begin_calls
                response = client.post(
                    "/api/v1/assistant/config",
                    json={},
                    headers=self._headers(sessions, name),
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(budget.begin_calls, before)

                status = client.get("/api/v1/assistant/status")
                self.assertEqual(status.status_code, 200)
                data = status.json["data"]
                self.assertTrue(data["business_quota_exempt"])
                self.assertIsNone(data["daily_calls_remaining"])
                self.assertEqual(data["daily_calls"], 5)
                self.assertEqual(data["daily_calls_used"], 61)

    def test_normal_user_budget_and_client_flags_do_not_exempt(self):
        app, budget, sessions = self._app(budget_count=61)
        client = self._client(app, sessions, NORMAL_NAME)
        before = budget.begin_calls
        response = client.post(
            "/api/v1/assistant/config",
            json={},
            headers={
                **self._headers(sessions, NORMAL_NAME),
                "X-Admin": "true",
                "X-Business-Exempt": "true",
            },
        )
        self.assertEqual(response.status_code, 429)
        self.assertEqual(budget.begin_calls, before + 1)

        status = client.get("/api/v1/assistant/status")
        data = status.json["data"]
        self.assertFalse(data["business_quota_exempt"])
        self.assertEqual(data["daily_calls_remaining"], 0)

        # A separate under-budget app proves the JSON field is rejected by the
        # normal route rather than silently becoming a server exemption.
        app2, budget2, sessions2 = self._app(budget_count=0)
        under_budget = self._client(app2, sessions2, NORMAL_NAME)
        forged = under_budget.post(
            "/api/v1/assistant/query",
            json={
                "message": "synthetic",
                "request_id": "synthetic_request_1",
                "stream": False,
                "business_exempt": True,
            },
            headers=self._headers(sessions2, NORMAL_NAME),
        )
        self.assertEqual(forged.status_code, 400)
        self.assertEqual(budget2.begin_calls, 1)

    def test_authentication_and_csrf_failures_still_win(self):
        app, budget, sessions = self._app(budget_count=61)
        unauthenticated = app.test_client().get("/api/v1/assistant/status")
        self.assertEqual(unauthenticated.status_code, 401)

        admin = self._client(app, sessions, "zhang")
        before = budget.begin_calls
        no_csrf = admin.post("/api/v1/assistant/config", json={})
        self.assertEqual(no_csrf.status_code, 403)
        self.assertEqual(budget.begin_calls, before)

        # A body/header self-report cannot turn the admin flag into a bypass
        # for a request that has not passed CSRF or authentication.
        forged = admin.post(
            "/api/v1/assistant/config",
            json={"business_exempt": True, "is_admin": True},
            headers={"Origin": ORIGIN, "X-Admin": "true"},
        )
        self.assertEqual(forged.status_code, 403)
        self.assertEqual(budget.begin_calls, before)

    def test_admin_cannot_inject_exemption_through_json(self):
        app, budget, sessions = self._app(budget_count=61)
        admin = self._client(app, sessions, "zhang")
        before = budget.begin_calls
        response = admin.post(
            "/api/v1/assistant/query",
            json={
                "message": "synthetic",
                "request_id": "synthetic_request_2",
                "stream": False,
                "business_exempt": True,
            },
            headers=self._headers(sessions, "zhang"),
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(budget.begin_calls, before)

    def test_server_identity_passes_business_exempt_into_launch_only(self):
        app, budget, sessions = self._app(budget_count=0)
        body = {
            "message": "synthetic",
            "request_id": "synthetic_request_3",
            "stream": False,
        }
        admin = self._client(app, sessions, "zhang")
        admin_response = admin.post(
            "/api/v1/assistant/query",
            json=body,
            headers=self._headers(sessions, "zhang"),
        )
        self.assertEqual(admin_response.status_code, 200)
        normal = self._client(app, sessions, NORMAL_NAME)
        normal_response = normal.post(
            "/api/v1/assistant/query",
            json=body,
            headers=self._headers(sessions, NORMAL_NAME),
        )
        self.assertEqual(normal_response.status_code, 200)
        self.assertEqual(budget.begin_calls, 1)
        self.assertEqual(
            [call["kwargs"].get("business_exempt") for call in _FakeService.launches],
            [True, False],
        )


def _sqlite_upsert(table):
    """Adapt only the MySQL ON DUPLICATE KEY builder used by the real Ledger."""

    class _Adapter:
        def __init__(self):
            self.stmt = sqlite_insert(table)

        @property
        def inserted(self):
            return SimpleNamespace(id=table.c.id)

        def _execute_on_connection(self, connection, distilled_params, execution_options):
            return self.stmt._execute_on_connection(
                connection, distilled_params, execution_options
            )

        def values(self, **values):
            self.stmt = self.stmt.values(**values)
            return self

        def on_duplicate_key_update(self, **values):
            self.stmt = self.stmt.on_conflict_do_update(
                index_elements=["id"], set_=values
            )
            return self

    return _Adapter()


class _AgentVault:
    def __init__(self, daily_calls):
        self.config_value = {
            "generation": 1,
            "model": "mock-agent",
            "enabled": True,
            "verification": "VERIFIED",
            "daily_calls": daily_calls,
            "max_output_tokens": 2048,
            "cache_enabled": False,
        }

    def config(self, _user, _binding):
        return dict(self.config_value)

    def key(self, _user, _binding, _generation):
        return "SYNTHETIC_AGENT_KEY_NOT_A_REAL_SECRET"

    def verified(self, *_args):
        return None


class _AgentReader:
    tmdb = None

    def __init__(self):
        self.movie = {
            "movie_id": 1,
            "title": "合成策略测试片",
            "release_year": 2020,
            "runtime_minutes": 100,
            "original_language": "zh",
            "genres": ["剧情"],
            "is_adult": False,
            "poster": None,
        }
        self.behavior = SimpleNamespace(snapshot=lambda _user: {})

    def snapshot(self, _user):
        return ([dict(self.movie)], {}, False)

    def get_movies(self, ids):
        return [dict(self.movie)] if 1 in ids else []

    def resolve(self, _user, _intent, _filters, _shown=None, **_kwargs):
        return ([dict(self.movie)], {}, False, False)


def _plan_step():
    return {
        "content": "",
        "tool_calls": [
            {
                "id": "synthetic-plan-1",
                "type": "function",
                "function": {
                    "name": "plan_movie_turn",
                    "arguments": json.dumps(
                        {
                            "intent_update": {
                                "version": 1,
                                "intent": "search",
                                "behavior": "want",
                                "changes": [],
                                "uncertain": False,
                                "clarification": "",
                            },
                            "actions": [
                                {
                                    "name": "get_movie_detail",
                                    "arguments": {"movie_id": 1},
                                }
                            ],
                        }
                    ),
                },
            }
        ],
    }


def _final_step():
    return {
        "content": json.dumps(
            {
                "answer": "合成模型解释，依据服务端工具返回的候选。",
                "movie_ids": [1],
                "discoveries": [],
            },
            ensure_ascii=False,
        )
    }


class _ThreadConnection:
    def execute(self, *_args, **_kwargs):
        return SimpleNamespace(scalar=lambda: 1)

    def commit(self):
        return None

    def close(self):
        return None


class _ThreadEngine:
    def connect(self):
        return _ThreadConnection()


class _NoopLedger:
    def finish(self, *_args, **_kwargs):
        return None


class AgentBusinessIntegrationTests(unittest.TestCase):
    def test_real_agent_service_and_ledger_receive_daily_only_for_normal_user(self):
        store_engine = _sqlite_engine(tables())
        md = tables()
        ledger = Ledger(store_engine, md)
        reader = _AgentReader()
        vault = _AgentVault(daily_calls=10)
        day = datetime.utcnow().date().isoformat()
        admin_budget_id = f"byok:{day}:2:mock"
        with store_engine.begin() as connection:
            # Already at the normal daily limit; business_exempt must still
            # dispatch, while a normal user at this count would be quota-blocked.
            connection.execute(
                md.tables["budgets"].insert().values(id=admin_budget_id, calls=10)
            )

        claims = {}
        for user, rid in ((1, "synthetic_normal_request"), (2, "synthetic_admin_request")):
            key, _state, new = ledger.claim(
                user, "binding-" + str(user), rid, "fingerprint-" + str(user)
            )
            self.assertTrue(new)
            claims[user] = key

        dispatched = []
        original_dispatch = ledger.dispatch

        def recording_dispatch(key, user, model, daily, paid):
            dispatched.append({"key": key, "user": user, "daily": daily, "paid": paid})
            return original_dispatch(key, user, model, daily, paid)

        ledger.dispatch = recording_dispatch

        def factory(_key, _config):
            return ScriptedAgentProvider([_plan_step(), _final_step()])

        service = AgentService(store_engine, md, reader, vault, ledger, factory)
        with patch("assistant.ledger_v2.upsert", side_effect=_sqlite_upsert):
            for user, business_exempt in ((1, False), (2, True)):
                runtime = {
                    "chunks": [],
                    "draft": "",
                    "cancel": threading.Event(),
                    "user": user,
                    "binding": "binding-" + str(user),
                    "business_exempt": business_exempt,
                }
                service.run(
                    claims[user],
                    user,
                    "binding-" + str(user),
                    "推荐一部电影",
                    None,
                    "query",
                    None,
                    runtime,
                )
                self.assertEqual(ledger.read(claims[user])["state"], "DONE")

        self.assertEqual(len(dispatched), 4)
        self.assertEqual([item["daily"] for item in dispatched[:2]], [10, 10])
        self.assertEqual([item["daily"] for item in dispatched[2:]], [None, None])
        self.assertTrue(all(item["paid"] is False for item in dispatched))
        with store_engine.connect() as connection:
            normal = connection.execute(
                md.tables["budgets"].select().where(
                    md.tables["budgets"].c.id == f"byok:{day}:1:mock"
                )
            ).mappings().one()
            admin = connection.execute(
                md.tables["budgets"].select().where(
                    md.tables["budgets"].c.id == admin_budget_id
                )
            ).mappings().one()
            usage_count = connection.execute(
                md.tables["usage"].select()
            ).mappings().all()
        self.assertEqual(normal["calls"], 2)
        self.assertEqual(admin["calls"], 12)
        self.assertEqual(len(usage_count), 4)

    def test_launch_stores_server_business_exemption_in_runtime(self):
        service = AgentService(
            _ThreadEngine(), None, None, None, _NoopLedger(), None
        )
        captured = {}
        done = threading.Event()

        def fake_run(_key, _user, _binding, _message, _cid, _kind, _movie_id, runtime):
            captured.update(runtime)
            done.set()

        service.run = fake_run
        service.launch(
            "synthetic-thread-request",
            7,
            "synthetic-binding",
            "message",
            None,
            "query",
            None,
            business_exempt=True,
        )
        self.assertTrue(done.wait(2), "launch worker did not run")
        self.assertIs(captured.get("business_exempt"), True)


class ConfigDefaultTests(unittest.TestCase):
    def _admin_users(self, env):
        engine = create_engine("sqlite://")
        try:
            with patch.dict(os.environ, env, clear=True), patch(
                "app.create_engine", return_value=engine
            ):
                application = create_app({"ALLOWED_ORIGINS": [ORIGIN]})
                return list(application.config["ADMIN_USERS"])
        finally:
            engine.dispose()

    def test_default_admins_are_the_three_authorized_accounts(self):
        values = self._admin_users(
            {
                "MI2_DATABASE_URL": "mysql+pymysql://synthetic:placeholder@127.0.0.1:3306/mi2_auth_dev",
            }
        )
        self.assertEqual(values, ADMIN_NAMES)

    def test_environment_list_still_takes_precedence(self):
        values = self._admin_users(
            {
                "MI2_DATABASE_URL": "mysql+pymysql://synthetic:placeholder@127.0.0.1:3306/mi2_auth_dev",
                "MI2_ADMIN_USERS": " ops , zhang,deepseek,codex , extra ",
            }
        )
        self.assertEqual(values, ["ops", "zhang", "deepseek", "codex", "extra"])


if __name__ == "__main__":
    unittest.main()