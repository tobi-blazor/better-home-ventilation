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
    CONF_DETAILS,
    CONF_DRYNESS_HUMIDITY_HIGH_LIMIT,
    CONF_DRYNESS_HYSTERESIS,
    CONF_DRYNESS_THRESHOLD,
    CONF_EVALUATION_INTERVAL,
    CONF_EXCLUDE_KEYWORDS,
    CONF_FALLBACK_FLOW,
    CONF_FLOW_QUANTUM,
    CONF_HEADLINE_KEYWORDS,
    CONF_HUMIDITY_HIGH_END,
    CONF_HUMIDITY_HIGH_START,
    CONF_HUMIDITY_SENSORS,
    CONF_MAX_FLOW,
    CONF_MAX_STEP_DOWN,
    CONF_MAX_STEP_UP,
    CONF_MIN_FLOW,
    CONF_MIN_SEVERITY,
    CONF_MIN_WRITE_INTERVAL,
    CONF_NAME,
    CONF_NIGHT_ENABLED,
    CONF_NIGHT_END,
    CONF_NIGHT_START,
    CONF_NOTIFY_PROBLEMS,
    CONF_NOTIFY_REMINDERS,
    CONF_NOTIFY_SAFETY,
    CONF_NOTIFY_SERVICES,
    CONF_OFF_ENTITY,
    CONF_OFF_NOTICE_AFTER,
    CONF_OFF_STATE,
    CONF_PAUSE_REMINDER_AFTER,
    CONF_PRESENCE_ENTITIES,
    CONF_PROBLEM_DELAY,
    CONF_QUIET_HUMIDITY_OVERRIDE,
    CONF_QUIET_MAX_FLOW,
    CONF_RESTART_CONFIRM_TIMEOUT,
    CONF_SAFETY_WHEN_DISABLED,
    CONF_SELECT_MAPPING,
    CONF_SOURCE_DEVICE,
    CONF_SOURCE_ENTITY,
    CONF_STALE_TIMEOUT,
    CONF_STARTUP_GRACE,
    CONF_STATES,
    CONF_TEXT_KEYWORDS,
    CONF_THRESHOLD,
    CONF_TRIGGER,
    DEFAULT_NAME,
    DEFAULTS,
    DETAILS_ATTRIBUTES,
    DETAILS_AUTO,
    DETAILS_MODES,
    DETAILS_NONE,
    MIN_SEVERITY_NONE,
    OFF_STATE_OFF,
    OFF_STATE_ON,
    OFF_STATES,
    OPTIONS_MINOR_VERSION,
    TRIGGER_NUMERIC,
    TRIGGER_ON_OFF,
    TRIGGER_STATE_LIST,
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
#: Keys of the 0.2 safety entities, created only with at least one warning
#: source subentry.
SAFETY_ENTITY_KEYS: Final[dict[str, tuple[str, ...]]] = {
    "binary_sensor": ("safety_warning",),
    "switch": ("safety_override",),
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
SERVICE_OVERRIDE_SAFETY: Final = "override_safety"
ATTR_CONFIG_ENTRY_ID: Final = "config_entry_id"
ATTR_DURATION: Final = "duration"
ATTR_LEVEL: Final = "level"

# --- Config subentries (docs/spec/01-configuration.md) -----------------------
SUBENTRY_READINESS_CONDITION: Final = "readiness_condition"
SUBENTRY_WARNING_SOURCE: Final = "warning_source"

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
    "CONF_DETAILS",
    "CONF_DRYNESS_HUMIDITY_HIGH_LIMIT",
    "CONF_DRYNESS_HYSTERESIS",
    "CONF_DRYNESS_THRESHOLD",
    "CONF_ENTITY_ID",
    "CONF_EVALUATION_INTERVAL",
    "CONF_EXCLUDE_KEYWORDS",
    "CONF_EXPECTED_STATE",
    "CONF_FALLBACK_FLOW",
    "CONF_FLOW_QUANTUM",
    "CONF_HEADLINE_KEYWORDS",
    "CONF_HUMIDITY_HIGH_END",
    "CONF_HUMIDITY_HIGH_START",
    "CONF_HUMIDITY_SENSORS",
    "CONF_MAX_FLOW",
    "CONF_MAX_STEP_DOWN",
    "CONF_MAX_STEP_UP",
    "CONF_MIN_FLOW",
    "CONF_MIN_SEVERITY",
    "CONF_MIN_WRITE_INTERVAL",
    "CONF_NAME",
    "CONF_NIGHT_ENABLED",
    "CONF_NIGHT_END",
    "CONF_NIGHT_START",
    "CONF_NOTIFY_PROBLEMS",
    "CONF_NOTIFY_REMINDERS",
    "CONF_NOTIFY_SAFETY",
    "CONF_NOTIFY_SERVICES",
    "CONF_OFF_ENTITY",
    "CONF_OFF_NOTICE_AFTER",
    "CONF_OFF_STATE",
    "CONF_PAUSE_REMINDER_AFTER",
    "CONF_PRESENCE_ENTITIES",
    "CONF_PROBLEM_DELAY",
    "CONF_QUIET_HUMIDITY_OVERRIDE",
    "CONF_QUIET_MAX_FLOW",
    "CONF_RESTART_CONFIRM_TIMEOUT",
    "CONF_SAFETY_WHEN_DISABLED",
    "CONF_SELECT_MAPPING",
    "CONF_SOURCE_DEVICE",
    "CONF_SOURCE_ENTITY",
    "CONF_STALE_TIMEOUT",
    "CONF_STARTUP_GRACE",
    "CONF_STATES",
    "CONF_TEXT_KEYWORDS",
    "CONF_THRESHOLD",
    "CONF_TRIGGER",
    "DEFAULTS",
    "DEFAULT_NAME",
    "DETAILS_ATTRIBUTES",
    "DETAILS_AUTO",
    "DETAILS_MODES",
    "DETAILS_NONE",
    "DOMAIN",
    "ENTITY_KEYS",
    "EVENT_MODE_CHANGED",
    "EVENT_READINESS_CORRECTED",
    "MANUFACTURER",
    "MIN_SEVERITY_NONE",
    "MODEL",
    "OFF_STATES",
    "OFF_STATE_OFF",
    "OFF_STATE_ON",
    "OPTIONS_MINOR_VERSION",
    "PAUSE_DURATION_DEFAULT",
    "PAUSE_DURATION_HOURS",
    "SAFETY_ENTITY_KEYS",
    "SERVICE_BOOST",
    "SERVICE_OVERRIDE_SAFETY",
    "SERVICE_PAUSE",
    "SERVICE_RESUME",
    "SUBENTRY_READINESS_CONDITION",
    "SUBENTRY_WARNING_SOURCE",
    "TRIGGER_NUMERIC",
    "TRIGGER_ON_OFF",
    "TRIGGER_STATE_LIST",
    "defaults_for_max_flow",
]
