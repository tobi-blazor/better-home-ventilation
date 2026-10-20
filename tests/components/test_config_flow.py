"""Config flow (C1, C2): docs/spec/01-configuration.md, BHV-01, BHV-53."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import SOURCE_USER, ConfigFlowResult
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
import pytest

from custom_components.better_home_ventilation.const import (
    ACTUATOR_UNIT_FLOW,
    ACTUATOR_UNIT_PERCENT,
    CONF_ACTUATOR,
    CONF_ACTUATOR_UNIT,
    CONF_CO2_SENSORS,
    CONF_HUMIDITY_SENSORS,
    CONF_MAX_FLOW,
    CONF_NAME,
    CONF_OFF_ENTITY,
    CONF_OFF_STATE,
    CONF_PRESENCE_ENTITIES,
    CONF_SELECT_MAPPING,
    DEFAULTS,
    DOMAIN,
    OFF_STATE_OFF,
    OFF_STATE_ON,
    defaults_for_max_flow,
)

from .common import (
    CO2_BEDROOM,
    CO2_LIVING,
    FAN,
    HUMIDITY_LIVING,
    NUMBER_FLOW,
    NUMBER_PERCENT,
    NUMBER_RAW,
    SELECT_SINGLE,
    SELECT_STAGES,
    STAGE_OPTIONS,
    STANDBY_SWITCH,
    mock_entry,
    schema_fields,
    sections,
)

pytestmark = pytest.mark.usefixtures("entities")


async def _start(hass: HomeAssistant) -> ConfigFlowResult:
    """Start the config flow."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    return result


async def _user(
    hass: HomeAssistant, actuator: str, **extra: Any
) -> tuple[str, ConfigFlowResult]:
    """Start the flow and submit the `user` step."""
    result = await _start(hass)
    flow_id = result["flow_id"]
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_NAME: "Ventilation", CONF_ACTUATOR: actuator, **extra}
    )
    return flow_id, result


async def _inputs(hass: HomeAssistant, flow_id: str, **inputs: Any) -> ConfigFlowResult:
    """Submit the `inputs` step."""
    return await hass.config_entries.flow.async_configure(
        flow_id, inputs or {CONF_CO2_SENSORS: [CO2_LIVING]}
    )


def _assert_created(result: ConfigFlowResult, max_flow: int) -> dict[str, Any]:
    """Assert the entry was created with empty data and all defaults."""
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Ventilation"
    assert result["data"] == {}
    options = dict(result["options"])
    for key, value in {**DEFAULTS, **defaults_for_max_flow(max_flow)}.items():
        assert options[key] == value, key
    assert options[CONF_MAX_FLOW] == max_flow
    assert options[CONF_PRESENCE_ENTITIES] == []
    return options


async def test_bhv01_number_flow_takes_max_flow_from_actuator(
    hass: HomeAssistant,
) -> None:
    """A number in m³/h skips the actuator step; max flow comes from `max`."""
    flow_id, result = await _user(hass, NUMBER_FLOW)
    assert result["step_id"] == "inputs"

    result = await _inputs(
        hass,
        flow_id,
        **{CONF_CO2_SENSORS: [CO2_LIVING], CONF_HUMIDITY_SENSORS: [HUMIDITY_LIVING]},
    )

    options = _assert_created(result, 400)
    assert options[CONF_ACTUATOR] == NUMBER_FLOW
    assert options[CONF_ACTUATOR_UNIT] == ACTUATOR_UNIT_FLOW
    assert options[CONF_CO2_SENSORS] == [CO2_LIVING]
    assert options[CONF_HUMIDITY_SENSORS] == [HUMIDITY_LIVING]
    assert CONF_SELECT_MAPPING not in options
    assert isinstance(options[CONF_MAX_FLOW], int)
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.data == {}
    assert entry.title == "Ventilation"


async def test_bhv01_number_percent(hass: HomeAssistant) -> None:
    """A number in % skips the actuator step and stores `percent`."""
    flow_id, result = await _user(hass, NUMBER_PERCENT, **{CONF_MAX_FLOW: 300})
    assert result["step_id"] == "inputs"

    options = _assert_created(await _inputs(hass, flow_id), 300)
    assert options[CONF_ACTUATOR_UNIT] == ACTUATOR_UNIT_PERCENT


async def test_bhv01_number_unknown_unit_asks_for_unit(hass: HomeAssistant) -> None:
    """A number without a known unit asks for the unit in the actuator step."""
    flow_id, result = await _user(hass, NUMBER_RAW, **{CONF_MAX_FLOW: 350})
    assert result["step_id"] == "actuator"
    assert list(schema_fields(result["data_schema"])) == [CONF_ACTUATOR_UNIT]

    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_ACTUATOR_UNIT: ACTUATOR_UNIT_PERCENT}
    )
    assert result["step_id"] == "inputs"

    options = _assert_created(await _inputs(hass, flow_id), 350)
    assert options[CONF_ACTUATOR_UNIT] == ACTUATOR_UNIT_PERCENT


async def test_bhv01_fan(hass: HomeAssistant) -> None:
    """A fan skips the actuator step and stores no unit."""
    flow_id, result = await _user(hass, FAN, **{CONF_MAX_FLOW: 250})
    assert result["step_id"] == "inputs"

    options = _assert_created(await _inputs(hass, flow_id), 250)
    assert CONF_ACTUATOR_UNIT not in options
    assert CONF_SELECT_MAPPING not in options


async def test_bhv01_select_maps_options_to_flows(hass: HomeAssistant) -> None:
    """A select asks for one flow per option; unmapped options are left out."""
    flow_id, result = await _user(hass, SELECT_STAGES, **{CONF_MAX_FLOW: 400})
    assert result["step_id"] == "actuator"
    assert list(schema_fields(result["data_schema"])) == STAGE_OPTIONS

    result = await hass.config_entries.flow.async_configure(
        flow_id, {"Off": 0, "Stage 1": 100, "Stage 3": 300.0}
    )
    assert result["step_id"] == "inputs"

    options = _assert_created(await _inputs(hass, flow_id), 400)
    assert options[CONF_SELECT_MAPPING] == {"Off": 0, "Stage 1": 100, "Stage 3": 300}
    assert CONF_ACTUATOR_UNIT not in options


async def test_registry_only_actuator(
    hass: HomeAssistant, entity_registry: er.EntityRegistry
) -> None:
    """An actuator without state is classified from its registry entry."""
    entity_registry.async_get_or_create(
        "number",
        "test",
        "flow_register",
        suggested_object_id="registry_flow",
        unit_of_measurement="m³/h",
        capabilities={"max": 350},
    )
    flow_id, result = await _user(hass, "number.registry_flow")
    assert result["step_id"] == "inputs"

    options = _assert_created(await _inputs(hass, flow_id), 350)
    assert options[CONF_ACTUATOR_UNIT] == ACTUATOR_UNIT_FLOW


@pytest.mark.parametrize(
    "actuator",
    ["number.missing", "2b9e1c3d4f5a6b7c8d9e0f1a2b3c4d5e"],
)
async def test_actuator_not_found(hass: HomeAssistant, actuator: str) -> None:
    """An actuator without state and registry entry is rejected."""
    _, result = await _user(hass, actuator, **{CONF_MAX_FLOW: 400})

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_ACTUATOR: "actuator_not_found"}


async def test_select_options(hass: HomeAssistant) -> None:
    """A select with fewer than two options is rejected."""
    _, result = await _user(hass, SELECT_SINGLE, **{CONF_MAX_FLOW: 400})

    assert result["errors"] == {CONF_ACTUATOR: "select_options"}


@pytest.mark.parametrize(
    ("actuator", "attributes"),
    [
        (NUMBER_RAW, None),
        (FAN, None),
        (NUMBER_FLOW, {"unit_of_measurement": "m³/h", "max": "unknown"}),
        (NUMBER_FLOW, {"unit_of_measurement": "m³/h", "max": 5000}),
    ],
)
async def test_max_flow_required(
    hass: HomeAssistant, actuator: str, attributes: dict[str, Any] | None
) -> None:
    """Without a usable `max` attribute in m³/h the max flow must be entered."""
    if attributes is not None:
        hass.states.async_set(actuator, "100", attributes)
    flow_id, result = await _user(hass, actuator)

    assert result["errors"] == {CONF_MAX_FLOW: "max_flow_required"}

    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_NAME: "Ventilation", CONF_ACTUATOR: FAN, CONF_MAX_FLOW: 200}
    )
    assert result["step_id"] == "inputs"


@pytest.mark.parametrize(
    "mapping",
    [
        {"Stage 1": 100},
        {"Stage 1": 100, "Stage 2": 100},
        {},
    ],
)
async def test_select_mapping(hass: HomeAssistant, mapping: dict[str, int]) -> None:
    """At least two mapped options with distinct flows are needed."""
    flow_id, _ = await _user(hass, SELECT_STAGES, **{CONF_MAX_FLOW: 400})

    result = await hass.config_entries.flow.async_configure(flow_id, mapping)

    assert result["step_id"] == "actuator"
    assert result["errors"] == {"base": "select_mapping"}


async def test_no_inputs(hass: HomeAssistant) -> None:
    """At least one CO₂ or humidity sensor is needed."""
    flow_id, _ = await _user(hass, NUMBER_FLOW)

    result = await hass.config_entries.flow.async_configure(flow_id, {})

    assert result["step_id"] == "inputs"
    assert result["errors"] == {"base": "no_inputs"}


async def test_sensor_not_found(hass: HomeAssistant) -> None:
    """Room sensors must exist."""
    flow_id, _ = await _user(hass, NUMBER_FLOW)

    result = await _inputs(
        hass,
        flow_id,
        **{
            CONF_CO2_SENSORS: [CO2_LIVING, "sensor.missing_co2"],
            CONF_HUMIDITY_SENSORS: ["sensor.missing_humidity"],
        },
    )

    assert result["errors"] == {
        CONF_CO2_SENSORS: "sensor_not_found",
        CONF_HUMIDITY_SENSORS: "sensor_not_found",
    }


async def test_duplicate_sensor(hass: HomeAssistant) -> None:
    """A sensor cannot be used in two input groups."""
    flow_id, _ = await _user(hass, NUMBER_FLOW)

    result = await _inputs(
        hass,
        flow_id,
        **{
            CONF_CO2_SENSORS: [CO2_LIVING, CO2_BEDROOM],
            CONF_HUMIDITY_SENSORS: [CO2_BEDROOM],
        },
    )

    assert result["errors"] == {CONF_HUMIDITY_SENSORS: "duplicate_sensor"}


async def test_already_configured(hass: HomeAssistant) -> None:
    """An actuator controlled by another entry aborts (unique-config-entry)."""
    mock_entry().add_to_hass(hass)

    _, result = await _user(hass, NUMBER_FLOW)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_user_form_keeps_input_after_error(hass: HomeAssistant) -> None:
    """After an error the form is shown again with the entered values."""
    _, result = await _user(hass, "number.missing", **{CONF_MAX_FLOW: 300})

    fields = schema_fields(result["data_schema"])
    assert fields[CONF_ACTUATOR][1].description == {"suggested_value": "number.missing"}
    assert fields[CONF_MAX_FLOW][1].description == {"suggested_value": 300}


# --- Off actuator (BHV-02) ------------------------------------------------------------


async def test_bhv02_user_form_has_collapsed_off_section(hass: HomeAssistant) -> None:
    """The off actuator is offered at setup in a collapsed section."""
    result = await _start(hass)

    schema = result["data_schema"]
    assert sections(schema)["off"].options == {"collapsed": True}
    fields = schema_fields(schema)
    assert fields[CONF_OFF_ENTITY][0] == "off"
    assert fields[CONF_OFF_STATE][0] == "off"
    assert fields[CONF_OFF_STATE][1].default() == OFF_STATE_ON


@pytest.mark.parametrize("off_state", [OFF_STATE_ON, OFF_STATE_OFF])
async def test_bhv02_off_actuator_at_setup(hass: HomeAssistant, off_state: str) -> None:
    """An off actuator chosen at setup is stored flat in the options."""
    flow_id, result = await _user(
        hass,
        NUMBER_FLOW,
        off={CONF_OFF_ENTITY: STANDBY_SWITCH, CONF_OFF_STATE: off_state},
    )
    assert result["step_id"] == "inputs"

    result = await _inputs(hass, flow_id)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    options = dict(result["options"])
    assert options[CONF_OFF_ENTITY] == STANDBY_SWITCH
    assert options[CONF_OFF_STATE] == off_state
    assert "off" not in options


async def test_bhv02_off_actuator_kept_through_mapping_step(
    hass: HomeAssistant,
) -> None:
    """An off actuator entered with a select actuator survives the mapping step."""
    flow_id, _ = await _user(
        hass, SELECT_STAGES, **{CONF_MAX_FLOW: 400, "off": {CONF_OFF_ENTITY: FAN}}
    )
    result = await hass.config_entries.flow.async_configure(
        flow_id, {"Stage 1": 100, "Stage 3": 300}
    )
    assert result["step_id"] == "inputs"

    options = _assert_created(await _inputs(hass, flow_id), 400)
    assert options[CONF_OFF_ENTITY] == FAN
    assert options[CONF_OFF_STATE] == OFF_STATE_ON


@pytest.mark.parametrize("off", [None, {}, {CONF_OFF_STATE: OFF_STATE_ON}])
async def test_bhv02_no_off_actuator_at_setup(
    hass: HomeAssistant, off: dict[str, str] | None
) -> None:
    """Without an off actuator the key is absent (off = lowest actuator value)."""
    extra: dict[str, Any] = {} if off is None else {"off": off}
    flow_id, _ = await _user(hass, NUMBER_FLOW, **extra)

    options = _assert_created(await _inputs(hass, flow_id), 400)
    assert CONF_OFF_ENTITY not in options
    assert options[CONF_OFF_STATE] == OFF_STATE_ON
    assert "off" not in options


@pytest.mark.parametrize(
    ("actuator", "off_entity", "error"),
    [
        (NUMBER_FLOW, "switch.missing_standby", "off_entity_not_found"),
        (FAN, FAN, "off_entity_is_actuator"),
    ],
)
async def test_bhv02_off_actuator_errors(
    hass: HomeAssistant, actuator: str, off_entity: str, error: str
) -> None:
    """The off actuator must exist and differ from the actuator."""
    flow_id, result = await _user(
        hass, actuator, **{CONF_MAX_FLOW: 400, "off": {CONF_OFF_ENTITY: off_entity}}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"off": error}
    fields = schema_fields(result["data_schema"])
    assert fields[CONF_OFF_ENTITY][1].description == {"suggested_value": off_entity}

    result = await hass.config_entries.flow.async_configure(
        flow_id,
        {
            CONF_NAME: "Ventilation",
            CONF_ACTUATOR: actuator,
            CONF_MAX_FLOW: 400,
            "off": {CONF_OFF_ENTITY: STANDBY_SWITCH},
        },
    )
    assert result["step_id"] == "inputs"


async def test_bhv02_off_and_actuator_errors_together(hass: HomeAssistant) -> None:
    """Errors of the actuator and of the off actuator are shown at once."""
    _, result = await _user(
        hass,
        "number.missing",
        **{CONF_MAX_FLOW: 400, "off": {CONF_OFF_ENTITY: "switch.missing_standby"}},
    )

    assert result["errors"] == {
        CONF_ACTUATOR: "actuator_not_found",
        "off": "off_entity_not_found",
    }
