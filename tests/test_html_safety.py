import re

from bot import cards, handlers

ALLOWED = {
    "b", "i", "u", "s", "code", "pre", "a",
    "strong", "em", "tg-spoiler", "blockquote",
}

TAG_RE = re.compile(r"<\s*/?\s*([a-zA-Z][a-zA-Z0-9-]*)")


def bad_tags(text):
    return [
        m.group(1).lower()
        for m in TAG_RE.finditer(text)
        if m.group(1).lower() not in ALLOWED
    ]


def test_help_text_has_no_raw_tags():
    assert bad_tags(handlers.HELP_TEXT) == []


def test_status_card_tags_allowed():
    from trenitalia.models import Stop, TrainStatus

    status = TrainStatus(
        number=1,
        category="REG",
        origin="A",
        destination="B",
        delay=3,
        last_detected_station="A",
        scheduled_departure=None,
        scheduled_arrival=None,
        stops=[Stop(station="A", scheduled=None, actual=None, delay=0, passed=True)],
        tipo_treno="PG",
        provvedimento=0,
    )
    assert bad_tags(cards.status_text(status)) == []


def test_departures_card_tags_allowed():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from trenitalia.models import Departure

    when = datetime(2026, 10, 3, 8, 0, tzinfo=ZoneInfo("Europe/Rome"))
    rows = [
        Departure(
            number=1,
            category="REG",
            destination="MANTOVA",
            scheduled=when,
            actual=when,
            delay=2,
            arrived=False,
            origin_code="S1",
            platform="3",
        )
    ]
    assert bad_tags(cards.departures_text("SUZZARA", rows, when)) == []


def test_alerts_card_tags_allowed():
    text = cards.alerts_text("CIRCOLAZIONE REGOLARE", ["INFOLAVORI"], [{"testo": "test"}])
    assert bad_tags(text) == []
