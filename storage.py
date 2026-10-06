"""Per-chat settings persisted in SQLite (aiosqlite)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import aiosqlite

from config import DB_PATH

_SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_settings (
    chat_id   INTEGER PRIMARY KEY,
    alerts    INTEGER NOT NULL DEFAULT 1,
    prices    INTEGER NOT NULL DEFAULT 0,
    station   TEXT    NOT NULL DEFAULT '',
    station_code TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS seen_alerts (
    alert_id TEXT PRIMARY KEY,
    seen_at  INTEGER NOT NULL DEFAULT (strftime('%s','now'))
);
CREATE TABLE IF NOT EXISTS tracked_trains (
    chat_id     INTEGER NOT NULL,
    number      INTEGER NOT NULL,
    origin_code TEXT    NOT NULL DEFAULT '',
    midnight_ms INTEGER NOT NULL DEFAULT 0,
    last_sig    TEXT    NOT NULL DEFAULT '',
    PRIMARY KEY (chat_id, number, origin_code)
);
CREATE TABLE IF NOT EXISTS schedules (
    chat_id     INTEGER NOT NULL,
    number      INTEGER NOT NULL,
    days        TEXT    NOT NULL DEFAULT '',
    origin_code TEXT    NOT NULL DEFAULT '',
    last_date   TEXT    NOT NULL DEFAULT '',
    PRIMARY KEY (chat_id, number)
);
CREATE TABLE IF NOT EXISTS users (
    user_id    INTEGER PRIMARY KEY,
    username   TEXT    NOT NULL DEFAULT '',
    first_name TEXT    NOT NULL DEFAULT '',
    started_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
);
"""


@dataclass
class ChatSettings:
    chat_id: int
    alerts: bool = True
    prices: bool = False
    station: str = ""
    station_code: str = ""

    @classmethod
    def from_row(cls, row: aiosqlite.Row) -> "ChatSettings":
        return cls(
            chat_id=row["chat_id"],
            alerts=bool(row["alerts"]),
            prices=bool(row["prices"]),
            station=row["station"] or "",
            station_code=row["station_code"] or "",
        )


@dataclass
class TrackedTrain:
    chat_id: int
    number: int
    origin_code: str
    midnight_ms: int
    last_sig: str = ""

    @classmethod
    def from_row(cls, row: aiosqlite.Row) -> "TrackedTrain":
        return cls(
            chat_id=row["chat_id"],
            number=row["number"],
            origin_code=row["origin_code"],
            midnight_ms=row["midnight_ms"],
            last_sig=row["last_sig"] or "",
        )


@dataclass
class Schedule:
    chat_id: int
    number: int
    days: str
    origin_code: str = ""
    last_date: str = ""

    @classmethod
    def from_row(cls, row: aiosqlite.Row) -> "Schedule":
        return cls(
            chat_id=row["chat_id"],
            number=row["number"],
            days=row["days"] or "",
            origin_code=row["origin_code"] or "",
            last_date=row["last_date"] or "",
        )

    @property
    def weekday_set(self) -> set[int]:
        return {
            int(x) for x in (self.days or "").split(",") if x.strip().isdigit()
        }


class Storage:
    def __init__(self) -> None:
        self._db: Optional[aiosqlite.Connection] = None

    async def connect(self) -> None:
        self._db = await aiosqlite.connect(DB_PATH)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(_SCHEMA)
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        assert self._db is not None, "Storage.connect() not called"
        return self._db

    async def get(self, chat_id: int) -> ChatSettings:
        cur = await self.db.execute(
            "SELECT * FROM chat_settings WHERE chat_id = ?", (chat_id,)
        )
        row = await cur.fetchone()
        if row is None:
            await self.db.execute(
                "INSERT OR IGNORE INTO chat_settings (chat_id) VALUES (?)", (chat_id,)
            )
            await self.db.commit()
            return ChatSettings(chat_id=chat_id)
        return ChatSettings.from_row(row)

    async def _update(self, chat_id: int, column: str, value) -> ChatSettings:
        await self.get(chat_id)  # ensure the row exists
        await self.db.execute(
            f"UPDATE chat_settings SET {column} = ? WHERE chat_id = ?",
            (value, chat_id),
        )
        await self.db.commit()
        return await self.get(chat_id)

    async def set_alerts(self, chat_id: int, enabled: bool) -> ChatSettings:
        return await self._update(chat_id, "alerts", 1 if enabled else 0)

    async def set_prices(self, chat_id: int, enabled: bool) -> ChatSettings:
        return await self._update(chat_id, "prices", 1 if enabled else 0)

    async def set_station(self, chat_id: int, name: str, code: str) -> ChatSettings:
        await self.get(chat_id)
        await self.db.execute(
            "UPDATE chat_settings SET station = ?, station_code = ? WHERE chat_id = ?",
            (name, code, chat_id),
        )
        await self.db.commit()
        return await self.get(chat_id)

    async def chats_with_alerts(self) -> list[int]:
        cur = await self.db.execute(
            "SELECT chat_id FROM chat_settings WHERE alerts = 1"
        )
        return [r["chat_id"] for r in await cur.fetchall()]

    async def filter_unseen(self, alert_ids: list[str]) -> list[str]:
        """Return only ids not present in ``seen_alerts``."""
        unseen: list[str] = []
        for aid in alert_ids:
            cur = await self.db.execute(
                "SELECT 1 FROM seen_alerts WHERE alert_id = ?", (aid,)
            )
            if await cur.fetchone() is None:
                unseen.append(aid)
        return unseen

    async def mark_seen(self, alert_ids: list[str]) -> None:
        await self.db.executemany(
            "INSERT OR IGNORE INTO seen_alerts (alert_id) VALUES (?)",
            [(a,) for a in alert_ids],
        )
        await self.db.commit()


    async def record_user(
        self, user_id: int, username: str = "", first_name: str = ""
    ) -> bool:
        """Record a Telegram user on /start. Returns True if newly added."""
        cur = await self.db.execute(
            "INSERT OR IGNORE INTO users (user_id, username, first_name) "
            "VALUES (?, ?, ?)",
            (user_id, username, first_name),
        )
        await self.db.commit()
        return cur.rowcount == 1

    async def count_users(self) -> int:
        cur = await self.db.execute("SELECT COUNT(*) AS n FROM users")
        row = await cur.fetchone()
        return int(row["n"]) if row is not None else 0

    async def set_schedule(
        self, chat_id: int, number: int, days: str, origin_code: str = ""
    ) -> None:
        cur = await self.db.execute(
            "SELECT * FROM schedules WHERE chat_id = ? AND number = ?",
            (chat_id, number),
        )
        row = await cur.fetchone()
        if row is None:
            await self.db.execute(
                "INSERT INTO schedules (chat_id, number, days, origin_code) "
                "VALUES (?, ?, ?, ?)",
                (chat_id, number, days, origin_code),
            )
        else:
            keep_origin = origin_code or row["origin_code"]
            await self.db.execute(
                "UPDATE schedules SET days = ?, origin_code = ? "
                "WHERE chat_id = ? AND number = ?",
                (days, keep_origin, chat_id, number),
            )
        await self.db.commit()

    async def remove_schedule(self, chat_id: int, number: int) -> int:
        cur = await self.db.execute(
            "DELETE FROM schedules WHERE chat_id = ? AND number = ?",
            (chat_id, number),
        )
        await self.db.commit()
        return cur.rowcount

    async def list_schedules(self, chat_id: int):
        cur = await self.db.execute(
            "SELECT * FROM schedules WHERE chat_id = ? ORDER BY number",
            (chat_id,),
        )
        return [Schedule.from_row(r) for r in await cur.fetchall()]

    async def all_schedules(self):
        cur = await self.db.execute("SELECT * FROM schedules")
        return [Schedule.from_row(r) for r in await cur.fetchall()]

    async def set_schedule_origin(
        self, chat_id: int, number: int, origin_code: str
    ) -> None:
        await self.db.execute(
            "UPDATE schedules SET origin_code = ? WHERE chat_id = ? AND number = ?",
            (origin_code, chat_id, number),
        )
        await self.db.commit()

    async def set_schedule_last_date(
        self, chat_id: int, number: int, last_date: str
    ) -> None:
        await self.db.execute(
            "UPDATE schedules SET last_date = ? WHERE chat_id = ? AND number = ?",
            (last_date, chat_id, number),
        )
        await self.db.commit()

    async def add_tracked(
        self, chat_id: int, number: int, origin_code: str, midnight_ms: int
    ) -> bool:
        cur = await self.db.execute(
            "SELECT 1 FROM tracked_trains WHERE chat_id = ? AND number = ? AND origin_code = ?",
            (chat_id, number, origin_code),
        )
        existed = await cur.fetchone() is not None
        await self.db.execute(
            "INSERT OR IGNORE INTO tracked_trains "
            "(chat_id, number, origin_code, midnight_ms) VALUES (?, ?, ?, ?)",
            (chat_id, number, origin_code, midnight_ms),
        )
        await self.db.commit()
        return not existed

    async def remove_tracked(self, chat_id: int, number: int) -> int:
        cur = await self.db.execute(
            "DELETE FROM tracked_trains WHERE chat_id = ? AND number = ?",
            (chat_id, number),
        )
        await self.db.commit()
        return cur.rowcount

    async def remove_tracked_exact(
        self, chat_id: int, number: int, origin_code: str
    ) -> None:
        await self.db.execute(
            "DELETE FROM tracked_trains WHERE chat_id = ? AND number = ? AND origin_code = ?",
            (chat_id, number, origin_code),
        )
        await self.db.commit()

    async def list_tracked(self, chat_id: int):
        cur = await self.db.execute(
            "SELECT * FROM tracked_trains WHERE chat_id = ? ORDER BY number",
            (chat_id,),
        )
        return [TrackedTrain.from_row(r) for r in await cur.fetchall()]

    async def all_tracked(self):
        cur = await self.db.execute("SELECT * FROM tracked_trains")
        return [TrackedTrain.from_row(r) for r in await cur.fetchall()]

    async def set_tracked_sig(
        self, chat_id: int, number: int, origin_code: str, sig: str
    ) -> None:
        await self.db.execute(
            "UPDATE tracked_trains SET last_sig = ? "
            "WHERE chat_id = ? AND number = ? AND origin_code = ?",
            (sig, chat_id, number, origin_code),
        )
        await self.db.commit()

    async def set_tracked_midnight(
        self, chat_id: int, number: int, origin_code: str, midnight_ms: int
    ) -> None:
        await self.db.execute(
            "UPDATE tracked_trains SET midnight_ms = ? "
            "WHERE chat_id = ? AND number = ? AND origin_code = ?",
            (midnight_ms, chat_id, number, origin_code),
        )
        await self.db.commit()
