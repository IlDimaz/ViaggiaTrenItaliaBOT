"""Offline tests for the weekly schedule feature."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

import storage as storage_mod
import trenitalia.viaggiatreno as vt
from config import ROME
from storage import Storage
from trenitalia.models import Stop, TrainStatus
from bot import notifications, schedule


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
    monkeypatch.setattr(storage_mod, "DB_PATH", tmp_path / "sched.sqlite3")
    s = Storage()
    await s.connect()
    yield s
    await s.close()


# --- day parsing ---------------------------------------------------------- #

def test_parse_italian_and_english():
    assert schedule.parse_days("lunedì venerdì")[0] == {0, 4}
    assert schedule.parse_days("lun ven")[0] == {0, 4}
    assert schedule.parse_days("mon fri")[0] == {0, 4}
    assert schedule.parse_days("tue wed thu")[0] == {1, 2, 3}


def test_parse_shortcuts_and_ranges():
    assert schedule.parse_days("feriali")[0] == {0, 1, 2, 3, 4}
    assert schedule.parse_days("weekend")[0] == {5, 6}
    assert schedule.parse_days("tutti")[0] == {0, 1, 2, 3, 4, 5, 6}
    assert schedule.parse_days("lun-ven")[0] == {0, 1, 2, 3, 4}
    assert schedule.parse_days("ven-lun")[0] == {0, 4, 5, 6}


def test_parse_reports_unknown():
    days, unknown = schedule.parse_days("lun xyz")
    assert days == {0}
    assert unknown == ["xyz"]


def test_format_and_describe():
    assert schedule.format_days({4, 0}) == "0,4"
    assert schedule.describe_days("0,4") == "lunedì, venerdì"


# --- storage -------------------------------------------------------------- #

async def test_schedule_storage_roundtrip(store):
    await store.set_schedule(1, 16986, "0,4", "S02340")
    items = await store.list_schedules(1)
    assert len(items) == 1
    assert items[0].number == 16986
    assert items[0].weekday_set == {0, 4}
    assert items[0].origin_code == "S02340"

    # Updating keeps the cached origin when none is supplied.
    await store.set_schedule(1, 16986, "1")
    items = await store.list_schedules(1)
    assert items[0].days == "1"
    assert items[0].origin_code == "S02340"

    assert await store.remove_schedule(1, 16986) == 1
    assert await store.list_schedules(1) == []


# --- scheduler poller ----------------------------------------------------- #

def make_status(departure, arrived=False):
    stops = [
        Stop(station="SUZZARA", scheduled=departure, actual=None, delay=0, passed=arrived),
        Stop(
            station="MANTOVA",
            scheduled=departure + timedelta(minutes=30),
            actual=None,
            delay=0,
            passed=arrived,
        ),
    ]
    return TrainStatus(
        number=16986,
        category="REG",
        origin="SUZZARA",
        destination="MANTOVA",
        delay=0,
        last_detected_station="SUZZARA",
        scheduled_departure=departure,
        scheduled_arrival=departure + timedelta(minutes=30),
        tipo_treno="PG",
        provvedimento=0,
        stops=stops,
        suppressed=False,
    )


def _patch_train(monkeypatch, status):
    ref = vt.TrainRef(
        number=status.number,
        origin_code="S02340",
        origin_name="SUZZARA",
        midnight_ms=0,
    )

    async def fake_find(number):
        return [ref]

    async def fake_status(r):
        return status

    monkeypatch.setattr(notifications.vt, "find_train", fake_find)
    monkeypatch.setattr(notifications.vt, "train_status", fake_status)
    monkeypatch.setattr(notifications.vt, "midnight_ms", lambda *a, **k: 0)


ALL_DAYS = "0,1,2,3,4,5,6"


async def test_scheduler_activates_before_departure(store, monkeypatch):
    await store.get(1)
    dep = datetime.now(ROME) + timedelta(minutes=10)
    _patch_train(monkeypatch, make_status(dep))
    await store.set_schedule(1, 16986, ALL_DAYS, "S02340")
    app = FakeApp(store)

    await notifications.poll_schedules_once(app)
    assert len(app.bot.messages) == 1
    tracked = await store.all_tracked()
    assert len(tracked) == 1 and tracked[0].number == 16986

    # Same day: must not activate (or notify) a second time.
    app.bot.messages.clear()
    await notifications.poll_schedules_once(app)
    assert app.bot.messages == []


async def test_scheduler_waits_until_window(store, monkeypatch):
    await store.get(1)
    dep = datetime.now(ROME) + timedelta(hours=3)
    _patch_train(monkeypatch, make_status(dep))
    await store.set_schedule(1, 16986, ALL_DAYS, "S02340")
    app = FakeApp(store)

    await notifications.poll_schedules_once(app)
    assert app.bot.messages == []
    assert await store.all_tracked() == []


async def test_scheduler_skips_other_days(store, monkeypatch):
    await store.get(1)
    today = datetime.now(ROME).weekday()
    other = (today + 1) % 7
    dep = datetime.now(ROME) + timedelta(minutes=10)
    _patch_train(monkeypatch, make_status(dep))
    await store.set_schedule(1, 16986, str(other), "S02340")
    app = FakeApp(store)

    await notifications.poll_schedules_once(app)
    assert app.bot.messages == []
    assert await store.all_tracked() == []


async def test_scheduler_skips_arrived(store, monkeypatch):
    await store.get(1)
    dep = datetime.now(ROME) - timedelta(hours=2)
    _patch_train(monkeypatch, make_status(dep, arrived=True))
    await store.set_schedule(1, 16986, ALL_DAYS, "S02340")
    app = FakeApp(store)

    await notifications.poll_schedules_once(app)
    assert app.bot.messages == []
    assert await store.all_tracked() == []