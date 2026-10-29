"""Option keys and defaults (docs/spec/01-configuration.md)."""

from __future__ import annotations

import re

import pytest

from engine import options
from engine.options import (
    ADDED_IN_MINOR_VERSION,
    DEFAULTS,
    LIST_OPTIONS,
    MAX_FLOW_SHARES,
    OPTIONS_MINOR_VERSION,
    default_value,
    defaults_for_max_flow,
    migrate_options,
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


def test_02_defaults() -> None:
    """Defaults of the options steps `safety` and `notifications` (0.2)."""
    assert DEFAULTS[options.CONF_RESTART_CONFIRM_TIMEOUT] == 30 * 60
    assert DEFAULTS[options.CONF_SAFETY_WHEN_DISABLED] is True
    assert DEFAULTS[options.CONF_NOTIFY_SAFETY] is True
    assert DEFAULTS[options.CONF_NOTIFY_REMINDERS] is True
    assert DEFAULTS[options.CONF_NOTIFY_PROBLEMS] is True
    assert DEFAULTS[options.CONF_PAUSE_REMINDER_AFTER] == 8 * 3600
    assert DEFAULTS[options.CONF_OFF_NOTICE_AFTER] == 24 * 3600
    assert default_value(options.CONF_NOTIFY_SERVICES) == []
    assert default_value(options.CONF_NOTIFY_SERVICES) is not default_value(
        options.CONF_NOTIFY_SERVICES
    )


def test_migrate_options_1_to_2_adds_defaults_and_keeps_values() -> None:
    """Options minor version 1 -> 2: the 0.2 keys get their defaults."""
    old = {"actuator": "number.flow", "max_flow": 400, "pause_reminder_after": 3600}
    migrated = migrate_options(old, 1)
    assert migrated == {
        "actuator": "number.flow",
        "max_flow": 400,
        "pause_reminder_after": 3600,
        "restart_confirm_timeout": 1800,
        "safety_when_disabled": True,
        "notify_services": [],
        "notify_safety": True,
        "notify_reminders": True,
        "notify_problems": True,
        "off_notice_after": 86400,
    }
    assert set(migrated) - set(old) <= set(ADDED_IN_MINOR_VERSION[2])
    assert "notify_services" not in old  # input unchanged
    assert migrate_options(old, 0) == migrated


def test_migrate_options_current_version_is_a_copy() -> None:
    """Options of the current (or a newer) minor version are copied unchanged."""
    current = {"actuator": "number.flow"}
    assert migrate_options(current, OPTIONS_MINOR_VERSION) == current
    assert migrate_options(current, OPTIONS_MINOR_VERSION) is not current
    assert migrate_options(current, OPTIONS_MINOR_VERSION + 1) == current


def test_every_added_option_has_a_default() -> None:
    """Every key added by a migration has a default (scalar or list)."""
    for keys in ADDED_IN_MINOR_VERSION.values():
        for key in keys:
            assert key in DEFAULTS or key in LIST_OPTIONS


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
