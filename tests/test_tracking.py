import pytest

import storage as storage_mod
from storage import Storage
from trenitalia.models import Stop, TrainStatus
from bot import notifications


class FakeBot:
    def __init__(self):
        self.messages = []

    async def send_message(self, chat_id, text, parse_mode=None):
        self.messages.append((chat_id, text))


class FakeApp:
    def __init__(self, storage):
        self.bot_data = {"storage": storage}
        self.bot = FakeBot()


def make_status(delay=0, arrived=False, cancelled=False):
    stops = [
        Stop(station="MODENA", scheduled=None, actual=None, delay=0, passed=True),
        Stop(station="MANTOVA", scheduled=None, actual=None, delay=0, passed=arrived),
    ]
    return TrainStatus(
        number=22470,
        category="REG",
        origin="MODENA",
        destination="MANTOVA",
        delay=delay,
        last_detected_station="MANTOVA" if arrived else "MODENA",
        scheduled_departure=None,
        scheduled_arrival=None,
        tipo_treno="ST" if cancelled else "PG",
        provvedimento=0,
        stops=[] if cancelled else stops,
        suppressed=cancelled,
    )


@pytest.fixture
async def store(tmp_path, monkeypatch):
    monkeypatch.setattr(storage_mod, "DB_PATH", tmp_path / "t.sqlite3")
    s = Storage()
    await s.connect()
    yield s
    await s.close()


def _patch_status(monkeypatch, status):
    async def fake(ref):
        return status
    monkeypatch.setattr(notifications.vt, "train_status", fake)


async def test_no_change_no_message(store, monkeypatch):
    status = make_status(delay=5)
    _patch_status(monkeypatch, status)
    await store.add_tracked(1, 22470, "S05032", 0)
    await store.set_tracked_sig(1, 22470, "S05032", notifications.status_signature(status))
    app = FakeApp(store)
    await notifications.poll_tracked_once(app)
    assert app.bot.messages == []


async def test_change_sends_then_silent(store, monkeypatch):
    status = make_status(delay=7)
    _patch_status(monkeypatch, status)
    await store.add_tracked(1, 22470, "S05032", 0)
    await store.set_tracked_sig(1, 22470, "S05032", "OLD")
    app = FakeApp(store)
    await notifications.poll_tracked_once(app)
    assert len(app.bot.messages) == 1
    app.bot.messages.clear()
    await notifications.poll_tracked_once(app)
    assert app.bot.messages == []


async def test_arrived_ends_tracking(store, monkeypatch):
    _patch_status(monkeypatch, make_status(arrived=True))
    await store.add_tracked(1, 22470, "S05032", 0)
    app = FakeApp(store)
    await notifications.poll_tracked_once(app)
    assert len(app.bot.messages) == 1
    assert await store.all_tracked() == []


async def test_cancelled_ends_tracking(store, monkeypatch):
    _patch_status(monkeypatch, make_status(cancelled=True))
    await store.add_tracked(1, 22470, "S05032", 0)
    app = FakeApp(store)
    await notifications.poll_tracked_once(app)
    assert len(app.bot.messages) == 1
    assert await store.all_tracked() == []
