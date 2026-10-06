"""Background poller that broadcasts new Trenitalia announcements.

Every ``ALERTS_POLL_SECONDS`` it checks the infomobilità ticker, the
circulation-alert box and the network news, deduplicates them and forwards
anything new to chats that enabled alerts. The first pass only records the
current state (it does not broadcast) to avoid spamming on startup.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from telegram.constants import ParseMode
from telegram.ext import Application

import trenitalia.viaggiatreno as vt
from config import ALERTS_POLL_SECONDS, ROME, SCHEDULE_LEAD_MINUTES, TRACK_SECONDS
from . import cards

log = logging.getLogger(__name__)

REGULAR_TICKERS = {"CIRCOLAZIONE REGOLARE"}


def _ticker_alert(ticker_line: str) -> str:
    if ticker_line and ticker_line.strip().upper() not in REGULAR_TICKERS:
        return ticker_line.strip()
    return ""


def _norm_alert(text: str) -> str:
    """Collapse whitespace and case so the same announcement coming from the
    ticker and the infomobilità box is recognised as a single item."""
    return " ".join((text or "").split()).lower()


async def poll_once(application: Application, broadcast: bool) -> None:
    storage = application.bot_data["storage"]
    try:
        ticker_line = await vt.ticker()
        titles = await vt.infomobilita_titles(False)
        news_items = await vt.news(0, "it")
    except Exception as exc:  # noqa: BLE001
        log.warning("alerts poll failed: %s", exc)
        return

    # The ticker is usually a one-line copy of an infomobilità box entry, so key
    # both by their normalised content: an announcement is then broadcast once
    # regardless of whether it arrived via the ticker or the box.
    ticker_alert = _ticker_alert(ticker_line)
    ticker_key = f"alert:{_norm_alert(ticker_alert)}" if ticker_alert else ""
    title_keys = [f"alert:{_norm_alert(t)}" for t in titles]

    ids: list[str] = []
    if ticker_alert:
        ids.append(ticker_key)
    ids.extend(title_keys)
    for n in news_items:
        key = n.get("id") or n.get("titolo") or ""
        ids.append(f"news:{key}")

    if not ids:
        return

    unseen = set(await storage.filter_unseen(ids))
    await storage.mark_seen(ids)
    if not broadcast or not unseen:
        return

    # Titles win over the standalone ticker when they share content, so the
    # announcement is rendered once under "Avvisi di circolazione".
    new_titles = [t for t, k in zip(titles, title_keys) if k in unseen]
    ticker_is_title = bool(ticker_key) and ticker_key in title_keys
    new_ticker = (
        ticker_alert
        if ticker_key and ticker_key in unseen and not ticker_is_title
        else ""
    )
    new_news = [
        n
        for n in news_items
        if f"news:{n.get('id') or n.get('titolo') or ''}" in unseen
    ]
    if not (new_ticker or new_titles or new_news):
        return

    text = cards.alerts_text(new_ticker, new_titles, new_news)
    for chat_id in await storage.chats_with_alerts():
        try:
            await application.bot.send_message(
                chat_id, text, parse_mode=ParseMode.HTML
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("broadcast to %s failed: %s", chat_id, exc)


async def run_poller(application: Application) -> None:
    """Long-running task started via ``application.create_task``."""
    primed = False
    while True:
        try:
            await poll_once(application, broadcast=primed)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("poller iteration failed: %s", exc)
        primed = True
        await asyncio.sleep(ALERTS_POLL_SECONDS)


def status_signature(status) -> str:
    next_stop = ""
    for stop in status.stops:
        if not stop.passed:
            next_stop = stop.station
            break
    parts = [
        str(status.delay),
        status.last_detected_station or "",
        next_stop,
        status.tipo_treno,
        str(status.provvedimento),
        cards.time_str(status.actual_arrival or status.scheduled_arrival),
        "1" if status.suppressed else "0",
    ]
    return "|".join(parts)


def _arrived(status) -> bool:
    return bool(status.stops) and status.stops[-1].passed


async def _send_tracked(application, chat_id: int, status, header: str) -> None:
    text = header + chr(10) + cards.status_text(status)
    try:
        await application.bot.send_message(chat_id, text, parse_mode=ParseMode.HTML)
    except Exception as exc:  # noqa: BLE001
        log.debug("tracked send to %s failed: %s", chat_id, exc)


async def _notify(application, chat_id: int, message: str) -> None:
    try:
        await application.bot.send_message(chat_id, message, parse_mode=ParseMode.HTML)
    except Exception as exc:  # noqa: BLE001
        log.debug("notify to %s failed: %s", chat_id, exc)


async def poll_tracked_once(application) -> None:
    storage = application.bot_data["storage"]
    tracked = await storage.all_tracked()
    if not tracked:
        return
    midnight = vt.midnight_ms()
    for item in tracked:
        ref = vt.TrainRef(
            number=item.number,
            origin_code=item.origin_code or "",
            origin_name="",
            midnight_ms=midnight,
        )
        try:
            status = await vt.train_status(ref)
        except Exception as exc:  # noqa: BLE001
            log.warning("track %s failed: %s", item.number, exc)
            continue
        if item.midnight_ms != midnight:
            await storage.set_tracked_midnight(
                item.chat_id, item.number, item.origin_code, midnight
            )
        if status is None:
            if item.last_sig != "NODATA":
                await storage.set_tracked_sig(
                    item.chat_id, item.number, item.origin_code, "NODATA"
                )
                await _notify(
                    application,
                    item.chat_id,
                    "Nessun dato di circolazione per il treno "
                    + str(item.number)
                    + ": tracciamento terminato.",
                )
                await storage.remove_tracked_exact(
                    item.chat_id, item.number, item.origin_code
                )
            continue
        if status.is_cancelled:
            await _send_tracked(application, item.chat_id, status, "Treno cancellato")
            await storage.remove_tracked_exact(item.chat_id, item.number, item.origin_code)
            continue
        if _arrived(status):
            await _send_tracked(application, item.chat_id, status, "Arrivato a destinazione")
            await storage.remove_tracked_exact(item.chat_id, item.number, item.origin_code)
            continue
        sig = status_signature(status)
        if sig != item.last_sig:
            await storage.set_tracked_sig(
                item.chat_id, item.number, item.origin_code, sig
            )
            await _send_tracked(application, item.chat_id, status, "Aggiornamento")


async def run_tracker(application) -> None:
    while True:
        try:
            await poll_tracked_once(application)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("tracker iteration failed: %s", exc)
        await asyncio.sleep(TRACK_SECONDS)


async def poll_schedules_once(application) -> None:
    """Start tracking the trains scheduled for today, shortly before departure.

    A schedule fires once per matching weekday: about ``SCHEDULE_LEAD_MINUTES``
    before the train's scheduled departure we resolve the train, register it
    with the tracker and send an initial status. The regular tracker then keeps
    pushing updates until the train arrives (or is cancelled).
    """
    storage = application.bot_data["storage"]
    schedules = await storage.all_schedules()
    if not schedules:
        return
    now = datetime.now(ROME)
    weekday = now.weekday()
    today = now.date().isoformat()
    lead = timedelta(minutes=SCHEDULE_LEAD_MINUTES)
    midnight = vt.midnight_ms()
    for item in schedules:
        if weekday not in item.weekday_set or item.last_date == today:
            continue
        if item.origin_code:
            ref = vt.TrainRef(
                number=item.number,
                origin_code=item.origin_code,
                origin_name="",
                midnight_ms=midnight,
            )
        else:
            try:
                refs = await vt.find_train(item.number)
            except Exception as exc:  # noqa: BLE001
                log.warning("schedule find %s failed: %s", item.number, exc)
                continue
            if not refs:
                continue
            ref = refs[0]
            ref.midnight_ms = midnight
        try:
            status = await vt.train_status(ref)
        except Exception as exc:  # noqa: BLE001
            log.warning("schedule status %s failed: %s", item.number, exc)
            continue
        if status is None:
            continue
        departure = status.scheduled_departure
        if departure is None or now < departure - lead or _arrived(status):
            continue
        await storage.set_schedule_origin(item.chat_id, item.number, ref.origin_code)
        await storage.set_schedule_last_date(item.chat_id, item.number, today)
        await storage.add_tracked(
            item.chat_id, item.number, ref.origin_code, midnight
        )
        await storage.set_tracked_sig(
            item.chat_id, item.number, ref.origin_code, status_signature(status)
        )
        await _send_tracked(application, item.chat_id, status, "Promemoria viaggio")


async def run_scheduler(application) -> None:
    while True:
        try:
            await poll_schedules_once(application)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("scheduler iteration failed: %s", exc)
        await asyncio.sleep(TRACK_SECONDS)


__all__ = [
    "poll_once",
    "run_poller",
    "poll_tracked_once",
    "run_tracker",
    "poll_schedules_once",
    "run_scheduler",
    "status_signature",
]
