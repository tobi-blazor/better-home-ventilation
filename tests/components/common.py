"""Shared constants and helpers of the component tests."""

from __future__ import annotations

from typing import Any

from homeassistant.data_entry_flow import section
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.better_home_ventilation.config_flow import (
    BetterHomeVentilationConfigFlow,
)
from custom_components.better_home_ventilation.const import (
    ACTUATOR_UNIT_FLOW,
    CONF_ACTUATOR,
    CONF_ACTUATOR_UNIT,
    CONF_CO2_SENSORS,
    CONF_HUMIDITY_SENSORS,
    CONF_MAX_FLOW,
    CONF_PRESENCE_ENTITIES,
    DEFAULTS,
    DOMAIN,
    defaults_for_max_flow,
)

# Neutral example entities (no real installation).
NUMBER_FLOW = "number.unit_flow"
NUMBER_PERCENT = "number.unit_percent"
NUMBER_RAW = "number.unit_raw"
FAN = "fan.unit"
SELECT_STAGES = "select.unit_stage"
SELECT_SINGLE = "select.unit_single"
CO2_LIVING = "sensor.living_room_co2"
CO2_BEDROOM = "sensor.bedroom_co2"
HUMIDITY_LIVING = "sensor.living_room_humidity"
HUMIDITY_BEDROOM = "sensor.bedroom_humidity"
PERSON = "person.resident"
STANDBY_SWITCH = "switch.unit_standby"
CONTROL_MODE = "select.unit_control_mode"

STAGE_OPTIONS = ["Off", "Stage 1", "Stage 2", "Stage 3"]


def entry_options(*remove: str, **overrides: Any) -> dict[str, Any]:
    """Return complete options of an entry as the config flow creates them."""
    options = {
        **DEFAULTS,
        **defaults_for_max_flow(400),
        CONF_ACTUATOR: NUMBER_FLOW,
        CONF_MAX_FLOW: 400,
        CONF_ACTUATOR_UNIT: ACTUATOR_UNIT_FLOW,
        CONF_CO2_SENSORS: [CO2_LIVING],
        CONF_HUMIDITY_SENSORS: [HUMIDITY_LIVING],
        CONF_PRESENCE_ENTITIES: [],
        **overrides,
    }
    for key in remove:
        del options[key]
    return options


def mock_entry(*remove: str, **overrides: Any) -> MockConfigEntry:
    """Return a config entry of the current version with complete options."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Ventilation",
        data={},
        options=entry_options(*remove, **overrides),
        version=BetterHomeVentilationConfigFlow.VERSION,
        minor_version=BetterHomeVentilationConfigFlow.MINOR_VERSION,
    )


def schema_fields(schema: Any) -> dict[str, tuple[str | None, Any, Any]]:
    """Return every field of a form schema: key -> (section, marker, selector)."""
    fields: dict[str, tuple[str | None, Any, Any]] = {}
    for marker, value in schema.schema.items():
        if isinstance(value, section):
            for inner, inner_value in value.schema.schema.items():
                fields[str(inner.schema)] = (str(marker.schema), inner, inner_value)
        else:
            fields[str(marker.schema)] = (None, marker, value)
    return fields


def suggested(schema: Any, key: str) -> Any:
    """Return the suggested value of a field of a form schema."""
    marker = schema_fields(schema)[key][1]
    return (marker.description or {}).get("suggested_value")


def sections(schema: Any) -> dict[str, Any]:
    """Return the sections of a form schema by name."""
    return {
        str(marker.schema): value
        for marker, value in schema.schema.items()
        if isinstance(value, section)
    }
