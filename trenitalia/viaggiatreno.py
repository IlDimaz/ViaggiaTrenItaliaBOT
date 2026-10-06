"""Client for the free ViaggiaTreno REST endpoints.

Base: https://www.viaggiatreno.it/infomobilita/resteasy/viaggiatreno/

These endpoints need no API key and are not metered/credited. All responses
are cached via :mod:`cache` to keep upstream load low.
"""
from __future__ import annotations

import asyncio
import re
from datetime import date as _date
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from urllib.parse import quote

import httpx

from cache import TTL_ALERTS, TTL_DEPARTURES, TTL_REGION, TTL_STATIONS, TTL_STATUS, cache
from config import HTTP_HEADERS, HTTP_TIMEOUT, ROME
from .models import Departure, Stop, TrainRef, TrainStatus, VTStation

BASE = "https://www.viaggiatreno.it/infomobilita/resteasy/viaggiatreno"
BASE_MOBILE = "https://www.viaggiatreno.it/infomobilitamobile/resteasy/viaggiatreno"

_clients: Dict[int, httpx.AsyncClient] = {}


def client() -> httpx.AsyncClient:
    """Return an AsyncClient bound to the current running event loop.

    Keying by loop keeps a single pooled client in production (one loop) while
    staying correct under test runners that create a fresh loop per test.
    """
    loop = asyncio.get_running_loop()
    key = id(loop)
    existing = _clients.get(key)
    if existing is None or existing.is_closed:
        existing = httpx.AsyncClient(
            timeout=HTTP_TIMEOUT,
            headers=dict(HTTP_HEADERS),
            follow_redirects=True,
        )
        _clients[key] = existing
    return existing


async def close() -> None:
    for c in list(_clients.values()):
        if not c.is_closed:
            await c.aclose()
    _clients.clear()


# English day/month names: the departures endpoint MUST receive English
# abbreviations regardless of the server locale, so we build the string by hand.
_EN_WDAY = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_EN_MON = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]


def js_date_string(when: datetime) -> str:
    """Reproduce JavaScript's ``Date.toString()`` output, e.g.
    ``Sat Oct 03 2026 14:30:00 GMT+0200``. Required by /partenze and /arrivi."""
    offset = when.utcoffset() or timedelta(0)
    total = int(offset.total_seconds())
    sign = "+" if total >= 0 else "-"
    total = abs(total)
    hh, mm = divmod(total // 60, 60)
    return (
        f"{_EN_WDAY[when.weekday()]} {_EN_MON[when.month - 1]} "
        f"{when.day:02d} {when.year} {when.hour:02d}:{when.minute:02d}:"
        f"{when.second:02d} GMT{sign}{hh:02d}{mm:02d}"
    )


def midnight_ms(when: Optional[_date] = None) -> int:
    """Epoch-ms of midnight (Europe/Rome) for the given day (default today)."""
    when = when or datetime.now(ROME).date()
    dt = datetime(when.year, when.month, when.day, tzinfo=ROME)
    return int(dt.timestamp() * 1000)


def _ms_to_dt(ms: Optional[int]) -> Optional[datetime]:
    if not ms:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=ROME)


def _parse_board_time(date_str: Optional[str], hhmm: Optional[str]) -> Optional[datetime]:
    """Fallback for board rows whose epoch field is null but which still carry
    a ``YYYY-MM-DD`` date and an ``HH:MM`` time string."""
    if not date_str or not hhmm:
        return None
    try:
        d = datetime.strptime(date_str[:10], "%Y-%m-%d").date()
        h, m = hhmm.split(":")[:2]
        return datetime(d.year, d.month, d.day, int(h), int(m), tzinfo=ROME)
    except (ValueError, AttributeError):
        return None


async def _get_text(url: str) -> str:
    resp = await client().get(url)
    if resp.status_code == 204:
        return ""
    resp.raise_for_status()
    return resp.text


async def _get_json(url: str):
    resp = await client().get(url)
    if resp.status_code in (204, 404):
        return None
    resp.raise_for_status()
    text = resp.text.strip()
    if not text or text == "Error":
        return None
    try:
        return resp.json()
    except Exception:
        return None

# --------------------------------------------------------------------------- #
# Station search
# --------------------------------------------------------------------------- #
async def autocomplete_stations(text: str) -> List[VTStation]:
    """``autocompletaStazione`` -> list of (NAME, CODE)."""

    async def _fetch() -> List[VTStation]:
        raw = await _get_text(f"{BASE}/autocompletaStazione/{quote(text)}")
        out: List[VTStation] = []
        for line in raw.splitlines():
            line = line.strip()
            if "|" not in line:
                continue
            name, code = line.rsplit("|", 1)
            out.append(VTStation(code=code.strip(), name=name.strip()))
        return out

    return await cache.get_or_set(f"vt:auto:{text.lower()}", TTL_STATIONS, _fetch)


async def cerca_stazione(text: str) -> list:
    """``cercaStazione`` -> richer JSON list (includes coordinates)."""
    data = await cache.get_or_set(
        f"vt:cerca:{text.lower()}",
        TTL_STATIONS,
        lambda: _get_json(f"{BASE}/cercaStazione/{quote(text)}"),
    )
    return data or []


async def resolve_station(text: str) -> Optional[VTStation]:
    """Best-effort exact-ish match of a free-text station name. Prefers an
    exact name match, otherwise the first autocomplete suggestion."""
    upper = text.strip().upper()
    candidates = await cerca_stazione(text)
    for c in candidates:
        name = (c.get("nomeLungo") or c.get("nomeBreve") or "").upper()
        if name == upper:
            code = c.get("codiceStazione") or c.get("codStazione") or c.get("id")
            if code:
                return VTStation(code=code, name=name)
    auto = await autocomplete_stations(text)
    if auto:
        exact = next((s for s in auto if s.name.upper() == upper), None)
        return exact or auto[0]
    return None


async def region(station_code: str) -> int:
    async def _fetch() -> int:
        raw = await _get_text(f"{BASE}/regione/{quote(station_code)}")
        try:
            return int(raw.strip())
        except ValueError:
            return 0

    return await cache.get_or_set(f"vt:region:{station_code}", TTL_REGION, _fetch)

# --------------------------------------------------------------------------- #
# Train identification
# --------------------------------------------------------------------------- #
_TRAIN_REF_RE = re.compile(
    r"^(\d+)\s*-\s*(.*?)\s*-\s*\d{2}/\d{2}/\d{2}\|(\d+)-([A-Z0-9]+)-(\d+)$"
)


def _parse_train_ref_line(line: str) -> Optional[TrainRef]:
    m = _TRAIN_REF_RE.match(line.strip())
    if not m:
        return None
    _num, name, num2, code, ts = m.groups()
    return TrainRef(
        number=int(num2),
        origin_code=code,
        origin_name=name.strip(),
        midnight_ms=int(ts),
    )


async def find_train(number: int) -> List[TrainRef]:
    """``cercaNumeroTrenoTrenoAutocomplete`` -> one or more candidate runs."""

    async def _fetch() -> List[TrainRef]:
        raw = await _get_text(f"{BASE}/cercaNumeroTrenoTrenoAutocomplete/{number}")
        refs: List[TrainRef] = []
        for line in raw.splitlines():
            ref = _parse_train_ref_line(line)
            if ref:
                refs.append(ref)
        return refs

    return await cache.get_or_set(f"vt:find:{number}", TTL_STATIONS, _fetch)


# --------------------------------------------------------------------------- #
# Departures / arrivals boards
# --------------------------------------------------------------------------- #
async def partenze(station_code: str, when: datetime) -> List[Departure]:
    """Departures board for a station around ``when`` (Europe/Rome)."""
    wstr = js_date_string(when)
    key = f"vt:partenze:{station_code}:{wstr}"

    async def _fetch() -> List[Departure]:
        url = f"{BASE}/partenze/{quote(station_code)}/{quote(wstr)}"
        data = await _get_json(url)
        if not isinstance(data, list):
            return []
        departures: List[Departure] = []
        for row in data:
            sched = _ms_to_dt(row.get("partenzaTreno")) or _parse_board_time(
                row.get("dataPartenzaTrenoAsDate"), row.get("compOrarioPartenza")
            )
            delay = int(row.get("ritardo") or 0)
            actual = sched + timedelta(minutes=delay) if sched else None
            departures.append(
                Departure(
                    number=int(row.get("numeroTreno") or 0),
                    category=(row.get("categoriaDescrizione") or row.get("categoria") or "").strip(),
                    destination=(row.get("destinazione") or "").strip(),
                    scheduled=sched,
                    actual=actual,
                    delay=delay,
                    arrived=bool(row.get("arrivato")),
                    origin_code=row.get("codOrigine") or station_code,
                    origin_name=(row.get("origine") or "").strip(),
                    platform=(
                        row.get("binarioEffettivoPartenzaDescrizione")
                        or row.get("binarioProgrammatoPartenzaDescrizione")
                    ),
                )
            )
        departures.sort(
            key=lambda d: (
                d.effective_time is None,
                d.effective_time or datetime.max.replace(tzinfo=ROME),
            )
        )
        return departures

    return await cache.get_or_set(key, TTL_DEPARTURES, _fetch)


async def arrivi(station_code: str, when: datetime) -> List[Departure]:
    """Arrivals board for a station around ``when``."""
    wstr = js_date_string(when)
    key = f"vt:arrivi:{station_code}:{wstr}"

    async def _fetch() -> List[Departure]:
        url = f"{BASE}/arrivi/{quote(station_code)}/{quote(wstr)}"
        data = await _get_json(url)
        if not isinstance(data, list):
            return []
        out: List[Departure] = []
        for row in data:
            sched = _ms_to_dt(row.get("arrivoTreno"))
            delay = int(row.get("ritardo") or 0)
            actual = sched + timedelta(minutes=delay) if sched else None
            out.append(
                Departure(
                    number=int(row.get("numeroTreno") or 0),
                    category=(row.get("categoriaDescrizione") or row.get("categoria") or "").strip(),
                    destination=(row.get("origine") or "").strip(),  # origin for arrivals
                    scheduled=sched,
                    actual=actual,
                    delay=delay,
                    arrived=bool(row.get("arrivato")),
                    origin_code=row.get("codOrigine") or "",
                    origin_name=(row.get("origine") or "").strip(),
                    platform=(
                        row.get("binarioEffettivoArrivoDescrizione")
                        or row.get("binarioProgrammatoArrivoDescrizione")
                    ),
                )
            )
        return out

    return await cache.get_or_set(key, TTL_DEPARTURES, _fetch)

# --------------------------------------------------------------------------- #
# Real-time train status
# --------------------------------------------------------------------------- #
async def train_status(ref: TrainRef) -> Optional[TrainStatus]:
    """``andamentoTreno`` -> real-time status. Returns None on HTTP 204
    (no live data / train cancelled)."""
    key = f"vt:status:{ref.origin_code}:{ref.number}:{ref.midnight_ms}"

    async def _fetch() -> Optional[TrainStatus]:
        url = f"{BASE}/andamentoTreno/{quote(ref.origin_code)}/{ref.number}/{ref.midnight_ms}"
        data = await _get_json(url)
        if not isinstance(data, dict):
            return None

        stops: List[Stop] = []
        for f in data.get("fermate") or []:
            stops.append(
                Stop(
                    station=f.get("stazione") or "",
                    scheduled=_ms_to_dt(f.get("programmata")),
                    actual=_ms_to_dt(f.get("effettiva")),
                    delay=int(f.get("ritardo") or 0),
                    passed=bool(f.get("effettiva")),
                    fermata_type=str(f.get("tipoFermata") or ""),
                )
            )

        sched_dep = stops[0].scheduled if stops else _ms_to_dt(data.get("orarioPartenza"))
        sched_arr = stops[-1].scheduled if stops else _ms_to_dt(data.get("orarioArrivo"))

        return TrainStatus(
            number=int(data.get("numeroTreno") or ref.number),
            category=(data.get("categoria") or "").strip(),
            origin=(data.get("origine") or ref.origin_name or "").strip(),
            destination=(data.get("destinazione") or "").strip(),
            delay=int(data.get("ritardo") or 0),
            last_detected_station=(data.get("stazioneUltimoRilevamento") or "").strip() or None,
            scheduled_departure=sched_dep,
            scheduled_arrival=sched_arr,
            actual_departure=stops[0].actual if stops else None,
            actual_arrival=stops[-1].actual if stops else None,
            tipo_treno=str(data.get("tipoTreno") or "PG"),
            provvedimento=int(data.get("provvedimento") or 0),
            subtitle=(data.get("subTitle") or "").strip() or None,
            stops=stops,
            suppressed=not bool(stops),
        )

    return await cache.get_or_set(key, TTL_STATUS, _fetch)


# --------------------------------------------------------------------------- #
# Infomobilita / news
# --------------------------------------------------------------------------- #
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    return _TAG_RE.sub("", text).replace("&nbsp;", " ").strip()


async def ticker() -> str:
    """Short one-line banner, e.g. ``CIRCOLAZIONE REGOLARE``."""
    async def _fetch() -> str:
        raw = await _get_text(f"{BASE}/infomobilitaTicker")
        items = re.findall(r"<li[^>]*>(.*?)</li>", raw, re.S | re.I)
        return " | ".join(t for t in (_strip_html(i) for i in items) if t)

    return await cache.get_or_set("vt:ticker", TTL_ALERTS, _fetch)


async def infomobilita_titles(is_lavori: bool = False) -> List[str]:
    """Titles from the Infotraffico box (circulation issues / planned works)."""
    flag = "true" if is_lavori else "false"

    async def _fetch() -> List[str]:
        raw = await _get_text(f"{BASE}/infomobilitaRSSBox/{flag}")
        # The Infotraffico box titles are the anchor texts.
        titles = [
            _strip_html(t)
            for t in re.findall(r"<a[^>]*>(.*?)</a>", raw, re.S | re.I)
        ]
        if not any(titles):
            ids = re.findall(r'id="([^"]+)"', raw)
            titles = [
                i
                for i in ids
                if not i.startswith("heading") and not i.startswith("accordion")
            ]
        seen, out = set(), []
        for t in titles:
            t = t.strip()
            if t and t not in seen:
                seen.add(t)
                out.append(t)
        return out

    return await cache.get_or_set(f"vt:infobox:{flag}", TTL_ALERTS, _fetch)


async def news(region_code: int = 0, lang: str = "it") -> List[dict]:
    """Regional/network news items (titles + text)."""

    async def _fetch() -> List[dict]:
        data = await _get_json(f"{BASE}/news/{region_code}/{lang}")
        return data if isinstance(data, list) else []

    return await cache.get_or_set(f"vt:news:{region_code}:{lang}", TTL_ALERTS, _fetch)


async def smartcaring(train_number: int) -> List[dict]:
    """Per-train notices (delay reasons etc.), when available."""
    async def _fetch() -> List[dict]:
        url = f"{BASE}/news/smartcaring?commercialTrainNumber={train_number}"
        data = await _get_json(url)
        return data if isinstance(data, list) else []

    return await cache.get_or_set(f"vt:smartcaring:{train_number}", TTL_ALERTS, _fetch)


__all__ = [
    "autocomplete_stations",
    "cerca_stazione",
    "resolve_station",
    "region",
    "find_train",
    "partenze",
    "arrivi",
    "train_status",
    "ticker",
    "infomobilita_titles",
    "news",
    "smartcaring",
    "js_date_string",
    "midnight_ms",
    "close",
]



