"""Dataclasses describing the data we consume from the two public APIs."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional


@dataclass
class Station:
    """A station as returned by lefrecce.it ``locations/search``."""

    id: int
    name: str
    display_name: str
    centroid_id: Optional[int] = None
    multistation: bool = False


@dataclass
class VTStation:
    """A station as returned by ViaggiaTreno ``cercaStazione``."""

    code: str          # e.g. "S02340"
    name: str          # long name, e.g. "SUZZARA"
    short_name: str = ""


@dataclass
class TrainRef:
    """The (data, origin-station, train-number) triple that unambiguously
    identifies a train run in ViaggiaTreno."""

    number: int
    origin_code: str
    origin_name: str
    midnight_ms: int


@dataclass
class Departure:
    """One row of the departures board."""

    number: int
    category: str
    destination: str
    scheduled: Optional[datetime]
    actual: Optional[datetime]
    delay: int
    arrived: bool
    origin_code: str
    origin_name: str = ""
    platform: Optional[str] = None

    @property
    def effective_time(self) -> Optional[datetime]:
        return self.actual or self.scheduled


@dataclass
class Stop:
    """A single stop of a train."""

    station: str
    scheduled: Optional[datetime]
    actual: Optional[datetime]
    delay: int
    passed: bool
    fermata_type: str = ""


@dataclass
class TrainStatus:
    """Real-time status of a train run (from ``andamentoTreno``)."""

    number: int
    category: str
    origin: str
    destination: str
    delay: int
    last_detected_station: Optional[str]
    scheduled_departure: Optional[datetime]
    scheduled_arrival: Optional[datetime]
    actual_departure: Optional[datetime] = None
    actual_arrival: Optional[datetime] = None
    tipo_treno: str = "PG"          # PG normal, ST cancelled, PP/SI/SF partial, DV rerouted
    provvedimento: int = 0
    subtitle: Optional[str] = None
    stops: List[Stop] = field(default_factory=list)
    suppressed: bool = False

    @property
    def is_cancelled(self) -> bool:
        return self.tipo_treno == "ST" or (
            self.provvedimento in (1,) and not self.stops
        )

    @property
    def is_partially_cancelled(self) -> bool:
        return self.tipo_treno in ("PP", "SI", "SF") or self.provvedimento == 2

    @property
    def is_rerouted(self) -> bool:
        return self.tipo_treno == "DV" or self.provvedimento == 3


@dataclass
class Price:
    currency: str
    amount: float


@dataclass
class Solution:
    """A trip option returned by lefrecce.it ``ticket/solutions``."""

    origin: str
    destination: str
    departure: Optional[datetime]
    arrival: Optional[datetime]
    duration: str
    train_numbers: List[str]
    train_category: str
    price: Optional[Price] = None
    status: str = ""
