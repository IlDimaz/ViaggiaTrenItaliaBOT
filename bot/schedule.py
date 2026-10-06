"""Weekday parsing and formatting for the ``/schedule`` command.

Monday = 0 ... Sunday = 6, matching ``datetime.weekday()`` and
``cards.WEEKDAYS_IT``. Day tokens are accepted in Italian and English, as full
names or the usual abbreviations, plus a few range/shortcut forms.
"""
from __future__ import annotations

import re
import unicodedata

DAY_NAMES_IT = [
    "lunedì",
    "martedì",
    "mercoledì",
    "giovedì",
    "venerdì",
    "sabato",
    "domenica",
]

# Aliases are matched against an accent- and case-free form (see ``_norm``).
_ALIASES: dict[str, int] = {
    "lun": 0, "lunedi": 0,
    "mar": 1, "martedi": 1,
    "mer": 2, "merc": 2, "mercoledi": 2,
    "gio": 3, "giov": 3, "giovedi": 3,
    "ven": 4, "venerdi": 4,
    "sab": 5, "sabato": 5,
    "dom": 6, "domenica": 6,
    # English full names and abbreviations.
    "mon": 0, "monday": 0,
    "tue": 1, "tues": 1, "tuesday": 1,
    "wed": 2, "weds": 2, "wednesday": 2,
    "thu": 3, "thur": 3, "thurs": 3, "thursday": 3,
    "fri": 4, "friday": 4,
    "sat": 5, "saturday": 5,
    "sun": 6, "sunday": 6,
}

_GROUPS: dict[str, set[int]] = {
    "feriale": {0, 1, 2, 3, 4},
    "feriali": {0, 1, 2, 3, 4},
    "weekday": {0, 1, 2, 3, 4},
    "weekdays": {0, 1, 2, 3, 4},
    "weekend": {5, 6},
    "tutti": {0, 1, 2, 3, 4, 5, 6},
    "daily": {0, 1, 2, 3, 4, 5, 6},
    "everyday": {0, 1, 2, 3, 4, 5, 6},
}


def _norm(token: str) -> str:
    token = (token or "").strip().lower()
    decomposed = unicodedata.normalize("NFKD", token)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _expand_range(start: int, end: int) -> set[int]:
    if start <= end:
        return set(range(start, end + 1))
    # Wrap-around, e.g. "ven-lun" -> Fri, Sat, Sun, Mon.
    return set(range(start, 7)) | set(range(0, end + 1))


def parse_days(text: str) -> tuple[set[int], list[str]]:
    """Parse a free-text weekday list into ``(weekdays, unknown)``."""
    days: set[int] = set()
    unknown: list[str] = []
    for token in re.split(r"[,\s]+", (text or "").strip()):
        if not token:
            continue
        norm = _norm(token)
        if not norm:
            continue
        if norm in _GROUPS:
            days.update(_GROUPS[norm])
            continue
        if "-" in norm:
            left, _, right = norm.partition("-")
            a, b = _ALIASES.get(left), _ALIASES.get(right)
            if a is None or b is None:
                unknown.append(token)
            else:
                days.update(_expand_range(a, b))
            continue
        idx = _ALIASES.get(norm)
        if idx is None:
            unknown.append(token)
        else:
            days.add(idx)
    return days, unknown


def days_from_str(stored: str) -> set[int]:
    return {int(x) for x in (stored or "").split(",") if x.strip().isdigit()}


def format_days(days: "set[int] | str") -> str:
    """Return the canonical storage form, e.g. ``"0,4"``."""
    idxs = sorted(days_from_str(days) if isinstance(days, str) else days)
    return ",".join(str(i) for i in idxs)


def describe_days(days: "str | set[int]") -> str:
    """Human-readable Italian label, e.g. ``"lunedì, venerdì"``."""
    idxs = sorted(days_from_str(days) if isinstance(days, str) else days)
    return ", ".join(DAY_NAMES_IT[i] for i in idxs if 0 <= i <= 6)


__all__ = [
    "DAY_NAMES_IT",
    "parse_days",
    "days_from_str",
    "format_days",
    "describe_days",
]