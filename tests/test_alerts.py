"""Offline tests for infomobilità de-duplication (ticker vs. box titles)."""
from __future__ import annotations

import pytest

import storage as storage_mod
from storage import Storage
from bot import cards, notifications


class FakeBot:
    def __init__(self):
        self.messages = []

    async def send_message(self, chat_id, text, parse_mode=None):
        self.messages.append((chat_id, text))


class FakeApp:
    def __init__(self, storage):
        self.bot_data = {"storage": storage}
        self.bot = FakeBot()


@pytest.fixture
async def store(tmp_path, monkeypatch):
    monkeypatch.setattr(storage_mod, "DB_PATH", tmp_path / "alerts.sqlite3")
    s = Storage()
    await s.connect()
    yield s
    await s.close()


def _patch_sources(monkeypatch, ticker, titles, news=None):
    async def fake_ticker():
        return ticker

    async def fake_titles(is_lavori=False):
        return list(titles)

    async def fake_news(region_code=0, lang="it"):
        return list(news or [])

    monkeypatch.setattr(notifications.vt, "ticker", fake_ticker)
    monkeypatch.setattr(notifications.vt, "infomobilita_titles", fake_titles)
    monkeypatch.setattr(notifications.vt, "news", fake_news)


ALERT = (
    "Nodo di Bologna: circolazione rallentata dalle ore 8:40 per  la presenza "
    "di persone non autorizzate nei pressi della linea"
)


async def test_alerts_text_drops_duplicate_ticker():
    text = cards.alerts_text(ALERT, [ALERT], [])
    assert ALERT in text
    # Rendered once (only under the "Avvisi di circolazione" list), not twice.
    assert text.count("Nodo di Bologna") == 1
    assert "<i>Avvisi di circolazione:</i>" in text


async def test_alerts_text_keeps_distinct_ticker():
    text = cards.alerts_text("CIRCOLAZIONE RALLENTATA SU TUTTA LA RETE", [ALERT], [])
    assert "CIRCOLAZIONE RALLENTATA SU TUTTA LA RETE" in text
    assert ALERT in text


async def test_poll_once_sends_duplicate_content_once(store, monkeypatch):
    _patch_sources(monkeypatch, ticker=ALERT, titles=[ALERT])
    await store.get(1)  # create chat row (alerts default True)
    app = FakeApp(store)

    # Prime the poller (records state without broadcasting).
    await notifications.poll_once(app, broadcast=False)
    assert app.bot.messages == []

    # A single announcement must not fire a broadcast: it was already recorded.
    await notifications.poll_once(app, broadcast=True)
    assert app.bot.messages == []


async def test_poll_once_broadcasts_new_title_once(store, monkeypatch):
    _patch_sources(monkeypatch, ticker="", titles=[ALERT])
    await store.get(1)
    app = FakeApp(store)

    await notifications.poll_once(app, broadcast=False)
    app.bot.messages.clear()

    # Brand new title in the next cycle -> exactly one message, one occurrence.
    _patch_sources(monkeypatch, ticker="", titles=[ALERT, "NUOVO AVVISO"])
    await notifications.poll_once(app, broadcast=True)
    assert len(app.bot.messages) == 1
    _, text = app.bot.messages[0]
    assert "NUOVO AVVISO" in text
    assert text.count(ALERT) <= 1


async def test_ticker_duplicate_of_already_sent_title_is_silent(store, monkeypatch):
    await store.get(1)
    app = FakeApp(store)

    # Prime with an empty box (records nothing).
    _patch_sources(monkeypatch, ticker="CIRCOLAZIONE REGOLARE", titles=[])
    await notifications.poll_once(app, broadcast=False)
    assert app.bot.messages == []

    # Cycle 1: the box title arrives (ticker still generic) -> one message.
    _patch_sources(monkeypatch, ticker="CIRCOLAZIONE REGOLARE", titles=[ALERT])
    await notifications.poll_once(app, broadcast=True)
    assert len(app.bot.messages) == 1
    app.bot.messages.clear()

    # Cycle 2: the ticker catches up with the same text -> must stay silent.
    _patch_sources(monkeypatch, ticker=ALERT, titles=[ALERT])
    await notifications.poll_once(app, broadcast=True)
    assert app.bot.messages == []


async def test_ticker_and_title_same_cycle_render_once(store, monkeypatch):
    await store.get(1)
    app = FakeApp(store)

    # Prime with the content already present so it is not broadcast.
    _patch_sources(monkeypatch, ticker="", titles=[])
    await notifications.poll_once(app, broadcast=False)

    # Both sources carry the same brand-new text in the same cycle.
    _patch_sources(monkeypatch, ticker=ALERT, titles=[ALERT])
    await notifications.poll_once(app, broadcast=True)
    assert len(app.bot.messages) == 1
    _, text = app.bot.messages[0]
    assert text.count("Nodo di Bologna") == 1
