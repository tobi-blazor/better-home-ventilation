"""Options parsing and small model helpers."""

from __future__ import annotations

from datetime import time, timedelta

import pytest

from engine.models import (
    ActuatorSnapshot,
    CommandRejected,
    Options,
    ReadinessCondition,
    Snapshot,
    entity_domain,
    is_unavailable,
)
from engine.modes import unit_running

from .helpers import ACTUATOR, OFF_SWITCH, T0, make_options


def test_options_from_stored_format() -> None:
    """Durations in seconds, times as strings, defaults relative to max flow."""
    condition = ReadinessCondition(entity_id="select.mode", expected_state="Remote")
    options = Options.from_mapping(
        {
            "actuator": ACTUATOR,
            "max_flow": 400,
            "co2_sensors": "sensor.living_room_co2",
            "humidity_sensors": None,
            "night_start": time(21, 30),
            "off_entity": "",
            "actuator_min": "",
            "select_mapping": None,
        },
        [condition],
    )
    assert options.co2_sensors == ("sensor.living_room_co2",)
    assert options.humidity_sensors == ()
    assert options.night_start == time(21, 30)
    assert options.night_end == time(7)
    assert options.off_entity is None
    assert options.off_domain is None
    assert options.actuator_min is None
    assert options.select_mapping == {}
    assert options.min_flow == 80
    assert options.fallback_flow == 150
    assert options.dryness_humidity_high_limit == timedelta(hours=1)
    assert options.actuator_domain == "number"
    assert options.readiness_conditions == (condition,)


def test_options_require_actuator_and_max_flow() -> None:
    """``actuator`` and ``max_flow`` have no default."""
    with pytest.raises(KeyError):
        Options.from_mapping({"actuator": ACTUATOR})


def test_helpers() -> None:
    """Domain parsing, unavailable states and the rejection code."""
    assert entity_domain("fan.unit") == "fan"
    assert is_unavailable(None)
    assert is_unavailable(" Unknown ")
    assert not is_unavailable("0")
    assert CommandRejected("control_disabled").code == "control_disabled"


def test_unit_running_needs_off_actuator_state() -> None:
    """Without an off actuator snapshot the run state is unknown."""
    options = make_options(off_entity=OFF_SWITCH)
    snapshot = Snapshot(
        now=T0, started_at=T0, states={}, actuator=ActuatorSnapshot(state="80")
    )
    assert unit_running(options, snapshot) is None
    assert unit_running(make_options(), snapshot) is None
