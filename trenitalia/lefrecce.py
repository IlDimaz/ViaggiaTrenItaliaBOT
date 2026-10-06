"""Client for the free lefrecce.it BFF endpoints.

- ``GET  /Channels.Website.BFF.WEB/website/locations/search``  (station search)
- ``POST /Channels.Website.BFF.WEB/website/ticket/solutions``  (trips + prices)

No API key, no quota, no credits.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Dict, List, Optional
from urllib.parse import quote

import httpx

from cache import TTL_SOLUTIONS, TTL_STATIONS, cache
from config import HTTP_HEADERS, HTTP_TIMEOUT
from .models import Price, Solution, Station

BASE = "https://www.lefrecce.it/Channels.Website.BFF.WEB/website"

_clients: Dict[int, httpx.AsyncClient] = {}


def client() -> httpx.AsyncClient:
    """AsyncClient bound to the current running event loop (see viaggiatreno)."""
    loop = asyncio.get_running_loop()
    key = id(loop)
    existing = _clients.get(key)
    if existing is None or existing.is_closed:
        existing = httpx.AsyncClient(
            timeout=HTTP_TIMEOUT,
            headers={**HTTP_HEADERS, "Content-Type": "application/json"},
            follow_redirects=True,
        )
        _clients[key] = existing
    return existing


async def close() -> None:
    for c in list(_clients.values()):
        if not c.is_closed:
            await c.aclose()
    _clients.clear()


async def search_stations(name: str, limit: int = 10) -> List[Station]:
    """Autocomplete stations whose name contains ``name``."""

    async def _fetch() -> List[Station]:
        url = f"{BASE}/locations/search?name={quote(name)}&limit={limit}"
        resp = await client().get(url)
        resp.raise_for_status()
        data = resp.json()
        stations: List[Station] = []
        for row in data or []:
            stations.append(
                Station(
                    id=int(row["id"]),
                    name=row.get("name") or "",
                    display_name=row.get("displayName") or row.get("name") or "",
                    centroid_id=row.get("centroidId"),
                    multistation=bool(row.get("multistation")),
                )
            )
        return stations

    return await cache.get_or_set(
        f"lf:stations:{name.lower()}:{limit}", TTL_STATIONS, _fetch
    )


def _iso(when: datetime) -> str:
    """ISO-8601 with a colon in the offset, e.g. ``2026-10-03T17:00:00.000+02:00``."""
    base = when.strftime("%Y-%m-%dT%H:%M:%S.000")
    offset = when.utcoffset()
    total = int(offset.total_seconds()) if offset else 0
    sign = "+" if total >= 0 else "-"
    total = abs(total)
    hh, mm = divmod(total // 60, 60)
    return f"{base}{sign}{hh:02d}:{mm:02d}"


async def solutions(
    departure_id: int,
    arrival_id: int,
    departure_time: datetime,
    adults: int = 1,
    children: int = 0,
    limit: int = 10,
    offset: int = 0,
    order: str = "DEPARTURE_DATE",
) -> dict:
    """Trip search between two lefrecce location ids, including prices."""
    body = {
        "departureLocationId": departure_id,
        "arrivalLocationId": arrival_id,
        "departureTime": _iso(departure_time),
        "adults": adults,
        "children": children,
        "criteria": {
            "frecceOnly": False,
            "regionalOnly": False,
            "noChanges": False,
            "order": order,
            "limit": limit,
            "offset": offset,
        },
        "advancedSearchRequest": {"bestFare": False},
    }
    key = (
        f"lf:solutions:{departure_id}:{arrival_id}:{body['departureTime']}:"
        f"{adults}:{children}:{limit}:{offset}:{order}"
    )

    async def _fetch() -> dict:
        resp = await client().post(f"{BASE}/ticket/solutions", json=body)
        resp.raise_for_status()
        return resp.json()

    return await cache.get_or_set(key, TTL_SOLUTIONS, _fetch)


def _parse_dt(text: Optional[str]) -> Optional[datetime]:
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def parse_solutions(payload: dict) -> List[Solution]:
    """Turn a raw /solutions payload into :class:`Solution` objects."""
    out: List[Solution] = []
    for item in (payload or {}).get("solutions") or []:
        sol = item.get("solution") or {}
        trains = sol.get("trains") or []
        numbers = [str(t.get("name") or t.get("description") or "") for t in trains]
        category = trains[0].get("trainCategory", "") if trains else ""

        price = None
        raw_price = sol.get("price")
        if isinstance(raw_price, dict) and raw_price.get("amount") is not None:
            price = Price(
                currency=str(raw_price.get("currency") or "EUR"),
                amount=float(raw_price["amount"]),
            )

        out.append(
            Solution(
                origin=sol.get("origin") or "",
                destination=sol.get("destination") or "",
                departure=_parse_dt(sol.get("departureTime")),
                arrival=_parse_dt(sol.get("arrivalTime")),
                duration=sol.get("duration") or "",
                train_numbers=numbers,
                train_category=category,
                price=price,
                status=sol.get("status") or "",
            )
        )
    return out


__all__ = ["search_stations", "solutions", "parse_solutions", "close"]
