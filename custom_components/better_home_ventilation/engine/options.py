"""Option keys, defaults and the options migration.

Pure module: no Home Assistant imports, no I/O. The keys are the stored keys in
``entry.options`` and in the ``warning_source`` subentries (see
docs/spec/01-configuration.md). Keys never change once released; renames go
through a migration.

Stored format: durations in seconds (int), times as ``"HH:MM:SS"``, flows in
m³/h, entity lists as lists of entity ids.
"""

from __future__ import annotations

from collections.abc import Mapping
import math
from types import MappingProxyType
from typing import Any, Final

type OptionValue = int | float | bool | str

# --- Config flow step `user` -------------------------------------------------
CONF_NAME: Final = "name"
CONF_ACTUATOR: Final = "actuator"
CONF_MAX_FLOW: Final = "max_flow"

# --- Config flow step `actuator` / options step `actuator_mapping` -----------
CONF_ACTUATOR_UNIT: Final = "actuator_unit"
# Mapping of `select` option text -> flow in m³/h.
CONF_SELECT_MAPPING: Final = "select_mapping"

ACTUATOR_UNIT_FLOW: Final = "flow"
ACTUATOR_UNIT_PERCENT: Final = "percent"
ACTUATOR_UNITS: Final = (ACTUATOR_UNIT_FLOW, ACTUATOR_UNIT_PERCENT)

# --- Options step `actuator`, sections `off` and `range` ---------------------
CONF_OFF_ENTITY: Final = "off_entity"
CONF_OFF_STATE: Final = "off_state"
CONF_ACTUATOR_MIN: Final = "actuator_min"
CONF_ACTUATOR_MAX: Final = "actuator_max"

OFF_STATE_ON: Final = "on"
OFF_STATE_OFF: Final = "off"
OFF_STATES: Final = (OFF_STATE_ON, OFF_STATE_OFF)

# --- Config flow step `inputs` / options step `inputs` -----------------------
CONF_CO2_SENSORS: Final = "co2_sensors"
CONF_HUMIDITY_SENSORS: Final = "humidity_sensors"
CONF_PRESENCE_ENTITIES: Final = "presence_entities"

# --- Options step `control` --------------------------------------------------
# section `co2`
CONF_CO2_START: Final = "co2_start"
CONF_CO2_END: Final = "co2_end"
# section `flows`
CONF_MIN_FLOW: Final = "min_flow"
CONF_FALLBACK_FLOW: Final = "fallback_flow"
# section `humidity`
CONF_HUMIDITY_HIGH_START: Final = "humidity_high_start"
CONF_HUMIDITY_HIGH_END: Final = "humidity_high_end"
CONF_DRYNESS_THRESHOLD: Final = "dryness_threshold"
CONF_DRYNESS_HYSTERESIS: Final = "dryness_hysteresis"
CONF_DRYNESS_HUMIDITY_HIGH_LIMIT: Final = "dryness_humidity_high_limit"
# section `quiet`
CONF_NIGHT_ENABLED: Final = "night_enabled"
CONF_NIGHT_START: Final = "night_start"
CONF_NIGHT_END: Final = "night_end"
CONF_QUIET_MAX_FLOW: Final = "quiet_max_flow"
CONF_QUIET_HUMIDITY_OVERRIDE: Final = "quiet_humidity_override"
# section `away`
CONF_AWAY_ENABLED: Final = "away_enabled"
# section `shaping`
CONF_MIN_WRITE_INTERVAL: Final = "min_write_interval"
CONF_MAX_STEP_UP: Final = "max_step_up"
CONF_MAX_STEP_DOWN: Final = "max_step_down"
CONF_DEADBAND_UP: Final = "deadband_up"
CONF_DEADBAND_DOWN: Final = "deadband_down"
CONF_FLOW_QUANTUM: Final = "flow_quantum"
# section `advanced`
CONF_EVALUATION_INTERVAL: Final = "evaluation_interval"
CONF_DEBOUNCE: Final = "debounce"
CONF_STALE_TIMEOUT: Final = "stale_timeout"
CONF_STARTUP_GRACE: Final = "startup_grace"
CONF_PROBLEM_DELAY: Final = "problem_delay"

# --- Options step `boosts`, section `manual` ---------------------------------
CONF_BOOST_DEFAULT_DURATION: Final = "boost_default_duration"
CONF_BOOST_DEFAULT_LEVEL: Final = "boost_default_level"
CONF_BOOST_MAX_DURATION: Final = "boost_max_duration"

# --- Options step `safety` (0.2) ---------------------------------------------
CONF_RESTART_CONFIRM_TIMEOUT: Final = "restart_confirm_timeout"
CONF_SAFETY_WHEN_DISABLED: Final = "safety_when_disabled"

# --- Options step `notifications` (0.2) --------------------------------------
CONF_NOTIFY_SERVICES: Final = "notify_services"
CONF_NOTIFY_SAFETY: Final = "notify_safety"
CONF_NOTIFY_REMINDERS: Final = "notify_reminders"
CONF_NOTIFY_PROBLEMS: Final = "notify_problems"
CONF_PAUSE_REMINDER_AFTER: Final = "pause_reminder_after"
CONF_OFF_NOTICE_AFTER: Final = "off_notice_after"

# --- Subentries `warning_source` (0.2) ---------------------------------------
CONF_SOURCE_ENTITY: Final = "source_entity"
CONF_SOURCE_DEVICE: Final = "source_device"
CONF_TRIGGER: Final = "trigger"
CONF_THRESHOLD: Final = "threshold"
CONF_STATES: Final = "states"
CONF_DETAILS: Final = "details"
CONF_HEADLINE_KEYWORDS: Final = "headline_keywords"
CONF_TEXT_KEYWORDS: Final = "text_keywords"
CONF_EXCLUDE_KEYWORDS: Final = "exclude_keywords"
CONF_MIN_SEVERITY: Final = "min_severity"

TRIGGER_ON_OFF: Final = "on_off"
TRIGGER_NUMERIC: Final = "numeric"
TRIGGER_STATE_LIST: Final = "state_list"
TRIGGERS: Final = (TRIGGER_ON_OFF, TRIGGER_NUMERIC, TRIGGER_STATE_LIST)

DETAILS_AUTO: Final = "auto"
DETAILS_ATTRIBUTES: Final = "attributes"
DETAILS_NONE: Final = "none"
DETAILS_MODES: Final = (DETAILS_AUTO, DETAILS_ATTRIBUTES, DETAILS_NONE)

#: ``min_severity`` value that disables the severity filter.
MIN_SEVERITY_NONE: Final = "none"

# --- Defaults ----------------------------------------------------------------
DEFAULT_NAME: Final = "Ventilation"

_MINUTE: Final = 60
_HOUR: Final = 60 * _MINUTE

#: Absolute defaults (independent of the max flow).
DEFAULTS: Final[Mapping[str, OptionValue]] = MappingProxyType(
    {
        CONF_OFF_STATE: OFF_STATE_ON,
        CONF_CO2_START: 850,
        CONF_CO2_END: 1400,
        CONF_HUMIDITY_HIGH_START: 55,
        CONF_HUMIDITY_HIGH_END: 70,
        CONF_DRYNESS_THRESHOLD: 40,
        CONF_DRYNESS_HYSTERESIS: 2.0,
        CONF_DRYNESS_HUMIDITY_HIGH_LIMIT: 1 * _HOUR,
        CONF_NIGHT_ENABLED: True,
        CONF_NIGHT_START: "22:00:00",
        CONF_NIGHT_END: "07:00:00",
        CONF_QUIET_HUMIDITY_OVERRIDE: 65,
        CONF_AWAY_ENABLED: True,
        CONF_MIN_WRITE_INTERVAL: 5 * _MINUTE,
        CONF_DEADBAND_UP: 10,
        CONF_DEADBAND_DOWN: 20,
        CONF_FLOW_QUANTUM: 5,
        CONF_EVALUATION_INTERVAL: 1 * _MINUTE,
        CONF_DEBOUNCE: 10,
        CONF_STALE_TIMEOUT: 30 * _MINUTE,
        CONF_STARTUP_GRACE: 1 * _MINUTE,
        CONF_PROBLEM_DELAY: 10 * _MINUTE,
        CONF_BOOST_DEFAULT_DURATION: 30 * _MINUTE,
        CONF_BOOST_MAX_DURATION: 4 * _HOUR,
        # 0.2: options steps `safety` and `notifications`
        CONF_RESTART_CONFIRM_TIMEOUT: 30 * _MINUTE,
        CONF_SAFETY_WHEN_DISABLED: True,
        CONF_NOTIFY_SAFETY: True,
        CONF_NOTIFY_REMINDERS: True,
        CONF_NOTIFY_PROBLEMS: True,
        CONF_PAUSE_REMINDER_AFTER: 8 * _HOUR,
        CONF_OFF_NOTICE_AFTER: 24 * _HOUR,
    }
)

#: Options minor version of the config entry (``ConfigFlow.MINOR_VERSION``).
#: 1 = phase 0.1, 2 = phase 0.2 (options steps `safety` and `notifications`).
OPTIONS_MINOR_VERSION: Final = 2

#: Keys added per options minor version; their defaults come from ``DEFAULTS``
#: or, for lists, are an empty list.
ADDED_IN_MINOR_VERSION: Final[Mapping[int, tuple[str, ...]]] = MappingProxyType(
    {
        2: (
            CONF_RESTART_CONFIRM_TIMEOUT,
            CONF_SAFETY_WHEN_DISABLED,
            CONF_NOTIFY_SERVICES,
            CONF_NOTIFY_SAFETY,
            CONF_NOTIFY_REMINDERS,
            CONF_NOTIFY_PROBLEMS,
            CONF_PAUSE_REMINDER_AFTER,
            CONF_OFF_NOTICE_AFTER,
        ),
    }
)

#: Options whose default is an empty list (not in ``DEFAULTS``, which holds
#: immutable values only).
LIST_OPTIONS: Final = frozenset({CONF_PRESENCE_ENTITIES, CONF_NOTIFY_SERVICES})


def default_value(key: str) -> OptionValue | list[str]:
    """Return the stored default of an absolute option (a fresh list for lists)."""
    if key in LIST_OPTIONS:
        return []
    return DEFAULTS[key]


def migrate_options(options: Mapping[str, Any], from_minor: int) -> dict[str, Any]:
    """Upgrade an options dict from minor version ``from_minor`` to the current one.

    Every key added after ``from_minor`` gets its default; values that are
    already present are kept (docs/spec/01-configuration.md#principles: new
    options are added by a migration, never by changing code defaults). A
    ``from_minor`` at or above :data:`OPTIONS_MINOR_VERSION` returns a copy.
    """
    result = dict(options)
    for minor in range(from_minor + 1, OPTIONS_MINOR_VERSION + 1):
        for key in ADDED_IN_MINOR_VERSION.get(minor, ()):
            if key not in result:
                result[key] = default_value(key)
    return result


#: Defaults given as a share of the max flow (0.1 options only).
MAX_FLOW_SHARES: Final[Mapping[str, float]] = MappingProxyType(
    {
        CONF_MIN_FLOW: 0.20,
        CONF_FALLBACK_FLOW: 0.375,
        CONF_MAX_STEP_UP: 0.125,
        CONF_MAX_STEP_DOWN: 0.125,
        CONF_QUIET_MAX_FLOW: 0.625,
        CONF_BOOST_DEFAULT_LEVEL: 0.75,
    }
)

#: Defaults relative to max flow are rounded to this step in m³/h.
FLOW_DEFAULT_ROUNDING: Final = 5


def round_flow(value: float, step: int = FLOW_DEFAULT_ROUNDING) -> int:
    """Round a flow to the nearest multiple of ``step`` (halves round up)."""
    return math.floor(value / step + 0.5) * step


def defaults_for_max_flow(max_flow: float) -> dict[str, int]:
    """Return the "% of max flow" defaults for a unit with ``max_flow`` m³/h.

    Calculated once when the entry is created and stored in the options;
    changing ``max_flow`` later does not change them.
    """
    return {key: round_flow(max_flow * share) for key, share in MAX_FLOW_SHARES.items()}
