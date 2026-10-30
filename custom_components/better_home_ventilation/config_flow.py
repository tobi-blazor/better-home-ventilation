"""Config flow, options flow and the subentry flows (BHV-53).

Spec: docs/spec/01-configuration.md. Everything is stored in ``entry.options``;
``entry.data`` stays empty. The entry is reloaded by one update listener
(``__init__.py``) on every change, so the options flow is a plain
``OptionsFlow`` and subentry flows finish with ``async_update_and_abort``
(docs/adr/0001-update-listener-instead-of-options-flow-with-reload.md).

Subentries: readiness conditions (0.1) and warning sources (0.2). Options
steps `safety` and `notifications` (0.2); sections `shower`, `cooking` and
`common` of the step `boosts` (0.3).
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from functools import partial
import math
from typing import TYPE_CHECKING, Any, Final, Literal

from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.const import (
    ATTR_UNIT_OF_MEASUREMENT,
    PERCENTAGE,
    UnitOfVolumeFlowRate,
)
from homeassistant.core import HomeAssistant, callback, split_entity_id, valid_entity_id
from homeassistant.data_entry_flow import section
from homeassistant.helpers import (
    device_registry as dr,
    entity_registry as er,
    selector,
)
from homeassistant.helpers.translation import async_get_translations

if TYPE_CHECKING:
    # Since HA 2026.9 `voluptuous` is an alias of `probatio` at runtime
    # (docs/spec/05-architecture.md, compatibility); type check against it.
    import probatio as vol
else:
    import voluptuous as vol

from .const import (
    ACTUATOR_UNIT_FLOW,
    ACTUATOR_UNIT_PERCENT,
    ACTUATOR_UNITS,
    CONF_ACTUATOR,
    CONF_ACTUATOR_MAX,
    CONF_ACTUATOR_MIN,
    CONF_ACTUATOR_UNIT,
    CONF_AUTO_BOOST_COOLDOWN,
    CONF_AUTO_BOOST_QUIET_CAP,
    CONF_AUTO_CORRECT,
    CONF_AWAY_ENABLED,
    CONF_BATHROOM_HUMIDITY_SENSORS,
    CONF_BOOST_DEFAULT_DURATION,
    CONF_BOOST_DEFAULT_LEVEL,
    CONF_BOOST_MAX_DURATION,
    CONF_CO2_END,
    CONF_CO2_SENSORS,
    CONF_CO2_START,
    CONF_COOKING_END_OFFSET,
    CONF_COOKING_HIGHER_IS_WORSE,
    CONF_COOKING_LEVEL,
    CONF_COOKING_MAX_DURATION,
    CONF_COOKING_RISE,
    CONF_COOKING_SENSORS,
    CONF_COOKING_TRIGGER,
    CONF_COOKING_TRIGGER_RUN_ON,
    CONF_COOKING_TRIGGER_THRESHOLD,
    CONF_COOKING_WINDOW,
    CONF_DEADBAND_DOWN,
    CONF_DEADBAND_UP,
    CONF_DEBOUNCE,
    CONF_DETAILS,
    CONF_DRYNESS_HUMIDITY_HIGH_LIMIT,
    CONF_DRYNESS_HYSTERESIS,
    CONF_DRYNESS_THRESHOLD,
    CONF_ENTITY_ID,
    CONF_EVALUATION_INTERVAL,
    CONF_EXCLUDE_KEYWORDS,
    CONF_EXPECTED_STATE,
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
    CONF_SHOWER_END_OFFSET,
    CONF_SHOWER_LEVEL,
    CONF_SHOWER_MAX_DURATION,
    CONF_SHOWER_RISE,
    CONF_SHOWER_WINDOW,
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
    DOMAIN,
    MIN_SEVERITY_NONE,
    OFF_STATE_OFF,
    OFF_STATE_ON,
    OFF_STATES,
    OPTIONS_MINOR_VERSION,
    SUBENTRY_READINESS_CONDITION,
    SUBENTRY_WARNING_SOURCE,
    TRIGGER_NUMERIC,
    TRIGGER_ON_OFF,
    TRIGGER_STATE_LIST,
    defaults_for_max_flow,
)
from .engine.models import Severity, is_unavailable
from .engine.safety import (
    PROVIDER_ATTRIBUTES,
    PROVIDER_NONE,
    invalid_regex as is_invalid_regex,
    suggested_filters,
)
from .notifications import mobile_app_services, service_name
from .warning_providers import detect_provider, device_entities

# --- Selector building blocks -------------------------------------------------

FLOW_UNIT: Final = UnitOfVolumeFlowRate.CUBIC_METERS_PER_HOUR
# `CONCENTRATION_PARTS_PER_MILLION` is deprecated from HA 2026.x and its
# replacement `UnitOfRatio` does not exist in HA 2026.4, so the plain unit is used.
PPM: Final = "ppm"
#: Units of a `number` actuator that mean "flow in m³/h".
FLOW_UNITS: Final = frozenset({FLOW_UNIT, "m3/h"})
MAX_FLOW_MIN: Final = 10
MAX_FLOW_MAX: Final = 2000

ACTUATOR_DOMAINS: Final = ["number", "fan", "select"]
OFF_ENTITY_DOMAINS: Final = ["switch", "input_boolean", "fan"]
PRESENCE_DOMAINS: Final = [
    "person",
    "device_tracker",
    "zone",
    "binary_sensor",
    "input_boolean",
]
READINESS_DOMAINS: Final = [
    "select",
    "input_select",
    "switch",
    "input_boolean",
    "number",
    "sensor",
    "binary_sensor",
]
#: Domains whose state the integration can set (``auto_correct``).
WRITABLE_DOMAINS: Final = frozenset(
    {"select", "input_select", "switch", "input_boolean", "number"}
)
ON_OFF_DOMAINS: Final = frozenset({"switch", "input_boolean", "binary_sensor"})
#: Domains of the cooking trigger entity (hob power, hood, ...).
COOKING_TRIGGER_DOMAINS: Final = ["binary_sensor", "sensor", "switch", "fan", "light"]

#: Input groups in which a sensor may appear only once (`duplicate_sensor`), in
#: the order of the forms; the later group reports the error.
SENSOR_GROUPS: Final = (
    CONF_CO2_SENSORS,
    CONF_HUMIDITY_SENSORS,
    CONF_BATHROOM_HUMIDITY_SENSORS,
    CONF_COOKING_SENSORS,
    CONF_COOKING_TRIGGER,
)
#: Flows of the step `boosts` that must lie within min flow ... max flow.
BOOST_LEVEL_KEYS: Final = (
    CONF_BOOST_DEFAULT_LEVEL,
    CONF_SHOWER_LEVEL,
    CONF_COOKING_LEVEL,
    CONF_AUTO_BOOST_QUIET_CAP,
)
OPTION_DOMAINS: Final = frozenset({"select", "input_select"})

#: A `select` actuator needs at least this many (mapped) options.
MIN_SELECT_OPTIONS: Final = 2

#: Selector translation key for the on/off choice of a readiness condition.
BINARY_STATE_TRANSLATION_KEY: Final = "binary_state"

# Options menu steps and sections. Section names never collide with option keys.
STEP_ACTUATOR: Final = "actuator"
STEP_INPUTS: Final = "inputs"
STEP_CONTROL: Final = "control"
STEP_BOOSTS: Final = "boosts"
STEP_SAFETY: Final = "safety"
STEP_NOTIFICATIONS: Final = "notifications"
OPTIONS_MENU: Final = [
    STEP_ACTUATOR,
    STEP_INPUTS,
    STEP_CONTROL,
    STEP_BOOSTS,
    STEP_SAFETY,
    STEP_NOTIFICATIONS,
]

SECTION_OFF: Final = "off"
SECTION_RANGE: Final = "range"
SECTION_ROOMS: Final = "rooms"
SECTION_PRESENCE: Final = "presence"
SECTION_CO2: Final = "co2"
SECTION_FLOWS: Final = "flows"
SECTION_HUMIDITY: Final = "humidity"
SECTION_QUIET: Final = "quiet"
SECTION_AWAY: Final = "away"
SECTION_SHAPING: Final = "shaping"
SECTION_ADVANCED: Final = "advanced"
SECTION_MANUAL: Final = "manual"
SECTION_SHOWER: Final = "shower"
SECTION_COOKING: Final = "cooking"
SECTION_COMMON: Final = "common"
SECTIONS: Final = frozenset(
    {
        SECTION_OFF,
        SECTION_RANGE,
        SECTION_ROOMS,
        SECTION_PRESENCE,
        SECTION_CO2,
        SECTION_FLOWS,
        SECTION_HUMIDITY,
        SECTION_QUIET,
        SECTION_AWAY,
        SECTION_SHAPING,
        SECTION_ADVANCED,
        SECTION_MANUAL,
        SECTION_SHOWER,
        SECTION_COOKING,
        SECTION_COMMON,
    }
)

#: Options stored as seconds and edited with a ``DurationSelector``.
DURATION_KEYS: Final = frozenset(
    {
        CONF_DRYNESS_HUMIDITY_HIGH_LIMIT,
        CONF_MIN_WRITE_INTERVAL,
        CONF_EVALUATION_INTERVAL,
        CONF_DEBOUNCE,
        CONF_STALE_TIMEOUT,
        CONF_STARTUP_GRACE,
        CONF_PROBLEM_DELAY,
        CONF_BOOST_DEFAULT_DURATION,
        CONF_BOOST_MAX_DURATION,
        CONF_RESTART_CONFIRM_TIMEOUT,
        CONF_PAUSE_REMINDER_AFTER,
        CONF_OFF_NOTICE_AFTER,
        CONF_SHOWER_WINDOW,
        CONF_SHOWER_MAX_DURATION,
        CONF_COOKING_WINDOW,
        CONF_COOKING_TRIGGER_RUN_ON,
        CONF_COOKING_MAX_DURATION,
        CONF_AUTO_BOOST_COOLDOWN,
    }
)

#: Integration page with "Add warning source" (description of the step `safety`).
INTEGRATION_URL: Final = f"/config/integrations/integration/{DOMAIN}"

#: Shortest reminder threshold in seconds (`pause_reminder_after`,
#: `off_notice_after`).
MIN_REMINDER_AFTER: Final = 60


def _number_selector(
    minimum: float | None,
    maximum: float | None,
    step: float | Literal["any"],
    unit: str | None,
) -> selector.NumberSelector:
    """Return a number selector in box mode."""
    config = selector.NumberSelectorConfig(
        mode=selector.NumberSelectorMode.BOX, step=step
    )
    if minimum is not None:
        config["min"] = minimum
    if maximum is not None:
        config["max"] = maximum
    if unit is not None:
        config["unit_of_measurement"] = unit
    return selector.NumberSelector(config)


def _flow_selector(max_flow: float, minimum: float = 0) -> selector.NumberSelector:
    """Return a selector for a flow between ``minimum`` and the max flow."""
    return _number_selector(minimum, max_flow, 5, FLOW_UNIT)


def _percent_selector(
    minimum: float, maximum: float, step: float = 1
) -> selector.NumberSelector:
    """Return a selector for a relative humidity in %."""
    return _number_selector(minimum, maximum, step, PERCENTAGE)


def _entity_selector(
    domain: str | list[str],
    *,
    device_class: str | None = None,
    multiple: bool = False,
) -> selector.EntitySelector:
    """Return an entity selector."""
    config = selector.EntitySelectorConfig(domain=domain, multiple=multiple)
    if device_class is not None:
        config["device_class"] = device_class
    return selector.EntitySelector(config)


def _section(fields: Mapping[Any, Any], *, collapsed: bool = False) -> section:
    """Return a section with ``fields``."""
    return section(vol.Schema(dict(fields)), {"collapsed": collapsed})


DURATION_SELECTOR: Final = selector.DurationSelector()
TIME_SELECTOR: Final = selector.TimeSelector()
BOOLEAN_SELECTOR: Final = selector.BooleanSelector()

ACTUATOR_SELECTOR: Final = _entity_selector(ACTUATOR_DOMAINS)
MAX_FLOW_SELECTOR: Final = _number_selector(MAX_FLOW_MIN, MAX_FLOW_MAX, 5, FLOW_UNIT)
CO2_SENSORS_SELECTOR: Final = _entity_selector(
    "sensor", device_class="carbon_dioxide", multiple=True
)
HUMIDITY_SENSORS_SELECTOR: Final = _entity_selector(
    "sensor", device_class="humidity", multiple=True
)

#: Fields of the section `off` (BHV-02), in the config step `user` and the
#: options step `actuator`.
OFF_FIELDS: Final = {
    vol.Optional(CONF_OFF_ENTITY): _entity_selector(OFF_ENTITY_DOMAINS),
    vol.Required(CONF_OFF_STATE, default=OFF_STATE_ON): selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=list(OFF_STATES),
            translation_key=CONF_OFF_STATE,
            mode=selector.SelectSelectorMode.LIST,
        )
    ),
}
OFF_KEYS: Final = (CONF_OFF_ENTITY, CONF_OFF_STATE)

# --- Config flow schemas --------------------------------------------------------

USER_SCHEMA: Final = vol.Schema(
    {
        vol.Required(CONF_NAME, default=DEFAULT_NAME): selector.TextSelector(),
        vol.Required(CONF_ACTUATOR): ACTUATOR_SELECTOR,
        # Optional: left empty, the actuator's `max` is used if its unit is m³/h,
        # otherwise `max_flow_required` (docs/spec/01-configuration.md).
        vol.Optional(CONF_MAX_FLOW): MAX_FLOW_SELECTOR,
        # Collapsed, so the first setup stays short. An omitted section means
        # no off actuator (the default `off_state` comes from DEFAULTS).
        vol.Optional(SECTION_OFF): _section(OFF_FIELDS, collapsed=True),
    }
)

ROOM_FIELDS: Final = {
    vol.Optional(CONF_CO2_SENSORS, default=list): CO2_SENSORS_SELECTOR,
    vol.Optional(CONF_HUMIDITY_SENSORS, default=list): HUMIDITY_SENSORS_SELECTOR,
}
INPUTS_SCHEMA: Final = vol.Schema(ROOM_FIELDS)


# --- Actuator classification ----------------------------------------------------


class ActuatorKind(StrEnum):
    """How an actuator maps flows to its native value."""

    NUMBER_FLOW = "number_flow"
    NUMBER_PERCENT = "number_percent"
    NUMBER_UNKNOWN = "number_unknown"
    FAN = "fan"
    SELECT = "select"


#: Actuator kinds that need the mapping step (`actuator` / `actuator_mapping`).
MAPPING_KINDS: Final = frozenset({ActuatorKind.NUMBER_UNKNOWN, ActuatorKind.SELECT})


@dataclass(frozen=True, slots=True)
class ActuatorInfo:
    """What the flows need to know about an actuator entity."""

    kind: ActuatorKind
    options: tuple[str, ...] = ()
    max_value: float | None = None


def _as_float(value: Any) -> float | None:
    """Return ``value`` as float, or None if it is not numeric."""
    try:
        return float(value)
    except TypeError, ValueError:
        return None


def _actuator_info(hass: HomeAssistant, entity_id: str) -> ActuatorInfo | None:
    """Classify an actuator from its state, falling back to the entity registry.

    Returns None if the entity has neither a state nor a registry entry.
    """
    if not valid_entity_id(entity_id):
        return None
    state = hass.states.get(entity_id)
    entry = er.async_get(hass).async_get(entity_id)
    if state is None and entry is None:
        return None
    attributes: Mapping[str, Any] = state.attributes if state is not None else {}
    capabilities: Mapping[str, Any] = (
        (entry.capabilities or {}) if entry is not None else {}
    )

    def attribute(name: str) -> Any:
        return attributes.get(name, capabilities.get(name))

    domain = split_entity_id(entity_id)[0]
    if domain == "fan":
        return ActuatorInfo(ActuatorKind.FAN)
    if domain == "select":
        return ActuatorInfo(
            ActuatorKind.SELECT,
            options=tuple(str(option) for option in attribute("options") or ()),
        )
    unit = attributes.get(ATTR_UNIT_OF_MEASUREMENT)
    if unit is None and entry is not None:
        unit = entry.unit_of_measurement
    if unit in FLOW_UNITS:
        kind = ActuatorKind.NUMBER_FLOW
    elif unit == PERCENTAGE:
        kind = ActuatorKind.NUMBER_PERCENT
    else:
        kind = ActuatorKind.NUMBER_UNKNOWN
    return ActuatorInfo(kind, max_value=_as_float(attribute("max")))


def _check_actuator(hass: HomeAssistant, entity_id: str) -> ActuatorInfo | str:
    """Return the actuator info, or the error key if the actuator is unusable."""
    info = _actuator_info(hass, entity_id)
    if info is None:
        return "actuator_not_found"
    if info.kind is ActuatorKind.SELECT and len(info.options) < MIN_SELECT_OPTIONS:
        return "select_options"
    return info


def _auto_actuator_options(kind: ActuatorKind) -> dict[str, Any]:
    """Return the actuator options that need no input (mapping step skipped)."""
    if kind is ActuatorKind.NUMBER_FLOW:
        return {CONF_ACTUATOR_UNIT: ACTUATOR_UNIT_FLOW}
    if kind is ActuatorKind.NUMBER_PERCENT:
        return {CONF_ACTUATOR_UNIT: ACTUATOR_UNIT_PERCENT}
    return {}


def _without_mapping(options: Mapping[str, Any]) -> dict[str, Any]:
    """Return ``options`` without the actuator-specific mapping keys."""
    return {
        key: value
        for key, value in options.items()
        if key not in (CONF_ACTUATOR_UNIT, CONF_SELECT_MAPPING)
    }


def _mapping_schema(info: ActuatorInfo, max_flow: float) -> vol.Schema:
    """Return the schema of the mapping step for a `number` or `select` actuator.

    For a `select`, the field keys are the option texts of the device; they are
    not translated (docs/spec/01-configuration.md, step `actuator`).
    """
    if info.kind is ActuatorKind.NUMBER_UNKNOWN:
        return vol.Schema(
            {
                vol.Required(CONF_ACTUATOR_UNIT): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=list(ACTUATOR_UNITS),
                        translation_key=CONF_ACTUATOR_UNIT,
                        mode=selector.SelectSelectorMode.LIST,
                    )
                )
            }
        )
    return vol.Schema(
        {
            vol.Optional(option): _number_selector(0, max_flow, 1, FLOW_UNIT)
            for option in info.options
        }
    )


def _mapping_suggested_values(
    info: ActuatorInfo, options: Mapping[str, Any]
) -> dict[str, Any]:
    """Return the stored mapping as suggested values of the mapping step."""
    if info.kind is ActuatorKind.NUMBER_UNKNOWN:
        if CONF_ACTUATOR_UNIT in options:
            return {CONF_ACTUATOR_UNIT: options[CONF_ACTUATOR_UNIT]}
        return {}
    mapping: Mapping[str, Any] = options.get(CONF_SELECT_MAPPING) or {}
    return {key: value for key, value in mapping.items() if key in info.options}


def _mapping_result(
    info: ActuatorInfo, user_input: Mapping[str, Any]
) -> dict[str, Any] | None:
    """Return the options from the mapping step, or None if the mapping is invalid.

    A `select` needs at least two mapped options, all with distinct flows
    (error `select_mapping`). Unmapped options are left out.
    """
    if info.kind is ActuatorKind.NUMBER_UNKNOWN:
        return {CONF_ACTUATOR_UNIT: user_input[CONF_ACTUATOR_UNIT]}
    mapping = {
        option: _normalize_number(user_input[option])
        for option in info.options
        if user_input.get(option) is not None
    }
    flows = list(mapping.values())
    # A flow must map back to exactly one option: no duplicate flows.
    if len(flows) < MIN_SELECT_OPTIONS or len(set(flows)) != len(flows):
        return None
    return {CONF_SELECT_MAPPING: mapping}


def _max_flow(user_input: Mapping[str, Any], info: ActuatorInfo) -> float | None:
    """Return the entered max flow, or the actuator's max if it is in m³/h."""
    if (value := user_input.get(CONF_MAX_FLOW)) is not None:
        return _normalize_number(value)
    if (
        info.kind is ActuatorKind.NUMBER_FLOW
        and info.max_value is not None
        and MAX_FLOW_MIN <= info.max_value <= MAX_FLOW_MAX
    ):
        return _normalize_number(info.max_value)
    return None


# --- Input validation -------------------------------------------------------------


def _entity_exists(hass: HomeAssistant, entity_id: str) -> bool:
    """Return True if the entity has a state or an entity registry entry."""
    return (
        hass.states.get(entity_id) is not None
        or er.async_get(hass).async_get(entity_id) is not None
    )


def _entity_ids(value: Any) -> list[str]:
    """Return an option value (an entity id or a list of them) as a list."""
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value]


def _duplicate_sensors(
    values: Mapping[str, Any], fields: Collection[str]
) -> dict[str, str]:
    """Report a sensor used in more than one input group (`duplicate_sensor`).

    ``values`` are the stored options merged with the form input, ``fields``
    the groups shown in the form. The later group of the two reports the
    error; if it is not in the form, the earlier one does.
    """
    errors: dict[str, str] = {}
    first: dict[str, str] = {}
    for group in SENSOR_GROUPS:
        for entity_id in _entity_ids(values.get(group)):
            earlier = first.setdefault(entity_id, group)
            if earlier == group:
                continue
            target = group if group in fields else earlier
            if target in fields:
                errors.setdefault(target, "duplicate_sensor")
    return errors


def _missing_sensors(
    hass: HomeAssistant, values: Mapping[str, Any], fields: Collection[str]
) -> dict[str, str]:
    """Report a field with an entity that does not exist (`sensor_not_found`)."""
    return {
        field: "sensor_not_found"
        for field in fields
        if any(
            not _entity_exists(hass, item) for item in _entity_ids(values.get(field))
        )
    }


def _validate_inputs(hass: HomeAssistant, values: Mapping[str, Any]) -> dict[str, str]:
    """Validate the room sensors; return errors per field (or ``base``).

    In the options flow ``values`` include the stored detector sensors, so a
    room sensor that is also a bathroom or cooking sensor is a duplicate.
    """
    co2: list[str] = values.get(CONF_CO2_SENSORS) or []
    humidity: list[str] = values.get(CONF_HUMIDITY_SENSORS) or []
    if not co2 and not humidity:
        return {"base": "no_inputs"}
    fields = (CONF_CO2_SENSORS, CONF_HUMIDITY_SENSORS)
    return {
        **_missing_sensors(hass, values, fields),
        **_duplicate_sensors(values, fields),
    }


def _validate_off(hass: HomeAssistant, values: Mapping[str, Any]) -> dict[str, str]:
    """Validate the off actuator (BHV-02); errors per field.

    ``values`` are flattened and contain the actuator. Without an off actuator
    there is nothing to check.
    """
    off_entity = values.get(CONF_OFF_ENTITY)
    if not off_entity:
        return {}
    if off_entity == values.get(CONF_ACTUATOR):
        return {CONF_OFF_ENTITY: "off_entity_is_actuator"}
    if not _entity_exists(hass, off_entity):
        return {CONF_OFF_ENTITY: "off_entity_not_found"}
    return {}


def _validate_control(values: Mapping[str, Any]) -> dict[str, str]:
    """Cross-field validation of the `control` step; errors per field.

    ``values`` are the options merged with the form input, so stored inputs
    (presence entities) and the max flow are available.
    """
    errors: dict[str, str] = {}
    max_flow = values[CONF_MAX_FLOW]
    min_flow = values[CONF_MIN_FLOW]
    if values[CONF_CO2_START] >= values[CONF_CO2_END]:
        errors[CONF_CO2_END] = "co2_range"
    if values[CONF_HUMIDITY_HIGH_START] >= values[CONF_HUMIDITY_HIGH_END]:
        errors[CONF_HUMIDITY_HIGH_END] = "humidity_range"
    if (
        values[CONF_DRYNESS_THRESHOLD] + values[CONF_DRYNESS_HYSTERESIS]
        >= values[CONF_HUMIDITY_HIGH_START]
    ):
        errors[CONF_DRYNESS_THRESHOLD] = "dryness_overlap"
    if not min_flow <= values[CONF_FALLBACK_FLOW] <= max_flow:
        errors[CONF_FALLBACK_FLOW] = "fallback_range"
    if not min_flow <= values[CONF_QUIET_MAX_FLOW] <= max_flow:
        errors[CONF_QUIET_MAX_FLOW] = "quiet_range"
    if values[CONF_NIGHT_START] == values[CONF_NIGHT_END]:
        errors[CONF_NIGHT_END] = "night_window"
    return errors


def _validate_boosts(hass: HomeAssistant, values: Mapping[str, Any]) -> dict[str, str]:
    """Validate the `boosts` step (options merged with the input).

    Every level and the quiet cap lie within min flow ... max flow
    (`level_range`); the detector sensors and the trigger exist
    (`sensor_not_found`) and are not used in another input group
    (`duplicate_sensor`).
    """
    errors = {
        key: "level_range"
        for key in BOOST_LEVEL_KEYS
        if not values[CONF_MIN_FLOW] <= values[key] <= values[CONF_MAX_FLOW]
    }
    fields = (
        CONF_BATHROOM_HUMIDITY_SENSORS,
        CONF_COOKING_SENSORS,
        CONF_COOKING_TRIGGER,
    )
    return {
        **_missing_sensors(hass, values, fields),
        **_duplicate_sensors(values, fields),
        **errors,
    }


def _validate_notifications(values: Mapping[str, Any]) -> dict[str, str]:
    """Validate the `notifications` step: reminders need at least one minute."""
    return {
        key: "reminder_too_short"
        for key in (CONF_PAUSE_REMINDER_AFTER, CONF_OFF_NOTICE_AFTER)
        if values[key] < MIN_REMINDER_AFTER
    }


def _no_validation(_values: Mapping[str, Any]) -> dict[str, str]:
    """Accept every input (the selectors validate the fields)."""
    return {}


# --- Form helpers ---------------------------------------------------------------


def _normalize_number(value: float) -> float:
    """Return integral floats as int, so stored values keep their integer type."""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _seconds_to_duration(seconds: float) -> dict[str, int]:
    """Convert stored seconds to a `DurationSelector` value."""
    hours, rest = divmod(int(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    return {"hours": hours, "minutes": minutes, "seconds": secs}


def _duration_to_seconds(value: Mapping[str, float]) -> int:
    """Convert a `DurationSelector` value to seconds."""
    return int(timedelta(**value).total_seconds())


def _schema_fields(schema: vol.Schema) -> dict[str, str | None]:
    """Return every field of ``schema`` with the section it is in (or None)."""
    fields: dict[str, str | None] = {}
    for marker, value in schema.schema.items():
        if isinstance(value, section):
            for inner in value.schema.schema:
                fields[str(inner.schema)] = str(marker.schema)
        else:
            fields[str(marker.schema)] = None
    return fields


def _flatten(user_input: Mapping[str, Any]) -> dict[str, Any]:
    """Flatten section input and convert form values to the stored format."""
    flat: dict[str, Any] = {}
    for key, value in user_input.items():
        if key in SECTIONS:
            flat.update(value)
        else:
            flat[key] = value
    for key, value in flat.items():
        if key in DURATION_KEYS:
            flat[key] = _duration_to_seconds(value)
        elif isinstance(value, float):
            flat[key] = _normalize_number(value)
    return flat


def _merge_options(
    options: Mapping[str, Any], schema: vol.Schema, user_input: Mapping[str, Any]
) -> dict[str, Any]:
    """Return ``options`` updated with the input of a form built from ``schema``.

    Fields of the form missing in the input (a cleared optional field) are
    removed; options not in the form (e.g. a hidden section) are kept.
    """
    fields = _schema_fields(schema)
    merged = {key: value for key, value in options.items() if key not in fields}
    merged.update(_flatten(user_input))
    return merged


def _suggested_values(schema: vol.Schema, options: Mapping[str, Any]) -> dict[str, Any]:
    """Return stored options nested per section, in the form format."""
    suggested: dict[str, Any] = {}
    for key, section_name in _schema_fields(schema).items():
        if key not in options:
            continue
        value = options[key]
        if key in DURATION_KEYS:
            value = _seconds_to_duration(value)
        if section_name is None:
            suggested[key] = value
        else:
            suggested.setdefault(section_name, {})[key] = value
    return suggested


def _form_errors(field_errors: Mapping[str, str], schema: vol.Schema) -> dict[str, str]:
    """Key errors of fields inside a section by the section.

    The frontend shows errors per top-level form item; a field inside a section
    reports its error on the section (the pattern of the core MQTT flow).
    """
    fields = _schema_fields(schema)
    return {fields.get(key) or key: error for key, error in field_errors.items()}


# --- Config flow --------------------------------------------------------------------


class BetterHomeVentilationConfigFlow(ConfigFlow, domain=DOMAIN):
    """Create a Better Home Ventilation entry: `user` → `actuator` → `inputs`."""

    VERSION = 1
    #: 2 since phase 0.2 (options `safety` and `notifications`, migrated in
    #: `async_migrate_entry`).
    MINOR_VERSION = OPTIONS_MINOR_VERSION

    def __init__(self) -> None:
        """Initialize the flow."""
        self._title: str = DEFAULT_NAME
        self._options: dict[str, Any] = {}
        self._actuator: ActuatorInfo = ActuatorInfo(ActuatorKind.FAN)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return BetterHomeVentilationOptionsFlow()

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Return the supported subentry types (readiness conditions, warning sources)."""
        return {
            SUBENTRY_READINESS_CONDITION: ReadinessConditionFlow,
            SUBENTRY_WARNING_SOURCE: WarningSourceFlow,
        }

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the name, the actuator, the max flow and the off actuator.

        BHV-01, BHV-02: the off actuator is optional; without it, off means the
        lowest actuator value, which some units do not take as a stop.
        """
        errors: dict[str, str] = {}
        if user_input is not None:
            actuator = user_input[CONF_ACTUATOR]
            self._async_abort_entries_match({CONF_ACTUATOR: actuator})
            values = _flatten(user_input)
            errors = _form_errors(_validate_off(self.hass, values), USER_SCHEMA)
            info = _check_actuator(self.hass, actuator)
            if isinstance(info, str):
                errors[CONF_ACTUATOR] = info
            elif (max_flow := _max_flow(user_input, info)) is None:
                errors[CONF_MAX_FLOW] = "max_flow_required"
            elif not errors:
                self._title = user_input[CONF_NAME]
                self._actuator = info
                self._options = {
                    CONF_ACTUATOR: actuator,
                    CONF_MAX_FLOW: max_flow,
                    **{key: values[key] for key in OFF_KEYS if key in values},
                }
                if info.kind in MAPPING_KINDS:
                    return await self.async_step_actuator()
                self._options.update(_auto_actuator_options(info.kind))
                return await self.async_step_inputs()
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, user_input),
            errors=errors,
        )

    async def async_step_actuator(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the unit of a `number` or the flows of a `select` actuator."""
        errors: dict[str, str] = {}
        schema = _mapping_schema(self._actuator, self._options[CONF_MAX_FLOW])
        if user_input is not None:
            result = _mapping_result(self._actuator, user_input)
            if result is None:
                errors["base"] = "select_mapping"
            else:
                self._options.update(result)
                return await self.async_step_inputs()
        return self.async_show_form(
            step_id="actuator",
            data_schema=self.add_suggested_values_to_schema(schema, user_input),
            errors=errors,
        )

    async def async_step_inputs(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the room sensors and create the entry with all defaults."""
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate_inputs(self.hass, user_input)
            if not errors:
                options: dict[str, Any] = {
                    **DEFAULTS,
                    **defaults_for_max_flow(self._options[CONF_MAX_FLOW]),
                    **self._options,
                    CONF_CO2_SENSORS: user_input[CONF_CO2_SENSORS],
                    CONF_HUMIDITY_SENSORS: user_input[CONF_HUMIDITY_SENSORS],
                    CONF_PRESENCE_ENTITIES: [],
                    CONF_NOTIFY_SERVICES: [],
                    CONF_BATHROOM_HUMIDITY_SENSORS: [],
                    CONF_COOKING_SENSORS: [],
                }
                return self.async_create_entry(
                    title=self._title, data={}, options=options
                )
        return self.async_show_form(
            step_id="inputs",
            data_schema=self.add_suggested_values_to_schema(INPUTS_SCHEMA, user_input),
            errors=errors,
        )


# --- Options flow ---------------------------------------------------------------------


def _actuator_options_schema() -> vol.Schema:
    """Return the schema of the options step `actuator`."""
    return vol.Schema(
        {
            vol.Required(CONF_ACTUATOR): ACTUATOR_SELECTOR,
            vol.Required(CONF_MAX_FLOW): MAX_FLOW_SELECTOR,
            vol.Required(SECTION_OFF): _section(OFF_FIELDS),
            vol.Required(SECTION_RANGE): _section(
                {
                    vol.Optional(CONF_ACTUATOR_MIN): _number_selector(
                        None, None, "any", None
                    ),
                    vol.Optional(CONF_ACTUATOR_MAX): _number_selector(
                        None, None, "any", None
                    ),
                },
                collapsed=True,
            ),
        }
    )


def _inputs_options_schema() -> vol.Schema:
    """Return the schema of the options step `inputs`."""
    return vol.Schema(
        {
            vol.Required(SECTION_ROOMS): _section(ROOM_FIELDS),
            vol.Required(SECTION_PRESENCE): _section(
                {
                    vol.Optional(
                        CONF_PRESENCE_ENTITIES, default=list
                    ): _entity_selector(PRESENCE_DOMAINS, multiple=True),
                }
            ),
        }
    )


def _control_options_schema(max_flow: float) -> vol.Schema:
    """Return the schema of the options step `control`."""
    fields: dict[Any, Any] = {
        vol.Required(SECTION_CO2): _section(
            {
                vol.Required(CONF_CO2_START): _number_selector(400, 2000, 10, PPM),
                vol.Required(CONF_CO2_END): _number_selector(500, 3000, 10, PPM),
            }
        ),
        vol.Required(SECTION_FLOWS): _section(
            {
                vol.Required(CONF_MIN_FLOW): _flow_selector(max_flow),
                vol.Required(CONF_FALLBACK_FLOW): _flow_selector(max_flow),
            }
        ),
        vol.Required(SECTION_HUMIDITY): _section(
            {
                vol.Required(CONF_HUMIDITY_HIGH_START): _percent_selector(30, 90),
                vol.Required(CONF_HUMIDITY_HIGH_END): _percent_selector(35, 100),
                vol.Required(CONF_DRYNESS_THRESHOLD): _percent_selector(20, 60),
                vol.Required(CONF_DRYNESS_HYSTERESIS): _percent_selector(0, 10, 0.5),
                vol.Required(CONF_DRYNESS_HUMIDITY_HIGH_LIMIT): DURATION_SELECTOR,
            }
        ),
        vol.Required(SECTION_QUIET): _section(
            {
                vol.Required(CONF_NIGHT_ENABLED): BOOLEAN_SELECTOR,
                vol.Required(CONF_NIGHT_START): TIME_SELECTOR,
                vol.Required(CONF_NIGHT_END): TIME_SELECTOR,
                vol.Required(CONF_QUIET_MAX_FLOW): _flow_selector(max_flow),
                vol.Required(CONF_QUIET_HUMIDITY_OVERRIDE): _percent_selector(40, 100),
            }
        ),
        vol.Required(SECTION_AWAY): _section(
            {vol.Required(CONF_AWAY_ENABLED): BOOLEAN_SELECTOR}
        ),
        vol.Required(SECTION_SHAPING): _section(
            {
                vol.Required(CONF_MIN_WRITE_INTERVAL): DURATION_SELECTOR,
                vol.Required(CONF_MAX_STEP_UP): _flow_selector(max_flow, 5),
                vol.Required(CONF_MAX_STEP_DOWN): _flow_selector(max_flow, 5),
                vol.Required(CONF_DEADBAND_UP): _number_selector(0, 100, 1, FLOW_UNIT),
                vol.Required(CONF_DEADBAND_DOWN): _number_selector(
                    0, 100, 1, FLOW_UNIT
                ),
                vol.Required(CONF_FLOW_QUANTUM): _number_selector(1, 50, 1, FLOW_UNIT),
            },
            collapsed=True,
        ),
    }
    # Always shown, collapsed: HA deprecated advanced mode in 2026.10
    # (`show_advanced_options`, breaks in 2027.6).
    fields[vol.Required(SECTION_ADVANCED)] = _section(
        {
            vol.Required(CONF_EVALUATION_INTERVAL): DURATION_SELECTOR,
            vol.Required(CONF_DEBOUNCE): DURATION_SELECTOR,
            vol.Required(CONF_STALE_TIMEOUT): DURATION_SELECTOR,
            vol.Required(CONF_STARTUP_GRACE): DURATION_SELECTOR,
            vol.Required(CONF_PROBLEM_DELAY): DURATION_SELECTOR,
        },
        collapsed=True,
    )
    return vol.Schema(fields)


def _boosts_options_schema(max_flow: float) -> vol.Schema:
    """Return the schema of the options step `boosts`.

    Sections `manual`, `shower`, `cooking` and `common` (collapsed). A
    detector is active when its sensors are set; there are no enable switches.
    """
    return vol.Schema(
        {
            vol.Required(SECTION_MANUAL): _section(
                {
                    vol.Required(CONF_BOOST_DEFAULT_DURATION): DURATION_SELECTOR,
                    vol.Required(CONF_BOOST_DEFAULT_LEVEL): _flow_selector(max_flow),
                    vol.Required(CONF_BOOST_MAX_DURATION): DURATION_SELECTOR,
                }
            ),
            vol.Required(SECTION_SHOWER): _section(
                {
                    vol.Optional(
                        CONF_BATHROOM_HUMIDITY_SENSORS, default=list
                    ): HUMIDITY_SENSORS_SELECTOR,
                    vol.Required(CONF_SHOWER_RISE): _percent_selector(1, 40, 0.5),
                    vol.Required(CONF_SHOWER_WINDOW): DURATION_SELECTOR,
                    vol.Required(CONF_SHOWER_END_OFFSET): _percent_selector(0, 30, 0.5),
                    vol.Required(CONF_SHOWER_LEVEL): _flow_selector(max_flow),
                    vol.Required(CONF_SHOWER_MAX_DURATION): DURATION_SELECTOR,
                }
            ),
            vol.Required(SECTION_COOKING): _section(
                {
                    vol.Optional(CONF_COOKING_SENSORS, default=list): _entity_selector(
                        "sensor", multiple=True
                    ),
                    vol.Required(CONF_COOKING_HIGHER_IS_WORSE): BOOLEAN_SELECTOR,
                    vol.Required(CONF_COOKING_RISE): _number_selector(1, 500, 1, None),
                    vol.Required(CONF_COOKING_WINDOW): DURATION_SELECTOR,
                    vol.Required(CONF_COOKING_END_OFFSET): _number_selector(
                        0, 200, 1, None
                    ),
                    vol.Optional(CONF_COOKING_TRIGGER): _entity_selector(
                        COOKING_TRIGGER_DOMAINS
                    ),
                    vol.Required(CONF_COOKING_TRIGGER_THRESHOLD): _number_selector(
                        0, None, "any", None
                    ),
                    vol.Required(CONF_COOKING_TRIGGER_RUN_ON): DURATION_SELECTOR,
                    vol.Required(CONF_COOKING_LEVEL): _flow_selector(max_flow),
                    vol.Required(CONF_COOKING_MAX_DURATION): DURATION_SELECTOR,
                }
            ),
            vol.Required(SECTION_COMMON): _section(
                {
                    vol.Required(CONF_AUTO_BOOST_QUIET_CAP): _flow_selector(max_flow),
                    vol.Required(CONF_AUTO_BOOST_COOLDOWN): DURATION_SELECTOR,
                },
                collapsed=True,
            ),
        }
    )


SAFETY_OPTIONS_SCHEMA: Final = vol.Schema(
    {
        vol.Required(CONF_RESTART_CONFIRM_TIMEOUT): DURATION_SELECTOR,
        vol.Required(CONF_SAFETY_WHEN_DISABLED): BOOLEAN_SELECTOR,
    }
)


def _notifications_options_schema(services: list[str]) -> vol.Schema:
    """Return the schema of the options step `notifications`.

    ``services`` are the ``notify.mobile_app_*`` services when the step opens.
    """
    return vol.Schema(
        {
            vol.Optional(CONF_NOTIFY_SERVICES, default=list): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        selector.SelectOptionDict(value=service, label=service)
                        for service in services
                    ],
                    multiple=True,
                    mode=selector.SelectSelectorMode.LIST,
                )
            ),
            vol.Required(CONF_NOTIFY_SAFETY): BOOLEAN_SELECTOR,
            vol.Required(CONF_NOTIFY_REMINDERS): BOOLEAN_SELECTOR,
            vol.Required(CONF_NOTIFY_PROBLEMS): BOOLEAN_SELECTOR,
            vol.Required(CONF_PAUSE_REMINDER_AFTER): DURATION_SELECTOR,
            vol.Required(CONF_OFF_NOTICE_AFTER): DURATION_SELECTOR,
        }
    )


#: Options named in the max-flow hint of the options step `actuator`.
MAX_FLOW_HINT_KEYS: Final = (
    CONF_MAX_FLOW,
    CONF_MIN_FLOW,
    CONF_FALLBACK_FLOW,
    CONF_QUIET_MAX_FLOW,
    CONF_BOOST_DEFAULT_LEVEL,
    CONF_SHOWER_LEVEL,
    CONF_COOKING_LEVEL,
    CONF_AUTO_BOOST_QUIET_CAP,
)


class BetterHomeVentilationOptionsFlow(OptionsFlow):
    """Change every setting of an entry; the update listener reloads it."""

    def __init__(self) -> None:
        """Initialize the flow."""
        self._pending: dict[str, Any] = {}
        self._actuator: ActuatorInfo = ActuatorInfo(ActuatorKind.FAN)

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the menu of option steps."""
        return self.async_show_menu(step_id="init", menu_options=OPTIONS_MENU)

    async def async_step_actuator(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the actuator, max flow, off actuator and native range."""
        options = self.config_entry.options
        schema = _actuator_options_schema()
        errors: dict[str, str] = {}
        if user_input is not None:
            merged = _merge_options(options, schema, user_input)
            actuator = merged[CONF_ACTUATOR]
            errors = _form_errors(_validate_off(self.hass, merged), schema)
            info = _check_actuator(self.hass, actuator)
            if isinstance(info, str):
                errors[CONF_ACTUATOR] = info
            elif self._actuator_in_use(actuator):
                errors[CONF_ACTUATOR] = "actuator_in_use"
            elif not errors:
                self._pending = _without_mapping(merged)
                self._actuator = info
                # Also shown when such an actuator is kept, so its mapping can
                # be edited (pre-filled with the stored mapping).
                if info.kind in MAPPING_KINDS:
                    return await self.async_step_actuator_mapping()
                return self.async_create_entry(
                    data=self._pending | _auto_actuator_options(info.kind)
                )
            suggested: dict[str, Any] = user_input
        else:
            suggested = _suggested_values(schema, options)
        return self.async_show_form(
            step_id=STEP_ACTUATOR,
            data_schema=self.add_suggested_values_to_schema(schema, suggested),
            errors=errors,
            description_placeholders={
                key: str(options.get(key, "-")) for key in MAX_FLOW_HINT_KEYS
            },
        )

    async def async_step_actuator_mapping(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the unit of a `number` or the flows of a `select` actuator."""
        options = self.config_entry.options
        schema = _mapping_schema(self._actuator, self._pending[CONF_MAX_FLOW])
        errors: dict[str, str] = {}
        if user_input is not None:
            result = _mapping_result(self._actuator, user_input)
            if result is None:
                errors["base"] = "select_mapping"
            else:
                return self.async_create_entry(data=self._pending | result)
            suggested: dict[str, Any] = user_input
        elif self._pending[CONF_ACTUATOR] == options.get(CONF_ACTUATOR):
            suggested = _mapping_suggested_values(self._actuator, options)
        else:
            suggested = {}
        return self.async_show_form(
            step_id="actuator_mapping",
            data_schema=self.add_suggested_values_to_schema(schema, suggested),
            errors=errors,
        )

    async def async_step_inputs(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the room sensors and presence entities."""
        return await self._async_options_form(
            STEP_INPUTS,
            _inputs_options_schema(),
            user_input,
            partial(_validate_inputs, self.hass),
        )

    async def async_step_control(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the control parameters."""
        schema = _control_options_schema(self.config_entry.options[CONF_MAX_FLOW])
        return await self._async_options_form(
            STEP_CONTROL, schema, user_input, _validate_control
        )

    async def async_step_boosts(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the manual boost defaults and the shower and cooking detection."""
        schema = _boosts_options_schema(self.config_entry.options[CONF_MAX_FLOW])
        return await self._async_options_form(
            STEP_BOOSTS, schema, user_input, partial(_validate_boosts, self.hass)
        )

    async def async_step_safety(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the restart confirmation and the safety while disabled."""
        return await self._async_options_form(
            STEP_SAFETY,
            SAFETY_OPTIONS_SCHEMA,
            user_input,
            _no_validation,
            description_placeholders={"integration_url": INTEGRATION_URL},
        )

    async def async_step_notifications(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose the phones and which notifications they get.

        The list shows the ``notify.mobile_app_*`` services that exist now;
        stored services that no longer exist are not pre-selected, so saving
        removes them (repair issue ``missing_notify_service``).
        """
        services = mobile_app_services(self.hass)
        options = self.config_entry.options
        stored = [
            service_name(str(service))
            for service in options.get(CONF_NOTIFY_SERVICES) or []
        ]
        return await self._async_options_form(
            STEP_NOTIFICATIONS,
            _notifications_options_schema(services),
            user_input,
            _validate_notifications,
            options={
                **options,
                CONF_NOTIFY_SERVICES: [s for s in stored if s in services],
            },
        )

    async def _async_options_form(
        self,
        step_id: str,
        schema: vol.Schema,
        user_input: dict[str, Any] | None,
        validate: Callable[[Mapping[str, Any]], dict[str, str]],
        *,
        options: Mapping[str, Any] | None = None,
        description_placeholders: Mapping[str, str] | None = None,
    ) -> ConfigFlowResult:
        """Show a one-form options step and save it when the input is valid.

        ``validate`` gets the stored options merged with the flattened input
        and returns errors per field (or ``base``). ``options`` replaces the
        stored options as the base of the form (pre-filled values).
        """
        if options is None:
            options = self.config_entry.options
        errors: dict[str, str] = {}
        if user_input is not None:
            merged = _merge_options(options, schema, user_input)
            field_errors = validate(merged)
            if not field_errors:
                return self.async_create_entry(data=merged)
            errors = _form_errors(field_errors, schema)
            suggested: dict[str, Any] = user_input
        else:
            suggested = _suggested_values(schema, options)
        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(schema, suggested),
            errors=errors,
            description_placeholders=description_placeholders,
        )

    def _actuator_in_use(self, actuator: str) -> bool:
        """Return True if another entry controls ``actuator``."""
        return any(
            entry.options.get(CONF_ACTUATOR) == actuator
            for entry in self.hass.config_entries.async_entries(DOMAIN)
            if entry.entry_id != self.config_entry.entry_id
        )


# --- Readiness condition subentries -------------------------------------------------

READINESS_ENTITY_SCHEMA: Final = vol.Schema(
    {vol.Required(CONF_ENTITY_ID): _entity_selector(READINESS_DOMAINS)}
)


def _friendly_name(hass: HomeAssistant, entity_id: str) -> str:
    """Return the friendly name of an entity, falling back to the entity id."""
    if (state := hass.states.get(entity_id)) is not None:
        return state.name
    if (entry := er.async_get(hass).async_get(entity_id)) is not None:
        return entry.name or entry.original_name or entity_id
    return entity_id


def _expected_state_selector(hass: HomeAssistant, entity_id: str) -> Any:
    """Return the selector for the expected state, depending on the domain."""
    domain = split_entity_id(entity_id)[0]
    state = hass.states.get(entity_id)
    if (
        domain in OPTION_DOMAINS
        and state is not None
        and state.attributes.get("options")
    ):
        return selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[str(option) for option in state.attributes["options"]],
                mode=selector.SelectSelectorMode.DROPDOWN,
            )
        )
    if domain in ON_OFF_DOMAINS:
        return selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[OFF_STATE_ON, OFF_STATE_OFF],
                translation_key=BINARY_STATE_TRANSLATION_KEY,
                mode=selector.SelectSelectorMode.LIST,
            )
        )
    if domain == "number":
        return _number_selector(None, None, "any", None)
    return selector.TextSelector()


def _readiness_state_schema(
    hass: HomeAssistant, entity_id: str, suggested: Mapping[str, Any]
) -> vol.Schema:
    """Return the schema of the readiness step `state` with suggested values.

    `auto_correct` is only offered for writable domains. For other domains the
    schema accepts extra keys, so a submitted `auto_correct` is reported as
    `not_correctable` instead of failing schema validation. The suggested
    values are set on the markers directly, because
    `add_suggested_values_to_schema` would drop the `extra` setting.
    """

    def marker(key: str, **kwargs: Any) -> vol.Required:
        if key in suggested:
            kwargs["description"] = {"suggested_value": suggested[key]}
        return vol.Required(key, **kwargs)

    fields: dict[Any, Any] = {
        marker(CONF_EXPECTED_STATE): _expected_state_selector(hass, entity_id)
    }
    if split_entity_id(entity_id)[0] in WRITABLE_DOMAINS:
        fields[marker(CONF_AUTO_CORRECT, default=False)] = BOOLEAN_SELECTOR
        return vol.Schema(fields)
    return vol.Schema(fields, extra=vol.ALLOW_EXTRA)


class ReadinessConditionFlow(ConfigSubentryFlow):
    """Add or reconfigure a readiness condition (BHV-04).

    Subentry data: ``entity_id``, ``expected_state`` (a number for `number`
    entities, a string otherwise) and ``auto_correct``. The subentry unique id is
    the entity id; the title is "<friendly name> = <expected state>".
    """

    def __init__(self) -> None:
        """Initialize the flow."""
        self._entity_id: str = ""
        self._current: Mapping[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Choose the entity of a new readiness condition."""
        return await self._async_step_entity("user", user_input)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Change the entity of an existing readiness condition."""
        self._current = self._get_reconfigure_subentry().data
        return await self._async_step_entity("reconfigure", user_input)

    async def _async_step_entity(
        self, step_id: str, user_input: dict[str, Any] | None
    ) -> SubentryFlowResult:
        """Show or handle the entity form of `user` and `reconfigure`."""
        if user_input is not None:
            entity_id: str = user_input[CONF_ENTITY_ID]
            own_id = (
                self._get_reconfigure_subentry().subentry_id
                if self.source == SOURCE_RECONFIGURE
                else None
            )
            for subentry in self._get_entry().subentries.values():
                if subentry.unique_id == entity_id and subentry.subentry_id != own_id:
                    return self.async_abort(reason="already_configured")
            self._entity_id = entity_id
            return await self.async_step_state()
        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(
                READINESS_ENTITY_SCHEMA, self._current
            ),
        )

    async def async_step_state(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Choose the expected state and whether it is corrected automatically."""
        entity_id = self._entity_id
        name = _friendly_name(self.hass, entity_id)
        errors: dict[str, str] = {}
        if user_input is not None:
            auto_correct = bool(user_input.get(CONF_AUTO_CORRECT, False))
            if auto_correct and split_entity_id(entity_id)[0] not in WRITABLE_DOMAINS:
                errors["base"] = "not_correctable"
            else:
                expected = user_input[CONF_EXPECTED_STATE]
                if isinstance(expected, float):
                    expected = _normalize_number(expected)
                data = {
                    CONF_ENTITY_ID: entity_id,
                    CONF_EXPECTED_STATE: expected,
                    CONF_AUTO_CORRECT: auto_correct,
                }
                title = f"{name} = {expected}"
                if self.source == SOURCE_RECONFIGURE:
                    return self.async_update_and_abort(
                        self._get_entry(),
                        self._get_reconfigure_subentry(),
                        title=title,
                        unique_id=entity_id,
                        data=data,
                    )
                return self.async_create_entry(
                    title=title, data=data, unique_id=entity_id
                )
            suggested: Mapping[str, Any] = user_input
        elif self._current.get(CONF_ENTITY_ID) == entity_id:
            suggested = self._current
        else:
            suggested = {}
        return self.async_show_form(
            step_id="state",
            data_schema=_readiness_state_schema(self.hass, entity_id, suggested),
            errors=errors,
            description_placeholders={"entity": name},
        )


# --- Warning source subentries --------------------------------------------------------

#: Domains of a source entity (01-configuration.md#warning-sources-config-subentries).
SOURCE_DOMAINS: Final = [
    "binary_sensor",
    "input_boolean",
    "switch",
    "sensor",
    "select",
    "input_select",
]
#: Source domains with a `trigger` step; all others use the trigger `on_off`.
TRIGGER_DOMAINS: Final = frozenset({"sensor", "select", "input_select"})
SECTION_KEYWORDS: Final = "keywords"
KEYWORD_KEYS: Final = (
    CONF_HEADLINE_KEYWORDS,
    CONF_TEXT_KEYWORDS,
    CONF_EXCLUDE_KEYWORDS,
)
SEVERITY_OPTIONS: Final = [
    MIN_SEVERITY_NONE,
    *(severity.value for severity in Severity),
]
#: Keys of the step `filters` in the subentry data.
FILTER_KEYS: Final = (CONF_DETAILS, *KEYWORD_KEYS, CONF_MIN_SEVERITY)

WARNING_SOURCE_SCHEMA: Final = vol.Schema(
    {
        vol.Optional(CONF_SOURCE_ENTITY): _entity_selector(SOURCE_DOMAINS),
        vol.Optional(CONF_SOURCE_DEVICE): selector.DeviceSelector(
            selector.DeviceSelectorConfig(entity=[{"domain": "binary_sensor"}])
        ),
        vol.Optional(CONF_NAME): selector.TextSelector(),
    }
)
KEYWORDS_SELECTOR: Final = selector.TextSelector(
    selector.TextSelectorConfig(multiple=True)
)


def _select(options: list[str], translation_key: str) -> selector.SelectSelector:
    """Return a translated single-choice selector."""
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=options,
            translation_key=translation_key,
            mode=selector.SelectSelectorMode.LIST,
        )
    )


def _is_number(value: str) -> bool:
    """Return True if ``value`` is a finite number."""
    number = _as_float(value)
    return number is not None and math.isfinite(number)


def _entity_options(hass: HomeAssistant, entity_id: str) -> list[str]:
    """Return the options of a select or enum sensor (for the state list)."""
    state = hass.states.get(entity_id)
    options = state.attributes.get("options") if state is not None else None
    return [str(option) for option in options] if options else []


def _trigger_schema(hass: HomeAssistant, entity_id: str) -> vol.Schema:
    """Return the schema of the step `trigger` (sensor, select, input_select)."""
    return vol.Schema(
        {
            vol.Required(CONF_TRIGGER): _select(
                [TRIGGER_NUMERIC, TRIGGER_STATE_LIST], CONF_TRIGGER
            ),
            vol.Optional(CONF_THRESHOLD): _number_selector(None, None, "any", None),
            vol.Optional(CONF_STATES): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=_entity_options(hass, entity_id),
                    multiple=True,
                    custom_value=True,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
        }
    )


def _filters_schema(*, with_filters: bool) -> vol.Schema:
    """Return the schema of the step `filters`.

    The keyword lists and the severity minimum are only shown when the
    source has details (provider other than `none`).
    """
    fields: dict[Any, Any] = {
        vol.Required(CONF_DETAILS, default=DETAILS_AUTO): _select(
            list(DETAILS_MODES), CONF_DETAILS
        ),
    }
    if with_filters:
        fields[vol.Optional(SECTION_KEYWORDS)] = _section(
            {vol.Optional(key): KEYWORDS_SELECTOR for key in KEYWORD_KEYS}
        )
        fields[vol.Required(CONF_MIN_SEVERITY, default=MIN_SEVERITY_NONE)] = _select(
            SEVERITY_OPTIONS, "severity"
        )
    return vol.Schema(fields)


def _keywords(value: Any) -> list[str]:
    """Return a keyword list without empty entries."""
    if not value:
        return []
    items = [value] if isinstance(value, str) else value
    return [str(item).strip() for item in items if str(item).strip()]


def _validate_keywords(values: Mapping[str, list[str]]) -> str | None:
    """Return `invalid_regex` or `keyword_conflict`, or None if the lists are fine."""
    if any(is_invalid_regex(item) for key in KEYWORD_KEYS for item in values[key]):
        return "invalid_regex"
    include = {
        item.casefold()
        for key in (CONF_HEADLINE_KEYWORDS, CONF_TEXT_KEYWORDS)
        for item in values[key]
    }
    if include & {item.casefold() for item in values[CONF_EXCLUDE_KEYWORDS]}:
        return "keyword_conflict"
    return None


def _effective_provider(details: str, detected: str) -> str:
    """Return the provider for the `details` option and the detected provider."""
    if details == DETAILS_NONE:
        return PROVIDER_NONE
    if details == DETAILS_ATTRIBUTES:
        return PROVIDER_ATTRIBUTES
    return detected


class WarningSourceFlow(ConfigSubentryFlow):
    """Add or reconfigure a warning source (BHV-40, BHV-45).

    Steps `user` (entity or device, optional name), `trigger` (only for
    `sensor`, `select`, `input_select`) and `filters` (details provider,
    keywords, severity). Subentry data: `source_entity` or `source_device`,
    `name`, `trigger` (+ `threshold` or `states`), `details`, the three keyword
    lists and `min_severity`. The unique id is the entity id or device id, the
    title is the name.
    """

    def __init__(self) -> None:
        """Initialize the flow."""
        self._data: dict[str, Any] = {}
        self._current: Mapping[str, Any] = {}
        self._filters_shown = False

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Choose the entity or device of a new warning source."""
        return await self._async_step_source("user", user_input)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Change an existing warning source (same steps, pre-filled)."""
        self._current = self._get_reconfigure_subentry().data
        return await self._async_step_source("reconfigure", user_input)

    # --- step `user` / `reconfigure` ----------------------------------------------

    def _unique_id(self) -> str:
        return str(self._data.get(CONF_SOURCE_ENTITY) or self._data[CONF_SOURCE_DEVICE])

    def _same_source(self) -> bool:
        """Return True if the source is the one being reconfigured."""
        return bool(self._current) and all(
            self._current.get(key) == self._data.get(key)
            for key in (CONF_SOURCE_ENTITY, CONF_SOURCE_DEVICE)
        )

    def _default_name(self) -> str:
        if entity_id := self._data.get(CONF_SOURCE_ENTITY):
            return _friendly_name(self.hass, entity_id)
        device_id = str(self._data[CONF_SOURCE_DEVICE])
        device = dr.async_get(self.hass).async_get(device_id)
        if device is None:
            return device_id
        return device.name_by_user or device.name or device_id

    async def _async_step_source(
        self, step_id: str, user_input: dict[str, Any] | None
    ) -> SubentryFlowResult:
        """Show or handle the source form of `user` and `reconfigure`."""
        errors: dict[str, str] = {}
        if user_input is not None:
            entity_id = user_input.get(CONF_SOURCE_ENTITY)
            device_id = user_input.get(CONF_SOURCE_DEVICE)
            if bool(entity_id) == bool(device_id):
                errors["base"] = "source_required"
            else:
                self._data = (
                    {CONF_SOURCE_ENTITY: entity_id}
                    if entity_id
                    else {CONF_SOURCE_DEVICE: device_id}
                )
                if self._duplicate(self._unique_id()):
                    return self.async_abort(reason="already_configured")
                self._data[CONF_NAME] = (
                    str(user_input.get(CONF_NAME) or "").strip() or self._default_name()
                )
                if entity_id and split_entity_id(entity_id)[0] in TRIGGER_DOMAINS:
                    return await self.async_step_trigger()
                self._data[CONF_TRIGGER] = TRIGGER_ON_OFF
                return await self.async_step_filters()
            suggested: Mapping[str, Any] = user_input
        else:
            suggested = {
                key: self._current[key]
                for key in (CONF_SOURCE_ENTITY, CONF_SOURCE_DEVICE, CONF_NAME)
                if key in self._current
            }
        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(
                WARNING_SOURCE_SCHEMA, suggested
            ),
            errors=errors,
        )

    def _duplicate(self, unique_id: str) -> bool:
        """Return True if another subentry has ``unique_id``.

        HA checks subentry unique ids across all subentry types of an entry
        (`config_entries._raise_if_subentry_unique_id_exists`).
        """
        own_id = (
            self._get_reconfigure_subentry().subentry_id
            if self.source == SOURCE_RECONFIGURE
            else None
        )
        return any(
            subentry.unique_id == unique_id and subentry.subentry_id != own_id
            for subentry in self._get_entry().subentries.values()
        )

    # --- step `trigger` ---------------------------------------------------------------

    async def async_step_trigger(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Choose a numeric threshold or a list of states (sensor, select)."""
        entity_id: str = self._data[CONF_SOURCE_ENTITY]
        schema = _trigger_schema(self.hass, entity_id)
        errors: dict[str, str] = {}
        if user_input is not None:
            trigger = user_input[CONF_TRIGGER]
            threshold = user_input.get(CONF_THRESHOLD)
            states = _keywords(user_input.get(CONF_STATES))
            state = self.hass.states.get(entity_id)
            if trigger == TRIGGER_NUMERIC:
                if threshold is None:
                    errors[CONF_THRESHOLD] = "threshold_required"
                elif (
                    state is not None
                    and not is_unavailable(state.state)
                    and not _is_number(state.state)
                ):
                    errors[CONF_TRIGGER] = "numeric_state"
            elif not states:
                errors[CONF_STATES] = "states_required"
            if not errors:
                self._data[CONF_TRIGGER] = trigger
                if trigger == TRIGGER_NUMERIC:
                    self._data[CONF_THRESHOLD] = _normalize_number(
                        float(str(threshold))
                    )
                else:
                    self._data[CONF_STATES] = states
                return await self.async_step_filters()
            suggested: Mapping[str, Any] = user_input
        elif self._same_source() and self._current.get(CONF_TRIGGER) in (
            TRIGGER_NUMERIC,
            TRIGGER_STATE_LIST,
        ):
            suggested = {
                key: self._current[key]
                for key in (CONF_TRIGGER, CONF_THRESHOLD, CONF_STATES)
                if key in self._current
            }
        else:
            state = self.hass.states.get(entity_id)
            numeric = (
                split_entity_id(entity_id)[0] == "sensor"
                and state is not None
                and _is_number(state.state)
            )
            suggested = {
                CONF_TRIGGER: TRIGGER_NUMERIC if numeric else TRIGGER_STATE_LIST
            }
        return self.async_show_form(
            step_id="trigger",
            data_schema=self.add_suggested_values_to_schema(schema, suggested),
            errors=errors,
            description_placeholders={"entity": _friendly_name(self.hass, entity_id)},
        )

    # --- step `filters` ---------------------------------------------------------------

    def _detected_provider(self) -> str:
        if entity_id := self._data.get(CONF_SOURCE_ENTITY):
            return detect_provider(self.hass, [entity_id])
        return detect_provider(
            self.hass, device_entities(self.hass, self._data[CONF_SOURCE_DEVICE])
        )

    async def _provider_label(self, provider: str) -> str:
        """Return the translated name of a provider (server language)."""
        translations = await async_get_translations(
            self.hass, self.hass.config.language, "common", [DOMAIN]
        )
        return translations.get(
            f"component.{DOMAIN}.common.provider_{provider}", provider
        )

    def _suggested_filters(self, provider: str) -> dict[str, Any]:
        """Return the pre-filled filter values (stored ones on reconfigure)."""
        if self._same_source() and CONF_DETAILS in self._current:
            stored: Mapping[str, Any] = self._current
        else:
            lists = suggested_filters(provider, self.hass.config.language) or {}
            stored = {key: list(lists.get(key, ())) for key in KEYWORD_KEYS}
        return {
            CONF_DETAILS: stored.get(CONF_DETAILS, DETAILS_AUTO),
            SECTION_KEYWORDS: {
                key: list(stored[key]) for key in KEYWORD_KEYS if stored.get(key)
            },
            CONF_MIN_SEVERITY: stored.get(CONF_MIN_SEVERITY, MIN_SEVERITY_NONE),
        }

    async def async_step_filters(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Choose the details provider and the filters, then save the source.

        The keyword lists are pre-filled per provider and server language
        (`engine.safety.suggested_filters`). For provider `none` they are not
        shown; choosing a provider with details shows them first.
        """
        detected = self._detected_provider()
        errors: dict[str, str] = {}
        suggested: dict[str, Any]
        if user_input is None:
            suggested = self._suggested_filters(detected)
            provider = _effective_provider(suggested[CONF_DETAILS], detected)
        else:
            details = user_input[CONF_DETAILS]
            provider = _effective_provider(details, detected)
            if provider != PROVIDER_NONE and not self._filters_shown:
                # The filters were hidden (provider `none`): show them first.
                suggested = self._suggested_filters(provider)
                suggested[CONF_DETAILS] = details
            else:
                section_input = user_input.get(SECTION_KEYWORDS) or {}
                values = {
                    key: _keywords(section_input.get(key)) for key in KEYWORD_KEYS
                }
                min_severity = user_input.get(CONF_MIN_SEVERITY, MIN_SEVERITY_NONE)
                if provider == PROVIDER_NONE:
                    values = {key: [] for key in KEYWORD_KEYS}
                    min_severity = MIN_SEVERITY_NONE
                if error := _validate_keywords(values):
                    errors[SECTION_KEYWORDS] = error
                    suggested = dict(user_input)
                else:
                    return self._async_save(
                        {
                            **self._data,
                            CONF_DETAILS: details,
                            **values,
                            CONF_MIN_SEVERITY: min_severity,
                        }
                    )
        with_filters = provider != PROVIDER_NONE
        self._filters_shown = with_filters
        return self.async_show_form(
            step_id="filters",
            data_schema=self.add_suggested_values_to_schema(
                _filters_schema(with_filters=with_filters), suggested
            ),
            errors=errors,
            description_placeholders={
                "name": self._data[CONF_NAME],
                "provider": await self._provider_label(detected),
            },
        )

    def _async_save(self, data: dict[str, Any]) -> SubentryFlowResult:
        """Create the subentry or update the reconfigured one."""
        title = data[CONF_NAME]
        unique_id = self._unique_id()
        if self.source == SOURCE_RECONFIGURE:
            return self.async_update_and_abort(
                self._get_entry(),
                self._get_reconfigure_subentry(),
                title=title,
                unique_id=unique_id,
                data=data,
            )
        return self.async_create_entry(title=title, data=data, unique_id=unique_id)
