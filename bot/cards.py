"""Render Telegram messages and inline keyboards (HTML parse mode)."""
from __future__ import annotations

from datetime import datetime
from html import escape
from typing import List, Optional, Sequence

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from trenitalia.models import Departure, Solution, TrainStatus

WEEKDAYS_IT = ["lun", "mar", "mer", "gio", "ven", "sab", "dom"]


def time_str(dt: Optional[datetime]) -> str:
    return dt.strftime("%H:%M") if dt else "--:--"


def day_str(dt: Optional[datetime]) -> str:
    if not dt:
        return ""
    return f"{WEEKDAYS_IT[dt.weekday()]} {dt.day:02d}/{dt.month:02d}"


def delay_str(minutes: int) -> str:
    if minutes > 0:
        return f"🔴 +{minutes} min"
    if minutes < 0:
        return f"🟢 {-minutes} min in anticipo"
    return "🟢 in orario"


def _dep_button_label(dep: Departure) -> str:
    t = time_str(dep.effective_time)
    dest = dep.destination.title() if dep.destination.isupper() else dep.destination
    label = f"{t} → {dest} · {dep.number}"
    if dep.delay > 0:
        label += f" (+{dep.delay})"
    return label[:64]


def departures_text(
    origin_name: str, rows: Sequence[Departure], when: datetime
) -> str:
    title = origin_name.title() if origin_name.isupper() else origin_name
    lines = [
        f"🚉 <b>Partenze da {escape(title)}</b>",
        f"<i>{day_str(when)} · dalle {time_str(when)}</i>",
        "",
    ]
    if not rows:
        lines.append("Nessuna partenza nell'intervallo richiesto.")
    else:
        for d in rows:
            dest = d.destination.title() if d.destination.isupper() else d.destination
            extra = ""
            if d.delay > 0:
                extra = f"  ⚠️ <b>+{d.delay}</b>"
            elif d.delay < 0:
                extra = f"  ✓ {-d.delay}min prima"
            plat = f"  <i>bin {escape(str(d.platform))}</i>" if d.platform else ""
            lines.append(
                f"<b>{time_str(d.effective_time)}</b>  {escape(dest)} "
                f"— {escape(d.category)} {d.number}{extra}{plat}"
            )
    return "\n".join(lines)


def departures_keyboard(
    station_code: str, rows: Sequence[Departure], when: datetime, minutes: int
) -> InlineKeyboardMarkup:
    buttons: List[List[InlineKeyboardButton]] = []
    for d in rows[:8]:
        buttons.append(
            [
                InlineKeyboardButton(
                    _dep_button_label(d),
                    callback_data=f"dep_{station_code}_{d.number}",
                )
            ]
        )
    epoch_min = int(when.timestamp() // 60)
    nav = [
        InlineKeyboardButton(
            "⬅️ Precedenti", callback_data=f"nav_{station_code}_{epoch_min - minutes}"
        ),
        InlineKeyboardButton(
            "🔄", callback_data=f"nav_{station_code}_{epoch_min}"
        ),
        InlineKeyboardButton(
            "Successivi ➡️", callback_data=f"nav_{station_code}_{epoch_min + minutes}"
        ),
    ]
    buttons.append(nav)
    return InlineKeyboardMarkup(buttons)

def status_text(
    status: TrainStatus,
    price: Optional[Solution] = None,
    notes: Optional[Sequence[str]] = None,
) -> str:
    cat = status.category or status.tipo_treno
    route = (
        f"{escape(status.origin.title() if status.origin.isupper() else status.origin)}"
        f" → {escape(status.destination.title() if status.destination.isupper() else status.destination)}"
    )
    lines = [f"🚆 <b>{escape(cat)} {status.number}</b>", route, ""]

    if status.is_cancelled:
        lines.append("❌ <b>TRENO CANCELLATO</b>")
    elif status.suppressed:
        lines.append("⚠️ Nessun dato di circolazione disponibile al momento.")
    else:
        lines.append(f"Stato: {delay_str(status.delay)}")
        if status.last_detected_station:
            lines.append(
                "📍 Ultimo rilevamento: "
                f"<b>{escape(status.last_detected_station.title())}</b>"
            )
        if status.is_partially_cancelled:
            lines.append("⚠️ <b>Corse parzialmente cancellate / limitate</b>")
        if status.is_rerouted:
            lines.append("↩️ <b>Treno instradato su percorso alternativo</b>")

        lines.append("")
        dep = status.actual_departure or status.scheduled_departure
        arr = status.actual_arrival or status.scheduled_arrival
        lines.append(
            f"🕑 Partenza: {time_str(dep)} "
            f"(prevista {time_str(status.scheduled_departure)})"
        )
        if arr:
            lines.append(
                f"🏁 Arrivo: {time_str(arr)} "
                f"(previsto {time_str(status.scheduled_arrival)})"
            )
        nxt = _next_stop(status)
        if nxt:
            lines.append(f"➡️ Prossima fermata: <b>{escape(nxt.title())}</b>")

    if status.subtitle:
        lines.append("")
        lines.append(f"ℹ️ {escape(status.subtitle)}")

    for note in notes or []:
        lines.append(f"📣 {escape(note)}")

    if price and price.price:
        cur = price.price.currency
        cur = "€" if cur in ("€", "EUR", "â‚¬") else cur
        lines.append("")
        lines.append(
            f"💶 <b>Prezzo</b>: {price.price.amount:.2f} {escape(cur)}"
            f"  ({escape(price.train_category or '')})"
        )
    return "\n".join(lines)


def _next_stop(status: TrainStatus) -> Optional[str]:
    for stop in status.stops:
        if not stop.passed:
            return stop.station
    return None


def status_keyboard(station_code: str, train_number: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🔄 Aggiorna",
                    callback_data=f"dep_{station_code or 'x'}_{train_number}",
                )
            ]
        ]
    )


def alerts_text(ticker_line: str, titles: Sequence[str], news: Sequence[dict]) -> str:
    lines = ["📢 <b>Infomobilità Trenitalia</b>", ""]
    # The ticker is usually a one-line copy of a box entry: don't print it again
    # when the exact same announcement is already listed under the titles.
    def _norm(text: str) -> str:
        return " ".join((text or "").split()).lower()

    title_norms = {_norm(t) for t in titles}
    if ticker_line and _norm(ticker_line) not in title_norms:
        lines.append(f"<b>{escape(ticker_line)}</b>")
    if titles:
        lines.append("")
        lines.append("<i>Avvisi di circolazione:</i>")
        for t in titles[:10]:
            lines.append(f"• {escape(t)}")
    for item in news[:5]:
        testo = (item.get("testo") or "").strip()
        if testo:
            lines.append("")
            lines.append(f"📰 {escape(testo[:400])}")
    if len(lines) == 2:
        lines.append("Nessun avviso al momento.")
    return "\n".join(lines)

