# 🚆 Trenitalia Telegram Bot

A Telegram bot that, given a departure station, lists the next trains to the
various destinations, shows real-time status (delays, last detected station,
disruptions) for a chosen train to make tracking delays easier, optionally shows prices, and proactively
forwards Trenitalia infomobilità announcements (including everyone's favorites: **scioperi**).
With this bot looking for your train stop to be an active chore: you set your go-to train, you track it and you'll have updates on it daily!
I'm currently self hosting it on my raspberrypi and you can find it on @viaggiatrenitaliabot on telegram, if it should be down or i stop mantaining it...well, the repo is open source for a reason.

> **lefrecce.it** (trips + prices + station search) and **ViaggiaTreno**
> (real-time status, departures board, infomobilità). Neither requires an API
> key, a token, a quota or any paid "credits". Politeness is handled with
> short-lived caching.

## Features

- **Station → departures board**: send `Bologna` and get the next departures
  towards the different destinations, each with time, category, train number,
  platform and delay. `⬅️ Precedenti` / `Successivi ➡️` buttons page the board.
- **Train status card**: send a train number (`22470`) *or* tap a departure,
  and get category, route, scheduled vs actual times, **delay**, **last
  detected station**, next stop, and any cancellation/rerouting flags.
- **Trip by origin+destination+time**: send `MANTOVA MODENA 16.29`.
- **Prices toggle** (`/prezzi`): adds the price to the trip card(for the whole end to end trip).
- **Proactive infomobilità** (`/avvisi` on/off): a background poller broadcasts
  new circulation alerts / strikes to subscribed chats.
- **Live train tracking** (`/segui <numero>`): the bot polls the train and pushes
  you a message whenever something *changes* (delay, last detected station,
  next stop), then auto-stops when the train arrives or is cancelled.
- **Weekly schedule** (`/schedule <numero> <giorni>`): set the trains you take on
  given weekdays (e.g. `/schedule 16986 lun ven`); the bot starts reporting each
  of them about 30 min before departure and keeps you posted until arrival.

## Commands

| Command | Description |
|---|---|
| `/start` | welcome |
| `/partenze [stazione]` | departures board (uses your favourite station) |
| `/treno <numero>` | real-time status of a train |
| `/stazione <nome>` | set your favourite station |
| `/prezzi` | toggle prices on the trip card |
| `/avvisi` | toggle automatic announcements |
| `/avvisi_ora` | show infomobilità right now |
| `/schedule <numero> <giorni>` | weekly reminder before departure (e.g. `lun ven`) |
| `/unschedule <numero>` | remove a weekly reminder |
| `/segui <numero>` | follow a train with automatic updates |
| `/seguiti` | list the trains you follow |
| `/stop [numero]` | stop following |
| `/help` | help |


## How it maps to the APIs

| Feature | Endpoint |
|---|---|
| Station autocomplete (name → id) | `lefrecce GET /website/locations/search?name=…` |
| Departures board | `ViaggiaTreno GET /partenze/{CODE}/{JS-Date}` |
| Train number → run(s) | `ViaggiaTreno GET /cercaNumeroTrenoTrenoAutocomplete/{n}` |
| Real-time status | `ViaggiaTreno GET /andamentoTreno/{CODE}/{n}/{midnightMs}` |
| Trip + price | `lefrecce POST /website/ticket/solutions` |
| Announcements / sciopero | `ViaggiaTreno /infomobilitaTicker`, `/infomobilitaRSSBox/{true\|false}`, `/news/{reg}/{lang}` |

Huge thanks for the cleaned API to
Docs: <https://github.com/SimoDax/Trenitalia-API/wiki/Nuove-API-Trenitalia-lefrecce.it>

## Notes / caveats

- `/partenze` and `/arrivi` require a **JavaScript `Date.toString()`** string
  (e.g. `Sat Oct 03 2026 14:30:00 GMT+0200`), not an epoch timestamp.
- Train numbers are **not unique**; the bot disambiguates by origin.
- `andamentoTreno` can return **HTTP 204** for cancelled/rerouted trains.
- Prices need a specific destination, so they appear on trip cards.
- These are unofficial endpoints and may change; parsing is isolated and fails
  soft. Not affiliated with Trenitalia S.p.A.

## Troubleshooting

**`RuntimeError: There is no current event loop in thread 'MainThread'` (Python 3.14).**
python-telegram-bot 21 relies on an implicit event loop that Python 3.12+ no
longer creates. `bot/app.py` works around this with `_ensure_event_loop()`.
If you still hit it (or other 3.14 edge cases), build the venv on Python 3.13:

```powershell
py -3.13 -m venv .venv313
.venv313\Scripts\python.exe -m pip install -r requirements.txt
.venv313\Scripts\python.exe main.py
```

**`Copy-Item` / `Activate.ps1` 'not recognized' in cmd.exe.** Those are
PowerShell commands. In cmd use `copy .env.example .env`, or run the venv
directly: `.venv\Scripts\python.exe main.py`.
