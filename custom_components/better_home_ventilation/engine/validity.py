"""Input validity and presence (BHV-18, docs/spec/02-control.md#input-validity).

An invalid value is excluded from every calculation; it is never read as 0.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
import math
from typing import Final

from .models import InputSummary, InputValue, Options, is_unavailable

#: Plausibility range of CO₂ values in ppm (inclusive).
CO2_RANGE: Final = (250.0, 10000.0)
#: Plausibility range of relative humidity in % (inclusive).
HUMIDITY_RANGE: Final = (0.0, 100.0)

_HOME_STATES: Final = frozenset({"home", "on"})


def parse_float(state: str | None) -> float | None:
    """Parse a state as a finite float; None for anything else."""
    if state is None or is_unavailable(state):
        return None
    try:
        value = float(state)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def numeric_value(
    value: InputValue | None,
    *,
    now: datetime,
    stale_timeout: timedelta,
    valid_range: tuple[float, float],
) -> float | None:
    """Return the valid numeric value of an input, or None if it is invalid.

    Invalid: missing entity, ``unavailable``/``unknown``/empty, not a finite
    float, ``last_reported`` older than ``stale_timeout``, or outside
    ``valid_range``.
    """
    if value is None:
        return None
    number = parse_float(value.state)
    if number is None:
        return None
    if value.last_reported is not None and now - value.last_reported > stale_timeout:
        return None
    low, high = valid_range
    if not low <= number <= high:
        return None
    return number


def is_home(value: InputValue | None) -> bool:
    """Return True if a presence entity counts as "home".

    ``home`` (person, device_tracker), ``on`` (binary_sensor, input_boolean)
    or a number > 0 (zone). Missing, ``unavailable`` and ``unknown`` count as
    home (fail-safe: no away reduction on missing data).
    """
    if value is None or value.state is None or is_unavailable(value.state):
        return True
    state = value.state.strip().lower()
    if state in _HOME_STATES:
        return True
    number = parse_float(state)
    return number is not None and number > 0


@dataclass(frozen=True, kw_only=True)
class Readings:
    """Valid input values of one evaluation, in configuration order."""

    co2: tuple[tuple[str, float], ...]
    humidity: tuple[tuple[str, float], ...]
    invalid: tuple[str, ...]
    presence_home: bool | None

    def summary(self) -> InputSummary:
        """Aggregate the readings for the decision record."""
        co2_entity, co2_max = _max(self.co2)
        h_entity, h_max = _max(self.humidity)
        h_mean = (
            sum(value for _, value in self.humidity) / len(self.humidity)
            if self.humidity
            else None
        )
        return InputSummary(
            co2_max=co2_max,
            co2_max_entity=co2_entity,
            h_max=h_max,
            h_max_entity=h_entity,
            h_mean=h_mean,
            presence_home=self.presence_home,
        )


def _max(values: Sequence[tuple[str, float]]) -> tuple[str | None, float | None]:
    """Highest value and its entity; on a tie the first configured entity wins."""
    best: tuple[str | None, float | None] = (None, None)
    for entity_id, value in values:
        if best[1] is None or value > best[1]:
            best = (entity_id, value)
    return best


def read_inputs(
    states: Mapping[str, InputValue], options: Options, now: datetime
) -> Readings:
    """Validate the CO₂ and living humidity inputs and evaluate presence."""
    invalid: list[str] = []

    def collect(
        entity_ids: Sequence[str], valid_range: tuple[float, float]
    ) -> tuple[tuple[str, float], ...]:
        valid: list[tuple[str, float]] = []
        for entity_id in entity_ids:
            value = numeric_value(
                states.get(entity_id),
                now=now,
                stale_timeout=options.stale_timeout,
                valid_range=valid_range,
            )
            if value is None:
                if entity_id not in invalid:
                    invalid.append(entity_id)
            else:
                valid.append((entity_id, value))
        return tuple(valid)

    co2 = collect(options.co2_sensors, CO2_RANGE)
    humidity = collect(options.humidity_sensors, HUMIDITY_RANGE)
    presence_home = (
        any(is_home(states.get(entity_id)) for entity_id in options.presence_entities)
        if options.presence_entities
        else None
    )
    return Readings(
        co2=co2,
        humidity=humidity,
        invalid=tuple(invalid),
        presence_home=presence_home,
    )
