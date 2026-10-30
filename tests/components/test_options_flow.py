"""Options flow (C3): docs/spec/01-configuration.md, BHV-53."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock, patch

from homeassistant.config_entries import ConfigEntryState, ConfigFlowResult
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)

from custom_components.better_home_ventilation.const import (
    ACTUATOR_UNIT_FLOW,
    ACTUATOR_UNIT_PERCENT,
    CONF_ACTUATOR,
    CONF_ACTUATOR_MAX,
    CONF_ACTUATOR_MIN,
    CONF_ACTUATOR_UNIT,
    CONF_AUTO_BOOST_COOLDOWN,
    CONF_AUTO_BOOST_QUIET_CAP,
    CONF_AWAY_ENABLED,
    CONF_BATHROOM_HUMIDITY_SENSORS,
    CONF_BOOST_DEFAULT_DURATION,
    CONF_BOOST_DEFAULT_LEVEL,
    CONF_BOOST_MAX_DURATION,
    CONF_CO2_END,
    CONF_CO2_SENSORS,
    CONF_CO2_START,
    CONF_COOKING_HIGHER_IS_WORSE,
    CONF_COOKING_LEVEL,
    CONF_COOKING_SENSORS,
    CONF_COOKING_TRIGGER,
    CONF_COOKING_TRIGGER_RUN_ON,
    CONF_DEADBAND_DOWN,
    CONF_DRYNESS_HYSTERESIS,
    CONF_DRYNESS_THRESHOLD,
    CONF_FALLBACK_FLOW,
    CONF_HUMIDITY_HIGH_END,
    CONF_HUMIDITY_HIGH_START,
    CONF_HUMIDITY_SENSORS,
    CONF_MAX_FLOW,
    CONF_MIN_FLOW,
    CONF_MIN_WRITE_INTERVAL,
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
    CONF_QUIET_MAX_FLOW,
    CONF_RESTART_CONFIRM_TIMEOUT,
    CONF_SAFETY_WHEN_DISABLED,
    CONF_SELECT_MAPPING,
    CONF_SHOWER_LEVEL,
    CONF_SHOWER_RISE,
    CONF_SHOWER_WINDOW,
    CONF_STALE_TIMEOUT,
    DOMAIN,
    OFF_STATE_OFF,
    OFF_STATE_ON,
)

from .common import (
    BATHROOM_HUMIDITY,
    CO2_BEDROOM,
    CO2_LIVING,
    FAN,
    HOB_POWER,
    HUMIDITY_BEDROOM,
    HUMIDITY_LIVING,
    KITCHEN_VOC,
    NUMBER_FLOW,
    NUMBER_PERCENT,
    NUMBER_RAW,
    PERSON,
    SELECT_SINGLE,
    SELECT_STAGES,
    STAGE_OPTIONS,
    STANDBY_SWITCH,
    mock_entry,
    schema_fields,
    sections,
    suggested,
)


@pytest.fixture
def reload_spy(hass: HomeAssistant) -> Iterator[MagicMock]:
    """Spy on reloads scheduled by the update listener (the reload still runs)."""
    with patch.object(
        hass.config_entries,
        "async_schedule_reload",
        wraps=hass.config_entries.async_schedule_reload,
    ) as spy:
        yield spy


async def _open(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    step: str,
) -> ConfigFlowResult:
    """Open the options menu and choose ``step``."""
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": step}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == step
    return result


def _form_input(result: ConfigFlowResult, **changes: Any) -> dict[str, Any]:
    """Return the suggested values of a form as nested input, with changes."""
    # The frontend sends every section, empty or not.
    user_input: dict[str, Any] = {name: {} for name in sections(result["data_schema"])}
    for key, (section_name, marker, _) in schema_fields(result["data_schema"]).items():
        value = changes.get(key, (marker.description or {}).get("suggested_value"))
        if value is None:
            continue
        target = (
            user_input
            if section_name is None
            else user_input.setdefault(section_name, {})
        )
        target[key] = value
    return user_input


async def _submit(
    hass: HomeAssistant, result: ConfigFlowResult, **changes: Any
) -> ConfigFlowResult:
    """Submit a form with its suggested values and ``changes``."""
    return await hass.config_entries.options.async_configure(
        result["flow_id"], _form_input(result, **changes)
    )


async def _assert_saved_and_reloaded(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    result: ConfigFlowResult,
    reload_spy: MagicMock,
) -> None:
    """Assert the options were saved and the update listener reloaded the entry."""
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options == result["data"]
    reload_spy.assert_called_once_with(entry.entry_id)
    assert entry.state is ConfigEntryState.LOADED


async def test_menu(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """The options flow starts with a menu of all steps (safety from 0.2)."""
    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == [
        "actuator",
        "inputs",
        "control",
        "boosts",
        "safety",
        "notifications",
    ]


# --- actuator ---------------------------------------------------------------------


async def test_actuator_form_shows_current_values_and_hint(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """The actuator step is pre-filled and shows the max-flow hint."""
    result = await _open(hass, entry, "actuator")

    schema = result["data_schema"]
    assert suggested(schema, CONF_ACTUATOR) == NUMBER_FLOW
    assert suggested(schema, CONF_MAX_FLOW) == 400
    assert suggested(schema, CONF_OFF_STATE) == OFF_STATE_ON
    assert suggested(schema, CONF_OFF_ENTITY) is None
    assert sections(schema)["off"].options == {"collapsed": False}
    assert sections(schema)["range"].options == {"collapsed": True}
    assert result["description_placeholders"] == {
        "max_flow": "400",
        "min_flow": "80",
        "fallback_flow": "150",
        "quiet_max_flow": "250",
        "boost_default_level": "300",
        "shower_level": "250",
        "cooking_level": "250",
        "auto_boost_quiet_cap": "200",
    }


async def test_actuator_save_flattens_sections(
    hass: HomeAssistant, entry: MockConfigEntry, reload_spy: MagicMock
) -> None:
    """Section input is stored flat; other options are kept (BHV-53)."""
    result = await _open(hass, entry, "actuator")

    result = await _submit(
        hass,
        result,
        **{
            CONF_MAX_FLOW: 350.0,
            CONF_OFF_ENTITY: STANDBY_SWITCH,
            CONF_OFF_STATE: OFF_STATE_OFF,
            CONF_ACTUATOR_MIN: 20,
            CONF_ACTUATOR_MAX: 380,
        },
    )

    await _assert_saved_and_reloaded(hass, entry, result, reload_spy)
    assert "off" not in entry.options
    assert "range" not in entry.options
    assert entry.options[CONF_MAX_FLOW] == 350
    assert isinstance(entry.options[CONF_MAX_FLOW], int)
    assert entry.options[CONF_OFF_ENTITY] == STANDBY_SWITCH
    assert entry.options[CONF_OFF_STATE] == OFF_STATE_OFF
    assert entry.options[CONF_ACTUATOR_MIN] == 20
    assert entry.options[CONF_ACTUATOR_MAX] == 380
    assert entry.options[CONF_ACTUATOR_UNIT] == ACTUATOR_UNIT_FLOW
    # Defaults relative to max flow do not follow a change of the max flow.
    assert entry.options[CONF_MIN_FLOW] == 80


async def test_actuator_cleared_optional_field_is_removed(
    hass: HomeAssistant, entities: None
) -> None:
    """Clearing the off actuator removes it from the options."""
    entry = mock_entry(**{CONF_OFF_ENTITY: STANDBY_SWITCH})
    entry.add_to_hass(hass)
    result = await _open(hass, entry, "actuator")
    assert suggested(result["data_schema"], CONF_OFF_ENTITY) == STANDBY_SWITCH

    user_input = _form_input(result)
    del user_input["off"][CONF_OFF_ENTITY]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert CONF_OFF_ENTITY not in entry.options


@pytest.mark.parametrize(
    ("actuator", "error"),
    [
        ("number.missing", "actuator_not_found"),
        (SELECT_SINGLE, "select_options"),
        ("number.other_unit", "actuator_in_use"),
    ],
)
async def test_actuator_errors(
    hass: HomeAssistant, entry: MockConfigEntry, actuator: str, error: str
) -> None:
    """Invalid or used actuators are rejected on the actuator field."""
    hass.states.async_set("number.other_unit", "100", {"unit_of_measurement": "m³/h"})
    mock_entry(**{CONF_ACTUATOR: "number.other_unit"}).add_to_hass(hass)
    result = await _open(hass, entry, "actuator")

    result = await _submit(hass, result, **{CONF_ACTUATOR: actuator})

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_ACTUATOR: error}
    assert suggested(result["data_schema"], CONF_ACTUATOR) == actuator


@pytest.mark.parametrize(
    ("actuator", "off_entity", "error"),
    [
        (NUMBER_FLOW, "switch.missing_standby", "off_entity_not_found"),
        (FAN, FAN, "off_entity_is_actuator"),
    ],
)
async def test_actuator_off_entity_errors(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    actuator: str,
    off_entity: str,
    error: str,
) -> None:
    """The off actuator must exist and differ from the actuator (BHV-02)."""
    result = await _open(hass, entry, "actuator")

    result = await _submit(
        hass, result, **{CONF_ACTUATOR: actuator, CONF_OFF_ENTITY: off_entity}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"off": error}
    assert CONF_OFF_ENTITY not in entry.options


async def test_actuator_change_to_percent_number(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """Changing to a number in % stores the unit without a mapping step."""
    result = await _open(hass, entry, "actuator")

    result = await _submit(hass, result, **{CONF_ACTUATOR: NUMBER_PERCENT})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_ACTUATOR] == NUMBER_PERCENT
    assert entry.options[CONF_ACTUATOR_UNIT] == ACTUATOR_UNIT_PERCENT


async def test_actuator_change_to_fan_drops_mapping(
    hass: HomeAssistant, entities: None
) -> None:
    """Changing from a select to a fan removes the select mapping."""
    entry = mock_entry(
        CONF_ACTUATOR_UNIT,
        **{
            CONF_ACTUATOR: SELECT_STAGES,
            CONF_SELECT_MAPPING: {"Off": 0, "Stage 1": 100},
        },
    )
    entry.add_to_hass(hass)
    result = await _open(hass, entry, "actuator")

    result = await _submit(hass, result, **{CONF_ACTUATOR: FAN})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_ACTUATOR] == FAN
    assert CONF_SELECT_MAPPING not in entry.options
    assert CONF_ACTUATOR_UNIT not in entry.options


async def test_actuator_change_to_select_continues_to_mapping(
    hass: HomeAssistant, entry: MockConfigEntry, reload_spy: MagicMock
) -> None:
    """Changing to a select asks for the mapping (empty), then saves it."""
    result = await _open(hass, entry, "actuator")

    result = await _submit(hass, result, **{CONF_ACTUATOR: SELECT_STAGES})
    assert result["step_id"] == "actuator_mapping"
    assert list(schema_fields(result["data_schema"])) == STAGE_OPTIONS
    assert all(suggested(result["data_schema"], key) is None for key in STAGE_OPTIONS)

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"Stage 1": 100}
    )
    assert result["step_id"] == "actuator_mapping"
    assert result["errors"] == {"base": "select_mapping"}
    assert suggested(result["data_schema"], "Stage 1") == 100

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"Stage 1": 100, "Stage 3": 300}
    )
    await _assert_saved_and_reloaded(hass, entry, result, reload_spy)
    assert entry.options[CONF_ACTUATOR] == SELECT_STAGES
    assert entry.options[CONF_SELECT_MAPPING] == {"Stage 1": 100, "Stage 3": 300}
    assert CONF_ACTUATOR_UNIT not in entry.options


async def test_actuator_kept_select_mapping_is_prefilled(
    hass: HomeAssistant, entities: None
) -> None:
    """A kept select shows its stored mapping for editing."""
    entry = mock_entry(
        **{
            CONF_ACTUATOR: SELECT_STAGES,
            CONF_SELECT_MAPPING: {"Off": 0, "Stage 2": 200, "Removed stage": 50},
        }
    )
    entry.add_to_hass(hass)
    result = await _open(hass, entry, "actuator")

    result = await _submit(hass, result)

    assert result["step_id"] == "actuator_mapping"
    assert suggested(result["data_schema"], "Off") == 0
    assert suggested(result["data_schema"], "Stage 2") == 200
    assert "Removed stage" not in schema_fields(result["data_schema"])


@pytest.mark.parametrize(
    ("stored_unit", "expected_suggestion"),
    [(ACTUATOR_UNIT_PERCENT, ACTUATOR_UNIT_PERCENT), (None, None)],
)
async def test_actuator_number_unknown_unit(
    hass: HomeAssistant,
    entities: None,
    stored_unit: str | None,
    expected_suggestion: str | None,
) -> None:
    """A number with unknown unit asks for the unit, pre-filled when kept."""
    if stored_unit is None:
        entry = mock_entry(CONF_ACTUATOR_UNIT, **{CONF_ACTUATOR: NUMBER_RAW})
    else:
        entry = mock_entry(
            **{CONF_ACTUATOR: NUMBER_RAW, CONF_ACTUATOR_UNIT: stored_unit}
        )
    entry.add_to_hass(hass)
    result = await _open(hass, entry, "actuator")

    result = await _submit(hass, result)
    assert result["step_id"] == "actuator_mapping"
    assert suggested(result["data_schema"], CONF_ACTUATOR_UNIT) == expected_suggestion

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_ACTUATOR_UNIT: ACTUATOR_UNIT_FLOW}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_ACTUATOR_UNIT] == ACTUATOR_UNIT_FLOW


# --- inputs -----------------------------------------------------------------------


async def test_inputs_save(
    hass: HomeAssistant, entry: MockConfigEntry, reload_spy: MagicMock
) -> None:
    """The inputs step is pre-filled and stores sections flat."""
    result = await _open(hass, entry, "inputs")
    schema = result["data_schema"]
    assert set(sections(schema)) == {"rooms", "presence"}
    assert suggested(schema, CONF_CO2_SENSORS) == [CO2_LIVING]
    assert suggested(schema, CONF_HUMIDITY_SENSORS) == [HUMIDITY_LIVING]
    assert "actual_flow_sensor" not in schema_fields(schema)

    result = await _submit(
        hass,
        result,
        **{
            CONF_CO2_SENSORS: [CO2_LIVING, CO2_BEDROOM],
            CONF_HUMIDITY_SENSORS: [HUMIDITY_LIVING, HUMIDITY_BEDROOM],
            CONF_PRESENCE_ENTITIES: [PERSON],
        },
    )

    await _assert_saved_and_reloaded(hass, entry, result, reload_spy)
    assert entry.options[CONF_CO2_SENSORS] == [CO2_LIVING, CO2_BEDROOM]
    assert entry.options[CONF_PRESENCE_ENTITIES] == [PERSON]
    assert "rooms" not in entry.options


@pytest.mark.parametrize(
    ("changes", "errors"),
    [
        (
            {CONF_CO2_SENSORS: [], CONF_HUMIDITY_SENSORS: []},
            {"base": "no_inputs"},
        ),
        ({CONF_CO2_SENSORS: ["sensor.missing_co2"]}, {"rooms": "sensor_not_found"}),
        ({CONF_HUMIDITY_SENSORS: [CO2_LIVING]}, {"rooms": "duplicate_sensor"}),
    ],
)
async def test_inputs_errors(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    changes: dict[str, Any],
    errors: dict[str, str],
) -> None:
    """Input errors of fields in a section are reported on the section."""
    result = await _open(hass, entry, "inputs")

    result = await _submit(hass, result, **changes)

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == errors


# --- control ----------------------------------------------------------------------


async def test_control_save(
    hass: HomeAssistant, entry: MockConfigEntry, reload_spy: MagicMock
) -> None:
    """All control sections are shown; `shaping` and `advanced` collapsed.

    Advanced mode is not used: HA deprecated it in 2026.10 (breaks in 2027.6).
    Away without presence entities is valid and simply has no effect.
    """
    result = await _open(hass, entry, "control")
    schema = result["data_schema"]
    assert list(sections(schema)) == [
        "co2",
        "flows",
        "humidity",
        "quiet",
        "away",
        "shaping",
        "advanced",
    ]
    assert sections(schema)["co2"].options == {"collapsed": False}
    assert sections(schema)["shaping"].options == {"collapsed": True}
    assert sections(schema)["advanced"].options == {"collapsed": True}
    assert suggested(schema, CONF_MIN_WRITE_INTERVAL) == {
        "hours": 0,
        "minutes": 5,
        "seconds": 0,
    }
    assert suggested(schema, CONF_PROBLEM_DELAY) == {
        "hours": 0,
        "minutes": 10,
        "seconds": 0,
    }
    assert suggested(schema, CONF_NIGHT_START) == "22:00:00"
    assert suggested(schema, CONF_AWAY_ENABLED) is True
    assert entry.options[CONF_PRESENCE_ENTITIES] == []

    result = await _submit(
        hass,
        result,
        **{
            CONF_CO2_START: 800.0,
            CONF_MIN_WRITE_INTERVAL: {"minutes": 10},
            CONF_DEADBAND_DOWN: 15,
            CONF_DRYNESS_HYSTERESIS: 2.5,
            CONF_PROBLEM_DELAY: {"minutes": 15},
        },
    )

    await _assert_saved_and_reloaded(hass, entry, result, reload_spy)
    assert entry.options[CONF_CO2_START] == 800
    assert entry.options[CONF_MIN_WRITE_INTERVAL] == 600
    assert entry.options[CONF_DEADBAND_DOWN] == 15
    assert entry.options[CONF_DRYNESS_HYSTERESIS] == 2.5
    assert entry.options[CONF_PROBLEM_DELAY] == 900
    assert entry.options[CONF_STALE_TIMEOUT] == 1800
    assert entry.options[CONF_AWAY_ENABLED] is True
    assert "co2" not in entry.options
    assert "advanced" not in entry.options


@pytest.mark.parametrize(
    ("changes", "errors"),
    [
        ({CONF_CO2_START: 1400, CONF_CO2_END: 1400}, {"co2": "co2_range"}),
        (
            {CONF_HUMIDITY_HIGH_START: 70, CONF_HUMIDITY_HIGH_END: 60},
            {"humidity": "humidity_range"},
        ),
        (
            {CONF_DRYNESS_THRESHOLD: 50, CONF_DRYNESS_HYSTERESIS: 5},
            {"humidity": "dryness_overlap"},
        ),
        ({CONF_FALLBACK_FLOW: 50}, {"flows": "fallback_range"}),
        ({CONF_QUIET_MAX_FLOW: 60}, {"quiet": "quiet_range"}),
        ({CONF_NIGHT_END: "22:00:00"}, {"quiet": "night_window"}),
    ],
)
async def test_control_errors(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    changes: dict[str, Any],
    errors: dict[str, str],
) -> None:
    """Every cross-field rule of the control step reports its error key."""
    result = await _open(hass, entry, "control")

    result = await _submit(hass, result, **changes)

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == errors
    assert entry.options[CONF_CO2_START] == 850


# --- boosts -----------------------------------------------------------------------


async def test_boosts_save(
    hass: HomeAssistant, entry: MockConfigEntry, reload_spy: MagicMock
) -> None:
    """The boosts step: manual boost, shower, cooking and the collapsed common part."""
    result = await _open(hass, entry, "boosts")
    schema = result["data_schema"]
    assert list(sections(schema)) == ["manual", "shower", "cooking", "common"]
    assert sections(schema)["common"].options == {"collapsed": True}
    assert sections(schema)["shower"].options == {"collapsed": False}
    assert suggested(schema, CONF_BOOST_DEFAULT_LEVEL) == 300
    assert suggested(schema, CONF_SHOWER_LEVEL) == 250
    assert suggested(schema, CONF_AUTO_BOOST_QUIET_CAP) == 200
    assert suggested(schema, CONF_BATHROOM_HUMIDITY_SENSORS) == []
    assert suggested(schema, CONF_COOKING_TRIGGER) is None
    assert suggested(schema, CONF_SHOWER_WINDOW) == {
        "hours": 0,
        "minutes": 10,
        "seconds": 0,
    }
    assert suggested(schema, CONF_BOOST_MAX_DURATION) == {
        "hours": 4,
        "minutes": 0,
        "seconds": 0,
    }

    result = await _submit(
        hass,
        result,
        **{CONF_BOOST_DEFAULT_LEVEL: 350, CONF_BOOST_DEFAULT_DURATION: {"hours": 1}},
    )

    await _assert_saved_and_reloaded(hass, entry, result, reload_spy)
    assert entry.options[CONF_BOOST_DEFAULT_LEVEL] == 350
    assert entry.options[CONF_BOOST_DEFAULT_DURATION] == 3600


async def test_boosts_level_range(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """A boost level below the minimum flow is rejected."""
    result = await _open(hass, entry, "boosts")

    result = await _submit(hass, result, **{CONF_BOOST_DEFAULT_LEVEL: 50})

    assert result["errors"] == {"manual": "level_range"}
    assert suggested(result["data_schema"], CONF_BOOST_DEFAULT_LEVEL) == 50


@pytest.mark.parametrize(
    ("key", "value", "section"),
    [
        (CONF_SHOWER_LEVEL, 75, "shower"),
        (CONF_COOKING_LEVEL, 60, "cooking"),
        (CONF_AUTO_BOOST_QUIET_CAP, 50, "common"),
    ],
)
async def test_auto_boost_level_range(
    hass: HomeAssistant, entry: MockConfigEntry, key: str, value: int, section: str
) -> None:
    """Auto boost levels and the quiet cap lie within min ... max flow."""
    result = await _open(hass, entry, "boosts")
    result = await _submit(hass, result, **{key: value})
    assert result["errors"] == {section: "level_range"}


async def test_bhv30_boosts_save_detectors(
    hass: HomeAssistant, entry: MockConfigEntry, reload_spy: MagicMock
) -> None:
    """Detector sensors, the trigger and tuning values are stored flat."""
    result = await _open(hass, entry, "boosts")
    result = await _submit(
        hass,
        result,
        **{
            CONF_BATHROOM_HUMIDITY_SENSORS: [BATHROOM_HUMIDITY],
            CONF_SHOWER_RISE: 6.5,
            CONF_SHOWER_WINDOW: {"minutes": 15},
            CONF_COOKING_SENSORS: [KITCHEN_VOC],
            CONF_COOKING_HIGHER_IS_WORSE: False,
            CONF_COOKING_TRIGGER: HOB_POWER,
            CONF_COOKING_TRIGGER_RUN_ON: {"minutes": 20},
            CONF_AUTO_BOOST_COOLDOWN: {"minutes": 5},
        },
    )
    await _assert_saved_and_reloaded(hass, entry, result, reload_spy)
    options = entry.options
    assert options[CONF_BATHROOM_HUMIDITY_SENSORS] == [BATHROOM_HUMIDITY]
    assert options[CONF_SHOWER_RISE] == 6.5
    assert options[CONF_SHOWER_WINDOW] == 900
    assert options[CONF_COOKING_SENSORS] == [KITCHEN_VOC]
    assert options[CONF_COOKING_HIGHER_IS_WORSE] is False
    assert options[CONF_COOKING_TRIGGER] == HOB_POWER
    assert options[CONF_COOKING_TRIGGER_RUN_ON] == 1200
    assert options[CONF_AUTO_BOOST_COOLDOWN] == 300
    assert "shower" not in options


async def test_boosts_clearing_the_trigger_removes_it(
    hass: HomeAssistant, entities: None
) -> None:
    """A cleared trigger entity is removed from the options."""
    entry = mock_entry(**{CONF_COOKING_TRIGGER: HOB_POWER})
    entry.add_to_hass(hass)
    result = await _open(hass, entry, "boosts")
    assert suggested(result["data_schema"], CONF_COOKING_TRIGGER) == HOB_POWER
    user_input = _form_input(result)
    del user_input["cooking"][CONF_COOKING_TRIGGER]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert CONF_COOKING_TRIGGER not in entry.options


@pytest.mark.parametrize(
    ("changes", "errors"),
    [
        (
            {CONF_BATHROOM_HUMIDITY_SENSORS: [HUMIDITY_LIVING]},
            {"shower": "duplicate_sensor"},
        ),
        ({CONF_COOKING_SENSORS: [CO2_LIVING]}, {"cooking": "duplicate_sensor"}),
        (
            {
                CONF_BATHROOM_HUMIDITY_SENSORS: [BATHROOM_HUMIDITY],
                CONF_COOKING_SENSORS: [BATHROOM_HUMIDITY],
            },
            {"cooking": "duplicate_sensor"},
        ),
        (
            {CONF_COOKING_SENSORS: [HOB_POWER], CONF_COOKING_TRIGGER: HOB_POWER},
            {"cooking": "duplicate_sensor"},
        ),
        (
            {CONF_BATHROOM_HUMIDITY_SENSORS: ["sensor.gone_humidity"]},
            {"shower": "sensor_not_found"},
        ),
        ({CONF_COOKING_TRIGGER: "sensor.gone_power"}, {"cooking": "sensor_not_found"}),
    ],
)
async def test_boosts_sensor_validation(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    changes: dict[str, Any],
    errors: dict[str, str],
) -> None:
    """A sensor in two input groups or one that does not exist is rejected."""
    result = await _open(hass, entry, "boosts")
    result = await _submit(hass, result, **changes)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == errors


async def test_inputs_duplicate_with_a_stored_bathroom_sensor(
    hass: HomeAssistant, entities: None
) -> None:
    """A room humidity sensor that is a bathroom sensor: the form field reports it."""
    entry = mock_entry(**{CONF_BATHROOM_HUMIDITY_SENSORS: [HUMIDITY_BEDROOM]})
    entry.add_to_hass(hass)
    result = await _open(hass, entry, "inputs")
    result = await _submit(
        hass, result, **{CONF_HUMIDITY_SENSORS: [HUMIDITY_LIVING, HUMIDITY_BEDROOM]}
    )
    assert result["errors"] == {"rooms": "duplicate_sensor"}


# --- safety and notifications (0.2) ------------------------------------------------


async def test_safety_save(
    hass: HomeAssistant, entry: MockConfigEntry, reload_spy: MagicMock
) -> None:
    """The safety step shows the stored values and links to the integration."""
    result = await _open(hass, entry, "safety")
    schema = result["data_schema"]
    assert suggested(schema, CONF_RESTART_CONFIRM_TIMEOUT) == {
        "hours": 0,
        "minutes": 30,
        "seconds": 0,
    }
    assert suggested(schema, CONF_SAFETY_WHEN_DISABLED) is True
    assert result["description_placeholders"] == {
        "integration_url": f"/config/integrations/integration/{DOMAIN}"
    }

    result = await _submit(
        hass,
        result,
        **{
            CONF_RESTART_CONFIRM_TIMEOUT: {"minutes": 0},
            CONF_SAFETY_WHEN_DISABLED: False,
        },
    )

    await _assert_saved_and_reloaded(hass, entry, result, reload_spy)
    assert entry.options[CONF_RESTART_CONFIRM_TIMEOUT] == 0
    assert entry.options[CONF_SAFETY_WHEN_DISABLED] is False


async def test_notifications_lists_mobile_app_services(
    hass: HomeAssistant, entry: MockConfigEntry, reload_spy: MagicMock
) -> None:
    """Only `notify.mobile_app_*` services; a missing stored one is dropped."""
    for service in ("mobile_app_example_tablet", "mobile_app_example_phone", "other"):
        async_mock_service(hass, "notify", service)
    hass.config_entries.async_update_entry(
        entry,
        options={
            **entry.options,
            CONF_NOTIFY_SERVICES: ["mobile_app_example_phone", "mobile_app_old_phone"],
        },
    )
    await hass.async_block_till_done()
    reload_spy.reset_mock()
    result = await _open(hass, entry, "notifications")
    schema = result["data_schema"]
    field = schema_fields(schema)[CONF_NOTIFY_SERVICES][2]
    assert field.config["multiple"] is True
    assert [option["value"] for option in field.config["options"]] == [
        "mobile_app_example_phone",
        "mobile_app_example_tablet",
    ]
    assert suggested(schema, CONF_NOTIFY_SERVICES) == ["mobile_app_example_phone"]
    for key in (CONF_NOTIFY_SAFETY, CONF_NOTIFY_REMINDERS, CONF_NOTIFY_PROBLEMS):
        assert suggested(schema, key) is True
    assert suggested(schema, CONF_PAUSE_REMINDER_AFTER) == {
        "hours": 8,
        "minutes": 0,
        "seconds": 0,
    }

    result = await _submit(
        hass,
        result,
        **{
            CONF_NOTIFY_SERVICES: [
                "mobile_app_example_phone",
                "mobile_app_example_tablet",
            ],
            CONF_NOTIFY_REMINDERS: False,
            CONF_OFF_NOTICE_AFTER: {"hours": 12},
        },
    )

    await _assert_saved_and_reloaded(hass, entry, result, reload_spy)
    assert entry.options[CONF_NOTIFY_SERVICES] == [
        "mobile_app_example_phone",
        "mobile_app_example_tablet",
    ]
    assert entry.options[CONF_NOTIFY_REMINDERS] is False
    assert entry.options[CONF_OFF_NOTICE_AFTER] == 12 * 3600
    assert entry.options[CONF_PAUSE_REMINDER_AFTER] == 8 * 3600


async def test_notifications_without_phones_and_validation(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """No phones: an empty list; reminders need at least one minute."""
    result = await _open(hass, entry, "notifications")
    field = schema_fields(result["data_schema"])[CONF_NOTIFY_SERVICES][2]
    assert field.config["options"] == []

    result = await _submit(
        hass,
        result,
        **{
            CONF_PAUSE_REMINDER_AFTER: {"seconds": 30},
            CONF_OFF_NOTICE_AFTER: {"minutes": 0},
        },
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {
        CONF_PAUSE_REMINDER_AFTER: "reminder_too_short",
        CONF_OFF_NOTICE_AFTER: "reminder_too_short",
    }

    result = await _submit(hass, result, **{CONF_PAUSE_REMINDER_AFTER: {"minutes": 1}})
    assert result["errors"] == {CONF_OFF_NOTICE_AFTER: "reminder_too_short"}

    result = await _submit(hass, result, **{CONF_OFF_NOTICE_AFTER: {"hours": 1}})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_NOTIFY_SERVICES] == []
    assert entry.options[CONF_PAUSE_REMINDER_AFTER] == 60


async def test_options_of_other_entries_are_untouched(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """Saving one entry does not change another entry."""
    other = mock_entry(**{CONF_ACTUATOR: FAN})
    other.add_to_hass(hass)
    before = dict(other.options)
    result = await _open(hass, entry, "boosts")

    await _submit(hass, result, **{CONF_BOOST_DEFAULT_LEVEL: 350})

    assert dict(other.options) == before
    assert len(hass.config_entries.async_entries(DOMAIN)) == 2
