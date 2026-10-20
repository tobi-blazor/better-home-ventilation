"""Input validity and presence (BHV-18, docs/spec/02-control.md#input-validity)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from engine.models import InputValue
from engine.validity import (
    CO2_RANGE,
    HUMIDITY_RANGE,
    is_home,
    numeric_value,
    parse_float,
    read_inputs,
)

from .helpers import CO2_A, CO2_B, HUM_A, PRESENCE, T0, Sim, make_options

STALE = timedelta(minutes=30)


def test_e03_bhv18_unavailable_co2_is_ignored() -> None:
    """E3: one CO₂ sensor unavailable, the other 900 -> 900 is used, entity listed."""
    sim = Sim(values={CO2_A: "unavailable", CO2_B: 900})
    decision, _ = sim.run()
    assert decision.inputs.co2_max == 900
    assert decision.inputs.co2_max_entity == CO2_B
    assert decision.invalid_inputs == (CO2_A,)
    assert decision.target == 110  # ramp(900) = 109.1, quantised


def test_e05_bhv18_stale_value_is_invalid() -> None:
    """E5: unchanged value with ``last_reported`` older than 30 min is invalid."""
    sim = Sim(values={CO2_A: 1200, CO2_B: 900})
    sim.set_stale(CO2_A, T0 - timedelta(minutes=31))
    decision, _ = sim.run()
    assert CO2_A in decision.invalid_inputs
    assert decision.inputs.co2_max == 900

    sim.set_stale(CO2_A, T0 - timedelta(minutes=30))
    decision, _ = sim.run()
    assert CO2_A not in decision.invalid_inputs
    assert decision.inputs.co2_max == 1200


@pytest.mark.parametrize(
    ("state", "valid_range"),
    [
        (None, CO2_RANGE),
        ("unavailable", CO2_RANGE),
        ("unknown", CO2_RANGE),
        ("", CO2_RANGE),
        ("abc", CO2_RANGE),
        ("nan", CO2_RANGE),
        ("inf", CO2_RANGE),
        ("249", CO2_RANGE),
        ("10001", CO2_RANGE),
        ("100.5", HUMIDITY_RANGE),
        ("-1", HUMIDITY_RANGE),
    ],
)
def test_bhv18_invalid_values_are_excluded(
    state: str | None, valid_range: tuple[float, float]
) -> None:
    """Unavailable, non-numeric, non-finite and implausible values are invalid."""
    value = InputValue(state=state, last_reported=T0)
    assert (
        numeric_value(value, now=T0, stale_timeout=STALE, valid_range=valid_range)
        is None
    )


@pytest.mark.parametrize(
    ("state", "valid_range", "expected"),
    [
        ("250", CO2_RANGE, 250.0),
        ("10000", CO2_RANGE, 10000.0),
        ("0", HUMIDITY_RANGE, 0.0),
        ("100", HUMIDITY_RANGE, 100.0),
        (" 45.5 ", HUMIDITY_RANGE, 45.5),
    ],
)
def test_bhv18_plausibility_range_is_inclusive(
    state: str, valid_range: tuple[float, float], expected: float
) -> None:
    """Range limits are valid values."""
    value = InputValue(state=state, last_reported=T0)
    assert (
        numeric_value(value, now=T0, stale_timeout=STALE, valid_range=valid_range)
        == expected
    )


def test_missing_input_and_unknown_age() -> None:
    """A missing entity is invalid; an unknown ``last_reported`` is not stale."""
    assert (
        numeric_value(None, now=T0, stale_timeout=STALE, valid_range=CO2_RANGE) is None
    )
    value = InputValue(state="800")
    assert (
        numeric_value(value, now=T0, stale_timeout=STALE, valid_range=CO2_RANGE) == 800
    )
    assert parse_float(None) is None


@pytest.mark.parametrize(
    ("state", "home"),
    [
        ("home", True),
        ("on", True),
        ("2", True),
        ("unavailable", True),
        ("unknown", True),
        (None, True),
        ("not_home", False),
        ("off", False),
        ("0", False),
        ("work", False),
    ],
)
def test_bhv15_presence_is_fail_safe_home(state: str | None, home: bool) -> None:
    """home/on/number > 0 count as home, and so do missing or unknown states."""
    assert is_home(InputValue(state=state)) is home


def test_presence_away_only_if_all_entities_are_away() -> None:
    """Away = all presence entities not home; missing entity counts as home."""
    options = make_options(presence_entities=[PRESENCE, "person.guest"])
    states = {
        PRESENCE: InputValue(state="not_home"),
        "person.guest": InputValue(state="not_home"),
    }
    assert read_inputs(states, options, T0).presence_home is False
    assert read_inputs({PRESENCE: states[PRESENCE]}, options, T0).presence_home is True
    no_presence = make_options()
    assert read_inputs({}, no_presence, T0).presence_home is None


def test_humidity_mean_and_max_entity() -> None:
    """The mean uses valid values only; ties keep the first configured entity."""
    sim = Sim(values={HUM_A: 50, "sensor.bedroom_humidity": 50})
    decision, _ = sim.run()
    assert decision.inputs.h_max_entity == HUM_A
    assert decision.inputs.h_mean == 50
