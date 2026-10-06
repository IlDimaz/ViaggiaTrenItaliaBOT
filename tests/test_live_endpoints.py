"""Smoke tests against the live, free Trenitalia endpoints.

Run with:  pytest -m live -s
They are marked ``live`` and require network access.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

import trenitalia.lefrecce as lf
import trenitalia.viaggiatreno as vt


def test_js_date_string_format():
    from zoneinfo import ZoneInfo

    rome = ZoneInfo("Europe/Rome")
    when = datetime(2026, 10, 3, 14, 30, 0, tzinfo=rome)
    # 2026-10-03 is a Saturday; CEST is +0200.
    assert vt.js_date_string(when) == "Sat Oct 03 2026 14:30:00 GMT+0200"


def test_midnight_ms_is_rome_midnight():
    from zoneinfo import ZoneInfo

    rome = ZoneInfo("Europe/Rome")
    ms = vt.midnight_ms(datetime(2026, 10, 3).date())
    dt = datetime.fromtimestamp(ms / 1000, tz=rome)
    assert (dt.hour, dt.minute, dt.second) == (0, 0, 0)


@pytest.mark.live
@pytest.mark.asyncio
async def test_station_search_live():
    stations = await lf.search_stations("SUZZARA")
    names = [s.display_name.upper() for s in stations]
    assert any("SUZZARA" in n for n in names)


@pytest.mark.live
@pytest.mark.asyncio
async def test_viaggiatreno_autocomplete_live():
    stations = await vt.autocomplete_stations("SUZZARA")
    assert any(s.code.startswith("S") for s in stations)


@pytest.mark.live
@pytest.mark.asyncio
async def test_find_train_live():
    refs = await vt.find_train(22470)
    assert refs
    assert refs[0].origin_code
    assert refs[0].midnight_ms > 0


@pytest.mark.live
@pytest.mark.asyncio
async def test_train_status_live():
    refs = await vt.find_train(22470)
    if not refs:
        pytest.skip("train 22470 not running today")
    status = await vt.train_status(refs[0])
    if status is None:
        pytest.skip("no live data (HTTP 204)")
    assert status.number == 22470
    assert status.origin


@pytest.mark.live
@pytest.mark.asyncio
async def test_partenze_live():
    station = await vt.resolve_station("SUZZARA")
    assert station is not None
    rows = await vt.partenze(station.code, datetime.now(vt.ROME))
    # A small station can legitimately have no departures; just assert the shape.
    assert isinstance(rows, list)


@pytest.mark.live
@pytest.mark.asyncio
async def test_ticker_live():
    line = await vt.ticker()
    assert isinstance(line, str)


@pytest.mark.live
@pytest.mark.asyncio
async def test_solutions_live_with_price():
    origin = await lf.search_stations("SUZZARA")
    dest = await lf.search_stations("MANTOVA")
    assert origin and dest
    payload = await lf.solutions(
        origin[0].id, dest[0].id, datetime.now(vt.ROME) + timedelta(hours=1), limit=5
    )
    sols = lf.parse_solutions(payload)
    assert sols
    assert any(s.train_numbers for s in sols)
