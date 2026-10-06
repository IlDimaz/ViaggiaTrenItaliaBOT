"""Offline unit tests for the SQLite settings store."""
from __future__ import annotations

import pytest

import storage as storage_mod
from storage import Storage


@pytest.fixture
async def store(tmp_path, monkeypatch):
    monkeypatch.setattr(storage_mod, "DB_PATH", tmp_path / "test.sqlite3")
    s = Storage()
    await s.connect()
    yield s
    await s.close()


async def test_defaults(store):
    settings = await store.get(111)
    assert settings.alerts is True
    assert settings.prices is False
    assert settings.station == ""


async def test_toggles_and_station(store):
    await store.set_alerts(111, False)
    await store.set_prices(111, True)
    settings = await store.set_station(111, "SUZZARA", "S02340")
    assert settings.alerts is False
    assert settings.prices is True
    assert settings.station == "SUZZARA"
    assert settings.station_code == "S02340"
    assert await store.chats_with_alerts() == []


async def test_chats_with_alerts(store):
    await store.get(1)
    await store.set_alerts(1, True)
    await store.get(2)
    await store.set_alerts(2, False)
    assert await store.chats_with_alerts() == [1]


async def test_seen_alerts_dedup(store):
    await store.mark_seen(["a", "b"])
    unseen = await store.filter_unseen(["a", "b", "c"])
    assert unseen == ["c"]
