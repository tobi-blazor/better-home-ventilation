"""Constants for Better Home Ventilation.

Option keys and defaults live in the pure engine (``engine/options.py``) and are
re-exported here for the Home Assistant glue.
"""

from __future__ import annotations

from typing import Final

from .engine.models import EVENT_MODE_CHANGED as ENGINE_EVENT_MODE_CHANGED
from .engine.options import (
    ACTUATOR_UNIT_FLOW,
    ACTUATOR_UNIT_PERCENT,
    ACTUATOR_UNITS,
    CONF_ACTUATOR,
    CONF_ACTUATOR_MAX,
    CONF_ACTUATOR_MIN,
    CONF_ACTUATOR_UNIT,
    CONF_AWAY_ENABLED,
    CONF_BOOST_DEFAULT_DURATION,
    CONF_BOOST_DEFAULT_LEVEL,
    CONF_BOOST_MAX_DURATION,
    CONF_CO2_END,
    CONF_CO2_SENSORS,
    CONF_CO2_START,
    CONF_DEADBAND_DOWN,
    CONF_DEADBAND_UP,
    CONF_DEBOUNCE,
    CONF_DRYNESS_HUMIDITY_HIGH_LIMIT,
    CONF_DRYNESS_HYSTERESIS,
    CONF_DRYNESS_THRESHOLD,
    CONF_EVALUATION_INTERVAL,
    CONF_FALLBACK_FLOW,
    CONF_FLOW_QUANTUM,
    CONF_HUMIDITY_HIGH_END,
    CONF_HUMIDITY_HIGH_START,
    CONF_HUMIDITY_SENSORS,
    CONF_MAX_FLOW,
    CONF_MAX_STEP_DOWN,
    CONF_MAX_STEP_UP,
    CONF_MIN_FLOW,
    CONF_MIN_WRITE_INTERVAL,
    CONF_NAME,
    CONF_NIGHT_ENABLED,
    CONF_NIGHT_END,
    CONF_NIGHT_START,
    CONF_NOTIFY_SERVICES,
    CONF_OFF_ENTITY,
    CONF_OFF_STATE,
    CONF_PRESENCE_ENTITIES,
    CONF_PROBLEM_DELAY,
    CONF_QUIET_HUMIDITY_OVERRIDE,
    CONF_QUIET_MAX_FLOW,
    CONF_SELECT_MAPPING,
    CONF_STALE_TIMEOUT,
    CONF_STARTUP_GRACE,
    DEFAULT_NAME,
    DEFAULTS,
    OFF_STATE_OFF,
    OFF_STATE_ON,
    OFF_STATES,
    OPTIONS_MINOR_VERSION,
    defaults_for_max_flow,
)

DOMAIN: Final = "better_home_ventilation"

# --- Device (docs/spec/04-interface.md#device) --------------------------------
MANUFACTURER: Final = "Better Home Ventilation"
MODEL: Final = "Demand controller"

# --- Events (docs/spec/04-interface.md#events) --------------------------------
EVENT_MODE_CHANGED: Final = ENGINE_EVENT_MODE_CHANGED
EVENT_READINESS_CORRECTED: Final = "better_home_ventilation_readiness_corrected"

# --- Entities (docs/spec/04-interface.md#entities) --------------------------
#: Keys of the 0.1 entities per platform; ``unique_id = f"{entry_id}_{key}"``.
ENTITY_KEYS: Final[dict[str, tuple[str, ...]]] = {
    "binary_sensor": ("problem",),
    "button": ("boost", "pause", "resume"),
    "number": ("boost_level",),
    "select": ("profile", "boost_duration", "pause_duration"),
    "sensor": ("setpoint", "reason", "mode", "ends_at"),
    "switch": ("control",),
}

#: Options of the ``boost_duration`` select (minutes).
BOOST_DURATION_OPTIONS: Final = ("15", "30", "60", "120")
#: Options of the ``pause_duration`` select and their duration in hours.
PAUSE_DURATION_HOURS: Final[dict[str, int | None]] = {
    "1h": 1,
    "4h": 4,
    "8h": 8,
    "indefinite": None,
}
#: Initial option of the ``pause_duration`` select.
PAUSE_DURATION_DEFAULT: Final = "1h"

# --- Services (docs/spec/04-interface.md#services) ----------------------------
SERVICE_BOOST: Final = "boost"
SERVICE_PAUSE: Final = "pause"
SERVICE_RESUME: Final = "resume"
ATTR_CONFIG_ENTRY_ID: Final = "config_entry_id"
ATTR_DURATION: Final = "duration"
ATTR_LEVEL: Final = "level"

# --- Config subentries (docs/spec/01-configuration.md) -----------------------
SUBENTRY_READINESS_CONDITION: Final = "readiness_condition"

# Keys of a readiness condition subentry's data.
CONF_ENTITY_ID: Final = "entity_id"
CONF_EXPECTED_STATE: Final = "expected_state"
CONF_AUTO_CORRECT: Final = "auto_correct"

__all__ = [
    "ACTUATOR_UNITS",
    "ACTUATOR_UNIT_FLOW",
    "ACTUATOR_UNIT_PERCENT",
    "ATTR_CONFIG_ENTRY_ID",
    "ATTR_DURATION",
    "ATTR_LEVEL",
    "BOOST_DURATION_OPTIONS",
    "CONF_ACTUATOR",
    "CONF_ACTUATOR_MAX",
    "CONF_ACTUATOR_MIN",
    "CONF_ACTUATOR_UNIT",
    "CONF_AUTO_CORRECT",
    "CONF_AWAY_ENABLED",
    "CONF_BOOST_DEFAULT_DURATION",
    "CONF_BOOST_DEFAULT_LEVEL",
    "CONF_BOOST_MAX_DURATION",
    "CONF_CO2_END",
    "CONF_CO2_SENSORS",
    "CONF_CO2_START",
    "CONF_DEADBAND_DOWN",
    "CONF_DEADBAND_UP",
    "CONF_DEBOUNCE",
    "CONF_DRYNESS_HUMIDITY_HIGH_LIMIT",
    "CONF_DRYNESS_HYSTERESIS",
    "CONF_DRYNESS_THRESHOLD",
    "CONF_ENTITY_ID",
    "CONF_EVALUATION_INTERVAL",
    "CONF_EXPECTED_STATE",
    "CONF_FALLBACK_FLOW",
    "CONF_FLOW_QUANTUM",
    "CONF_HUMIDITY_HIGH_END",
    "CONF_HUMIDITY_HIGH_START",
    "CONF_HUMIDITY_SENSORS",
    "CONF_MAX_FLOW",
    "CONF_MAX_STEP_DOWN",
    "CONF_MAX_STEP_UP",
    "CONF_MIN_FLOW",
    "CONF_MIN_WRITE_INTERVAL",
    "CONF_NAME",
    "CONF_NIGHT_ENABLED",
    "CONF_NIGHT_END",
    "CONF_NIGHT_START",
    "CONF_NOTIFY_SERVICES",
    "CONF_OFF_ENTITY",
    "CONF_OFF_STATE",
    "CONF_PRESENCE_ENTITIES",
    "CONF_PROBLEM_DELAY",
    "CONF_QUIET_HUMIDITY_OVERRIDE",
    "CONF_QUIET_MAX_FLOW",
    "CONF_SELECT_MAPPING",
    "CONF_STALE_TIMEOUT",
    "CONF_STARTUP_GRACE",
    "DEFAULTS",
    "DEFAULT_NAME",
    "DOMAIN",
    "ENTITY_KEYS",
    "EVENT_MODE_CHANGED",
    "EVENT_READINESS_CORRECTED",
    "MANUFACTURER",
    "MODEL",
    "OFF_STATES",
    "OFF_STATE_OFF",
    "OFF_STATE_ON",
    "OPTIONS_MINOR_VERSION",
    "PAUSE_DURATION_DEFAULT",
    "PAUSE_DURATION_HOURS",
    "SERVICE_BOOST",
    "SERVICE_PAUSE",
    "SERVICE_RESUME",
    "SUBENTRY_READINESS_CONDITION",
    "defaults_for_max_flow",
]
