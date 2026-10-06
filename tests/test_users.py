"""Offline tests for unique-user counting and the admin /stats command."""
from __future__ import annotations

import pytest

import storage as storage_mod
from storage import Storage
from bot import handlers


class Msg:
    def __init__(self):
        self.sent = []

    async def reply_text(self, text, **kw):
        self.sent.append(text)


class _User:
    def __init__(self, uid, username="", first_name=""):
        self.id = uid
        self.username = username
        self.first_name = first_name


class _Chat:
    def __init__(self, cid):
        self.id = cid


class Update:
    def __init__(self, user):
        self.effective_user = user
        self.effective_chat = _Chat(user.id)
        self.effective_message = Msg()


class App:
    def __init__(self, st):
        self.bot_data = {"storage": st}


class Ctx:
    def __init__(self, app):
        self.application = app


@pytest.fixture
async def store(tmp_path, monkeypatch):
    monkeypatch.setattr(storage_mod, "DB_PATH", tmp_path / "users.sqlite3")
    s = Storage()
    await s.connect()
    yield s
    await s.close()


async def test_record_user_counts_unique_only(store):
    assert await store.count_users() == 0
    assert await store.record_user(1, "anna", "Anna") is True
    assert await store.record_user(2, "bob", "Bob") is True
    assert await store.record_user(1, "anna", "Anna") is False  # duplicate
    assert await store.count_users() == 2


async def test_cmd_start_records_user(store):
    await handlers.cmd_start(Update(_User(7, "gio", "Giovanni")), Ctx(App(store)))
    assert await store.count_users() == 1
    # Re-starting does not double-count.
    await handlers.cmd_start(Update(_User(7, "gio", "Giovanni")), Ctx(App(store)))
    assert await store.count_users() == 1


async def test_cmd_stats_admin_only(store, monkeypatch):
    await store.record_user(1)
    await store.record_user(2)
    monkeypatch.setattr(handlers, "ADMIN_USER_IDS", {999})

    u = Update(_User(1))
    await handlers.cmd_stats(u, Ctx(App(store)))
    reply = u.effective_message.sent[0]
    assert "riservato" in reply.lower()
    assert "2" not in reply

    u = Update(_User(999))
    await handlers.cmd_stats(u, Ctx(App(store)))
    assert "2" in u.effective_message.sent[0]


async def test_cmd_stats_disabled_without_admins(store, monkeypatch):
    await store.record_user(1)
    monkeypatch.setattr(handlers, "ADMIN_USER_IDS", set())
    u = Update(_User(1))
    await handlers.cmd_stats(u, Ctx(App(store)))
    assert "riservato" in u.effective_message.sent[0].lower()