"""Telegram handlers: commands, free-text routing and button callbacks."""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

import trenitalia.lefrecce as lf
import trenitalia.viaggiatreno as vt
from config import (
    ADMIN_USER_IDS,
    DEPARTURES_WINDOW_MINUTES,
    ROME,
    SCHEDULE_LEAD_MINUTES,
    TRACK_SECONDS,
)
from . import cards, notifications, schedule

log = logging.getLogger(__name__)

HELP_TEXT = (
    "ℹ️ <b>Come si usa</b>\n\n"
    "• Scrivi il nome di una <b>stazione</b> (es. <code>SUZZARA</code>) per "
    "vedere le prossime partenze verso le varie destinazioni.\n"
    "• Tocca una partenza per i dettagli (ritardi, ultima stazione, problemi).\n"
    "• Scrivi un <b>numero di treno</b> (es. <code>22470</code>) per lo stato.\n"
    "• Scrivi <code>SUZZARA MANTOVA 8.55</code> per il dettaglio di quel viaggio.\n\n"
    "Comandi:\n"
    "/partenze [stazione] – tabellone partenze\n"
    "/treno &lt;numero&gt; – stato di un treno\n"
    "/stazione &lt;nome&gt; – imposta la stazione preferita\n"
    "/prezzi – attiva/disattiva i prezzi\n"
    "/avvisi – attiva/disattiva gli avvisi automatici\n"
    "/avvisi_ora – infomobilità adesso\n"
    "/schedule &lt;numero&gt; &lt;giorni&gt; – promemoria nei giorni scelti\n"
    "/unschedule &lt;numero&gt; – rimuovi un promemoria\n"
    "/segui &lt;numero&gt; - segui un treno (aggiornamenti automatici)\n"
    "/seguiti - elenco treni seguiti\n"
    "/stop [numero] - interrompi il tracciamento\n"
    "/help – questo messaggio"
)

_OD_RE = re.compile(
    r"^(?P<body>.+?)\s+(?P<h>\d{1,2})[.:](?P<m>\d{2})$"
)


def _storage(context: ContextTypes.DEFAULT_TYPE):
    return context.application.bot_data["storage"]


def _now() -> datetime:
    return datetime.now(ROME)


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is not None:
        await _storage(context).record_user(
            user.id, user.username or "", user.first_name or ""
        )
    await update.effective_message.reply_text(
        "👋 Ciao! Sono il tuo assistente Trenitalia.\n\n"
        "Scrivimi il nome di una stazione per vedere le prossime partenze, "
        "oppure un numero di treno per lo stato in tempo reale.\n\n" + HELP_TEXT,
        parse_mode=ParseMode.HTML,
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(HELP_TEXT, parse_mode=ParseMode.HTML)


def _is_admin(user) -> bool:
    return user is not None and user.id in ADMIN_USER_IDS


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update.effective_user):
        await update.effective_message.reply_text("Comando riservato.")
        return
    total = await _storage(context).count_users()
    await update.effective_message.reply_text(
        "👥 Utenti unici che hanno avviato il bot: " + str(total)
    )


async def cmd_prezzi(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    st = _storage(context)
    settings = await st.get(update.effective_chat.id)
    settings = await st.set_prices(update.effective_chat.id, not settings.prices)
    stato = "ATTIVI ✅" if settings.prices else "disattivati ❌"
    await update.effective_message.reply_text(f"Prezzi {stato}.")


async def cmd_avvisi(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    st = _storage(context)
    settings = await st.get(update.effective_chat.id)
    settings = await st.set_alerts(update.effective_chat.id, not settings.alerts)
    stato = "ATTIVI ✅" if settings.alerts else "disattivati ❌"
    await update.effective_message.reply_text(
        f"Avvisi automatici (scioperi, perturbazioni) {stato}."
    )


async def cmd_avvisi_ora(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _send_alerts(context, update.effective_chat.id)


async def cmd_stazione(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args or []
    if not args:
        await update.effective_message.reply_text(
            "Uso: /stazione &lt;nome&gt; (es. /stazione Suzzara)",
            parse_mode=ParseMode.HTML,
        )
        return
    await _handle_station_text(update, context, " ".join(args))


async def cmd_partenze(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args or []
    if args:
        await _handle_station_text(update, context, " ".join(args))
        return
    settings = await _storage(context).get(update.effective_chat.id)
    if settings.station_code:
        await _show_departures(
            update, context, settings.station_code, settings.station, _now()
        )
    else:
        await update.effective_message.reply_text(
            "Nessuna stazione preferita. Scrivimi il nome di una stazione o usa "
            "/stazione &lt;nome&gt;.",
            parse_mode=ParseMode.HTML,
        )


async def cmd_treno(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args or []
    if not args or not args[0].isdigit():
        await update.effective_message.reply_text("Uso: /treno <numero>")
        return
    await _handle_train_number(update, context, int(args[0]))

# --------------------------------------------------------------------------- #
# Free-text routing
# --------------------------------------------------------------------------- #
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (update.effective_message.text or "").strip()
    if not text:
        return
    if text.isdigit() and 1 <= len(text) <= 6:
        await _handle_train_number(update, context, int(text))
        return
    od = await _resolve_od(text)
    if od is not None:
        origin, dest, when = od
        await _handle_od(update, context, origin, dest, when)
        return
    await _handle_station_text(update, context, text)


async def _resolve_od(text: str):
    m = _OD_RE.match(text)
    if not m:
        return None
    hour, minute = int(m.group("h")), int(m.group("m"))
    if hour > 23 or minute > 59:
        return None
    body = m.group("body").strip()
    tokens = body.split()
    if len(tokens) < 2:
        return None
    for i in range(1, len(tokens)):
        orig_text = " ".join(tokens[:i])
        dest_text = " ".join(tokens[i:])
        if len(orig_text) < 2 or len(dest_text) < 2:
            continue
        origin = await _find_lefrecce_station(orig_text)
        if origin is None:
            continue
        dest = await _find_lefrecce_station(dest_text)
        if dest is None:
            continue
        when = _now().replace(hour=hour, minute=minute, second=0, microsecond=0)
        return origin, dest, when
    return None


async def _find_lefrecce_station(text: str):
    results = await lf.search_stations(text, limit=8)
    if not results:
        return None
    upper = text.strip().upper()
    for s in results:
        if s.display_name.upper() == upper or s.name.upper() == upper:
            return s
    for s in results:
        if s.display_name.upper().startswith(upper):
            return s
    return None


# --------------------------------------------------------------------------- #
# Station -> departures board
# --------------------------------------------------------------------------- #
async def _handle_station_text(
    update: Update, context: ContextTypes.DEFAULT_TYPE, text: str
) -> None:
    station = await vt.resolve_station(text)
    if station is None:
        await update.effective_message.reply_text(
            f"Non ho trovato la stazione “{text}”. Controlla il nome."
        )
        return
    await _storage(context).set_station(
        update.effective_chat.id, station.name, station.code
    )
    await _show_departures(update, context, station.code, station.name, _now())


async def _show_departures(update, context, station_code, station_name, when, edit=False):
    try:
        rows = await vt.partenze(station_code, when)
    except Exception as exc:  # noqa: BLE001
        log.warning("partenze failed: %s", exc)
        await update.effective_message.reply_text(
            "⚠️ Errore nel recupero delle partenze. Riprova tra poco."
        )
        return
    text = cards.departures_text(station_name, rows, when)
    kb = cards.departures_keyboard(
        station_code, rows, when, DEPARTURES_WINDOW_MINUTES
    )
    if edit and update.callback_query is not None:
        await update.callback_query.edit_message_text(
            text, reply_markup=kb, parse_mode=ParseMode.HTML
        )
    else:
        await update.effective_message.reply_text(
            text, reply_markup=kb, parse_mode=ParseMode.HTML
        )


# --------------------------------------------------------------------------- #
# Train number -> status
# --------------------------------------------------------------------------- #
async def _handle_train_number(
    update: Update, context: ContextTypes.DEFAULT_TYPE, number: int
) -> None:
    try:
        refs = await vt.find_train(number)
    except Exception as exc:  # noqa: BLE001
        log.warning("find_train failed: %s", exc)
        refs = []
    if not refs:
        await update.effective_message.reply_text(
            f"Nessun treno {number} circolante oggi trovato."
        )
        return
    if len(refs) > 1:
        buttons = [
            [
                InlineKeyboardButton(
                    f"{r.origin_name.title()} → ({r.number})",
                    callback_data=f"dep_{r.origin_code}_{r.number}",
                )
            ]
            for r in refs[:8]
        ]
        await update.effective_message.reply_text(
            f"Il numero {number} corrisponde a più corse. Quale?",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return
    await _reply_status(update, context, refs[0].origin_code, number)


async def _load_status(number: int, station_code: str):
    refs = await vt.find_train(number)
    if not refs:
        return None, None
    ref = next((r for r in refs if r.origin_code == station_code), refs[0])
    status = await vt.train_status(ref)
    return ref, status


async def _train_notes(number: int) -> list[str]:
    try:
        items = await vt.smartcaring(number)
    except Exception:  # noqa: BLE001
        return []
    notes: list[str] = []
    for it in items:
        text = it.get("testo") or it.get("descrizione") or it.get("titolo")
        if text:
            notes.append(str(text).strip()[:300])
    return notes[:3]


async def _find_price(origin: str, dest: str, number: int):
    if not origin or not dest:
        return None
    o = await _find_lefrecce_station(origin)
    d = await _find_lefrecce_station(dest)
    if o is None or d is None or o.id == d.id:
        return None
    try:
        payload = await lf.solutions(o.id, d.id, _now(), limit=10)
    except Exception as exc:  # noqa: BLE001
        log.warning("solutions failed: %s", exc)
        return None
    for sol in lf.parse_solutions(payload):
        if str(number) in sol.train_numbers:
            return sol
    return None


async def _reply_status(update, context, station_code, number, edit=False):
    try:
        _ref, status = await _load_status(number, station_code)
    except Exception as exc:  # noqa: BLE001
        log.warning("status failed: %s", exc)
        status = None
    if status is None:
        await update.effective_message.reply_text(
            f"⚠️ Nessun dato di circolazione per il treno {number} al momento."
        )
        return
    settings = await _storage(context).get(update.effective_chat.id)
    price_solution = None
    if settings.prices:
        price_solution = await _find_price(status.origin, status.destination, number)
    notes = await _train_notes(number)
    text = cards.status_text(status, price_solution, notes)
    kb = cards.status_keyboard(station_code, number)
    if edit and update.callback_query is not None:
        await update.callback_query.edit_message_text(
            text, reply_markup=kb, parse_mode=ParseMode.HTML
        )
    else:
        await update.effective_message.reply_text(
            text, reply_markup=kb, parse_mode=ParseMode.HTML
        )


async def _handle_od(update, context, origin_station, dest_station, when) -> None:
    try:
        payload = await lf.solutions(origin_station.id, dest_station.id, when, limit=10)
    except Exception as exc:  # noqa: BLE001
        log.warning("od solutions failed: %s", exc)
        await update.effective_message.reply_text("⚠️ Errore nella ricerca del viaggio.")
        return
    sols = lf.parse_solutions(payload)
    if not sols:
        await update.effective_message.reply_text(
            "Nessuna soluzione trovata per quel viaggio/orario."
        )
        return
    pick = sols[0]
    for sol in sols:
        if sol.departure and when <= sol.departure <= when + timedelta(hours=2):
            pick = sol
            break

    number = None
    if pick.train_numbers and pick.train_numbers[0].isdigit():
        number = int(pick.train_numbers[0])

    vt_station = await vt.resolve_station(origin_station.display_name)
    code = vt_station.code if vt_station else ""

    status = None
    if number is not None:
        _ref, status = await _load_status(number, code)
    if status is None:
        await update.effective_message.reply_text(
            f"🚆 {pick.train_numbers} {pick.origin} → {pick.destination} "
            f"{cards.time_str(pick.departure)} (nessun dato in tempo reale)."
        )
        return
    settings = await _storage(context).get(update.effective_chat.id)
    price_solution = pick if settings.prices else None
    notes = await _train_notes(number)
    await update.effective_message.reply_text(
        cards.status_text(status, price_solution, notes),
        reply_markup=cards.status_keyboard(code, number),
        parse_mode=ParseMode.HTML,
    )

async def _send_alerts(context, chat_id: int) -> None:
    ticker_line = await vt.ticker()
    titles = await vt.infomobilita_titles(False)
    news_items = await vt.news(0, "it")
    await context.bot.send_message(
        chat_id,
        cards.alerts_text(ticker_line, titles, news_items),
        parse_mode=ParseMode.HTML,
    )


async def cmd_segui(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args or []
    if not args or not args[0].isdigit():
        await update.effective_message.reply_text("Uso: /segui <numero>")
        return
    await _start_tracking(update, context, int(args[0]))


async def _start_tracking(update: Update, context: ContextTypes.DEFAULT_TYPE, number: int) -> None:
    try:
        refs = await vt.find_train(number)
    except Exception as exc:  # noqa: BLE001
        log.warning("find_train failed: %s", exc)
        refs = []
    if not refs:
        await update.effective_message.reply_text(
            "Nessun treno " + str(number) + " circolante oggi trovato."
        )
        return
    if len(refs) > 1:
        buttons = [
            [
                InlineKeyboardButton(
                    r.origin_name.title() + " (" + str(r.number) + ")",
                    callback_data="seg_" + r.origin_code + "_" + str(r.number),
                )
            ]
            for r in refs[:8]
        ]
        await update.effective_message.reply_text(
            "Il numero " + str(number) + " corrisponde a piu corse. Quale vuoi seguire?",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return
    await _confirm_tracking(update, context, refs[0])


async def _confirm_tracking(update: Update, context: ContextTypes.DEFAULT_TYPE, ref) -> None:
    storage = _storage(context)
    chat_id = update.effective_chat.id
    try:
        status = await vt.train_status(ref)
    except Exception as exc:  # noqa: BLE001
        log.warning("status failed: %s", exc)
        status = None
    if status is None:
        await update.effective_message.reply_text(
            "Nessun dato per il treno " + str(ref.number) + ": impossibile seguirlo ora."
        )
        return
    midnight = ref.midnight_ms or vt.midnight_ms()
    added = await storage.add_tracked(chat_id, ref.number, ref.origin_code, midnight)
    await storage.set_tracked_sig(
        chat_id, ref.number, ref.origin_code, notifications.status_signature(status)
    )
    prefix = "Ora seguo" if added else "Seguivo gia"
    header = (
        prefix
        + " il treno "
        + str(ref.number)
        + " (aggiornamenti ogni "
        + str(max(1, TRACK_SECONDS // 60))
        + " min). Ferma con /stop "
        + str(ref.number)
        + "."
    )
    await update.effective_message.reply_text(header)
    await update.effective_message.reply_text(
        cards.status_text(status), parse_mode=ParseMode.HTML
    )


async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args or []
    storage = _storage(context)
    chat_id = update.effective_chat.id
    if args and args[0].isdigit():
        number = int(args[0])
        count = await storage.remove_tracked(chat_id, number)
        if count:
            await update.effective_message.reply_text(
                "Tracciamento del treno " + str(number) + " fermato."
            )
        else:
            await update.effective_message.reply_text(
                "Non stavo seguendo il treno " + str(number) + "."
            )
        return
    items = await storage.list_tracked(chat_id)
    if not items:
        await update.effective_message.reply_text("Non stai seguendo nessun treno.")
        return
    for item in items:
        await storage.remove_tracked_exact(chat_id, item.number, item.origin_code)
    await update.effective_message.reply_text(
        "Tracciamento fermato per " + str(len(items)) + " treni."
    )


async def cmd_seguiti(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    items = await _storage(context).list_tracked(update.effective_chat.id)
    if not items:
        await update.effective_message.reply_text(
            "Non stai seguendo nessun treno. Usa /segui <numero>."
        )
        return
    lines = ["Treni seguiti:", ""]
    for item in items:
        lines.append("- " + str(item.number) + " (da " + (item.origin_code or "?") + ")")
    lines.append("")
    lines.append("Ferma con /stop <numero>.")
    await update.effective_message.reply_text(chr(10).join(lines))


async def cmd_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args or []
    if not args:
        await _list_schedules(update, context)
        return
    if not args[0].isdigit():
        await update.effective_message.reply_text(
            "Uso: /schedule <numero> <giorni> (es. /schedule 16986 lun ven)."
        )
        return
    number = int(args[0])
    days_text = " ".join(args[1:])
    if not days_text:
        await update.effective_message.reply_text(
            "Indica i giorni, es. /schedule " + str(number) + " lun ven."
        )
        return
    days, unknown = schedule.parse_days(days_text)
    if unknown:
        await update.effective_message.reply_text(
            "Giorni non riconosciuti: "
            + ", ".join(unknown)
            + ".\nEsempi: lun ven, monday friday, feriali, weekend, tutti."
        )
        return
    if not days:
        await update.effective_message.reply_text("Nessun giorno valido indicato.")
        return
    origin_code, route = await _schedule_route(number)
    await _storage(context).set_schedule(
        update.effective_chat.id, number, schedule.format_days(days), origin_code
    )
    where = " (" + route + ")" if route else ""
    await update.effective_message.reply_text(
        "✅ Promemoria attivato per il treno "
        + str(number)
        + where
        + ": "
        + schedule.describe_days(days)
        + ".\nTi avviserò circa "
        + str(SCHEDULE_LEAD_MINUTES)
        + " minuti prima della partenza."
    )


async def _schedule_route(number: int):
    """Best-effort (origin_code, human route) for a scheduled train."""
    try:
        refs = await vt.find_train(number)
    except Exception as exc:  # noqa: BLE001
        log.warning("schedule find_train failed: %s", exc)
        refs = []
    if not refs:
        return "", ""
    name = (refs[0].origin_name or "").strip()
    route = name.title() if name.isupper() else name
    return refs[0].origin_code, route


async def _list_schedules(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    items = await _storage(context).list_schedules(update.effective_chat.id)
    if not items:
        await update.effective_message.reply_text(
            "Nessun promemoria. Usa /schedule <numero> <giorni> "
            "(es. /schedule 16986 lun ven)."
        )
        return
    lines = ["⏰ <b>Promemoria settimanali</b>", ""]
    for item in items:
        lines.append(
            "• <b>"
            + str(item.number)
            + "</b> – "
            + schedule.describe_days(item.days)
        )
    lines.append("")
    lines.append("Rimuovi con /unschedule <numero>.")
    await update.effective_message.reply_text(
        "\n".join(lines), parse_mode=ParseMode.HTML
    )


async def cmd_unschedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args or []
    if not args or not args[0].isdigit():
        await update.effective_message.reply_text("Uso: /unschedule <numero>")
        return
    number = int(args[0])
    removed = await _storage(context).remove_schedule(
        update.effective_chat.id, number
    )
    if removed:
        await update.effective_message.reply_text(
            "Promemoria del treno " + str(number) + " rimosso."
        )
    else:
        await update.effective_message.reply_text(
            "Non avevi promemoria per il treno " + str(number) + "."
        )


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    try:
        if data.startswith("dep_"):
            _, code, num = data.split("_", 2)
            await _reply_status(update, context, code, int(num), edit=True)
        elif data.startswith("nav_"):
            _, code, epoch = data.split("_", 2)
            when = datetime.fromtimestamp(int(epoch) * 60, tz=ROME)
            settings = await _storage(context).get(update.effective_chat.id)
            name = settings.station if settings.station_code == code else code
            await _show_departures(update, context, code, name, when, edit=True)
        elif data.startswith("seg_"):
            _, code, num = data.split("_", 2)
            refs = await vt.find_train(int(num))
            ref = next(
                (r for r in refs if r.origin_code == code),
                refs[0] if refs else None,
            )
            if ref is None:
                await query.edit_message_text("Treno non piu disponibile.")
            else:
                await _confirm_tracking(update, context, ref)
    except Exception as exc:  # noqa: BLE001
        if "not modified" in str(exc).lower():
            return
        log.warning("callback failed: %s", exc)
        if query.message:
            await query.message.reply_text("⚠️ Errore nel gestire la richiesta.")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("Update %s caused error %s", update, context.error)


__all__ = [
    "cmd_start",
    "cmd_help",
    "cmd_prezzi",
    "cmd_avvisi",
    "cmd_avvisi_ora",
    "cmd_stazione",
    "cmd_partenze",
    "cmd_treno",
    "cmd_segui",
    "cmd_stop",
    "cmd_seguiti",
    "cmd_schedule",
    "cmd_unschedule",
    "cmd_stats",
    "text_handler",
    "callback_handler",
    "error_handler",
    "_send_alerts",
]



