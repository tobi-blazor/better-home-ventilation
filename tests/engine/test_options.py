"""Option keys and defaults (docs/spec/01-configuration.md)."""

from __future__ import annotations

import re

import pytest

from engine import options
from engine.options import (
    DEFAULTS,
    MAX_FLOW_SHARES,
    defaults_for_max_flow,
    round_flow,
)

_TIME = re.compile(r"^\d{2}:\d{2}:\d{2}$")


def test_defaults_for_max_flow_400_matches_spec_table() -> None:
    """Defaults relative to max flow for a 400 m³/h unit (0.1 options).

    `shower_level`, `cooking_level` and `auto_boost_quiet_cap` from the same
    table belong to phase 0.3 and are not options of 0.1.
    """
    assert defaults_for_max_flow(400) == {
        "min_flow": 80,
        "fallback_flow": 150,
        "max_step_up": 50,
        "max_step_down": 50,
        "quiet_max_flow": 250,
        "boost_default_level": 300,
    }


@pytest.mark.parametrize(
    ("value", "expected"),
    [(0, 0), (82, 80), (82.5, 85), (152.4, 150), (153.75, 155), (52.5, 55)],
)
def test_round_flow_to_5(value: float, expected: int) -> None:
    """Flows round to the nearest 5 m³/h, halves round up."""
    assert round_flow(value) == expected


def test_defaults_for_max_flow_rounds_each_value() -> None:
    """Every relative default is a multiple of 5 m³/h."""
    result = defaults_for_max_flow(410)
    assert result["fallback_flow"] == 155  # 153.75
    assert result["min_flow"] == 80  # 82
    assert all(value % 5 == 0 for value in result.values())


def test_absolute_defaults() -> None:
    """Absolute defaults: durations in seconds, times as HH:MM:SS."""
    assert DEFAULTS[options.CONF_CO2_START] == 850
    assert DEFAULTS[options.CONF_CO2_END] == 1400
    assert DEFAULTS[options.CONF_DRYNESS_HUMIDITY_HIGH_LIMIT] == 3600
    assert DEFAULTS[options.CONF_MIN_WRITE_INTERVAL] == 300
    assert DEFAULTS[options.CONF_STALE_TIMEOUT] == 1800
    assert DEFAULTS[options.CONF_BOOST_MAX_DURATION] == 4 * 3600
    assert DEFAULTS[options.CONF_NIGHT_START] == "22:00:00"
    assert DEFAULTS[options.CONF_NIGHT_END] == "07:00:00"
    assert DEFAULTS[options.CONF_OFF_STATE] == options.OFF_STATE_ON
    for key in (options.CONF_NIGHT_START, options.CONF_NIGHT_END):
        assert _TIME.match(str(DEFAULTS[key]))


def test_absolute_and_relative_defaults_do_not_overlap() -> None:
    """A key has either an absolute or a max-flow-relative default."""
    assert not set(DEFAULTS) & set(MAX_FLOW_SHARES)


def test_option_keys_are_unique_and_match_constant_names() -> None:
    """`CONF_FOO_BAR = "foo_bar"` for every option key; no key used twice."""
    keys = {
        name: value
        for name, value in vars(options).items()
        if name.startswith("CONF_") and isinstance(value, str)
    }
    for name, value in keys.items():
        assert value == name.removeprefix("CONF_").lower()
    assert len(set(keys.values())) == len(keys)
    assert set(DEFAULTS) <= set(keys.values())
    assert set(MAX_FLOW_SHARES) <= set(keys.values())
