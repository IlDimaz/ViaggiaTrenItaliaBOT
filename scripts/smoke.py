"""Manual end-to-end smoke test (no Telegram needed).

Usage:  .venv/Scripts/python.exe scripts/smoke.py [STATION] [TRAIN] [ORIGIN] [DEST]
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import trenitalia.lefrecce as lf  # noqa: E402
import trenitalia.viaggiatreno as vt  # noqa: E402
from bot import cards  # noqa: E402


async def main() -> None:
    station_name = sys.argv[1] if len(sys.argv) > 1 else "SUZZARA"
    train_number = int(sys.argv[2]) if len(sys.argv) > 2 else 22470
    origin = sys.argv[3] if len(sys.argv) > 3 else "SUZZARA"
    dest = sys.argv[4] if len(sys.argv) > 4 else "MANTOVA"

    print("\n=== 1) Station auto-complete (ViaggiaTreno) ===")
    station = await vt.resolve_station(station_name)
    print(station)

    if station:
        print("\n=== 2) Departures board ===")
        rows = await vt.partenze(station.code, datetime.now(vt.ROME))
        print(cards.departures_text(station.name, rows, datetime.now(vt.ROME)))
        print("(rows:", len(rows), ")")

    print("\n=== 3) Train number -> real-time status ===")
    refs = await vt.find_train(train_number)
    print("refs:", refs)
    if refs:
        status = await vt.train_status(refs[0])
        if status:
            print(cards.status_text(status))
        else:
            print("no live data (HTTP 204)")

    print("\n=== 4) Trip + price (lefrecce solutions) ===")
    o = await lf.search_stations(origin)
    d = await lf.search_stations(dest)
    print("origin id:", o[0].id if o else None, "| dest id:", d[0].id if d else None)
    if o and d:
        payload = await lf.solutions(
            o[0].id, d[0].id, datetime.now(vt.ROME) + timedelta(minutes=30), limit=5
        )
        for sol in lf.parse_solutions(payload)[:3]:
            price = f"{sol.price.amount:.2f}" if sol.price else "-"
            print(
                f"  {cards.time_str(sol.departure)} {sol.train_numbers} "
                f"{sol.origin}->{sol.destination} {price}"
            )

    print("\n=== 5) Infomobilità ===")
    ticker_line = await vt.ticker()
    titles = await vt.infomobilita_titles(False)
    news_items = await vt.news(0, "it")
    print(cards.alerts_text(ticker_line, titles, news_items)[:600])

    await vt.close()
    await lf.close()


if __name__ == "__main__":
    asyncio.run(main())
