"""Entities of an entry (C5) and their behaviour (04-interface.md#entities)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import ATTR_ENTITY_ID, ATTR_FRIENDLY_NAME, EntityCategory
from homeassistant.core import (
    CoreState,
    Event,
    EventStateChangedData,
    HomeAssistant,
    State,
    callback,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    mock_restore_cache_with_extra_data,
)

from custom_components.better_home_ventilation.const import (
    DOMAIN,
    ENTITY_KEYS,
)
from custom_components.better_home_ventilation.engine.models import (
    Boost,
    Pause,
    Profile,
    Source,
)
from custom_components.better_home_ventilation.select import nearest_boost_duration
from custom_components.better_home_ventilation.sensor import SetpointSensor

from .common import CO2_LIVING, NUMBER_FLOW, MockUnit, advance, mock_entry
from .test_controller import controller_of, setup_entry

#: Entity ids and friendly names of the 0.1 entities per language (C5).
EXPECTED: dict[str, dict[str, tuple[str, str]]] = {
    "en": {
        "binary_sensor.problem": ("binary_sensor.ventilation_problem", "Problem"),
        "button.boost": ("button.ventilation_boost", "Boost"),
        "button.pause": ("button.ventilation_pause", "Pause"),
        "button.resume": ("button.ventilation_resume", "Resume"),
        "number.boost_level": ("number.ventilation_boost_level", "Boost level"),
        "select.profile": ("select.ventilation_profile", "Profile"),
        "select.boost_duration": (
            "select.ventilation_boost_duration",
            "Boost duration",
        ),
        "select.pause_duration": (
            "select.ventilation_pause_duration",
            "Pause duration",
        ),
        "sensor.setpoint": ("sensor.ventilation_setpoint", "Setpoint"),
        "sensor.reason": ("sensor.ventilation_reason", "Reason"),
        "sensor.mode": ("sensor.ventilation_mode", "Mode"),
        "sensor.ends_at": ("sensor.ventilation_ends_at", "Ends at"),
        "switch.control": ("switch.ventilation_control", "Control"),
    },
    "de": {
        "binary_sensor.problem": ("binary_sensor.ventilation_problem", "Problem"),
        "button.boost": ("button.ventilation_boost", "Boost"),
        "button.pause": ("button.ventilation_pause", "Pause"),
        "button.resume": ("button.ventilation_fortsetzen", "Fortsetzen"),
        "number.boost_level": ("number.ventilation_boost_stufe", "Boost-Stufe"),
        "select.profile": ("select.ventilation_profil", "Profil"),
        "select.boost_duration": ("select.ventilation_boost_dauer", "Boost-Dauer"),
        "select.pause_duration": ("select.ventilation_pausendauer", "Pausendauer"),
        "sensor.setpoint": ("sensor.ventilation_sollwert", "Sollwert"),
        "sensor.reason": ("sensor.ventilation_grund", "Grund"),
        "sensor.mode": ("sensor.ventilation_modus", "Modus"),
        "sensor.ends_at": ("sensor.ventilation_endet_um", "Endet um"),
        "switch.control": ("switch.ventilation_regelung", "Regelung"),
    },
}

CATEGORIES = {
    "binary_sensor.problem": EntityCategory.DIAGNOSTIC,
    "number.boost_level": EntityCategory.CONFIG,
    "select.boost_duration": EntityCategory.CONFIG,
    "select.pause_duration": EntityCategory.CONFIG,
}

SETPOINT = "sensor.ventilation_setpoint"
REASON = "sensor.ventilation_reason"
MODE = "sensor.ventilation_mode"
ENDS_AT = "sensor.ventilation_ends_at"
PROBLEM = "binary_sensor.ventilation_problem"
BOOST = "button.ventilation_boost"
PAUSE = "button.ventilation_pause"
RESUME = "button.ventilation_resume"
CONTROL = "switch.ventilation_control"
PROFILE = "select.ventilation_profile"
BOOST_DURATION = "select.ventilation_boost_duration"
PAUSE_DURATION = "select.ventilation_pause_duration"
BOOST_LEVEL = "number.ventilation_boost_level"


@pytest.fixture(autouse=True)
def daytime(freezer: FrozenDateTimeFactory) -> None:
    """Run every test at 13:00 local time, outside the night window."""
    freezer.move_to("2026-10-14T20:00:00+00:00")


def state_of(hass: HomeAssistant, entity_id: str) -> State:
    """Return the state of an entity, which must exist."""
    state = hass.states.get(entity_id)
    assert state is not None, entity_id
    return state


async def press(hass: HomeAssistant, entity_id: str) -> None:
    """Press a button."""
    await hass.services.async_call(
        "button", "press", {ATTR_ENTITY_ID: entity_id}, blocking=True
    )


async def select(hass: HomeAssistant, entity_id: str, option: str) -> None:
    """Select an option."""
    await hass.services.async_call(
        "select",
        "select_option",
        {ATTR_ENTITY_ID: entity_id, "option": option},
        blocking=True,
    )


async def set_number(hass: HomeAssistant, entity_id: str, value: float) -> None:
    """Set a number."""
    await hass.services.async_call(
        "number",
        "set_value",
        {ATTR_ENTITY_ID: entity_id, "value": value},
        blocking=True,
    )


# --- C5: entities, ids, names, categories ---------------------------------------


@pytest.mark.usefixtures("entities")
@pytest.mark.parametrize("language", ["en", "de"])
async def test_c5_entities_with_ids_and_translated_names(
    hass: HomeAssistant, unit: MockUnit, language: str
) -> None:
    """Setup creates every 0.1 entity with the expected id and name (C5)."""
    hass.config.language = language
    entry = await setup_entry(hass)
    registry = er.async_get(hass)
    device_id = controller_of(entry).device_id
    assert device_id is not None
    device = dr.async_get(hass).async_get(device_id)
    assert device is not None

    entries = er.async_entries_for_config_entry(registry, entry.entry_id)
    assert {e.unique_id for e in entries} == {
        f"{entry.entry_id}_{key}" for keys in ENTITY_KEYS.values() for key in keys
    }
    for platform_key, (entity_id, name) in EXPECTED[language].items():
        platform, key = platform_key.split(".")
        assert (
            registry.async_get_entity_id(platform, DOMAIN, f"{entry.entry_id}_{key}")
            == entity_id
        )
        registry_entry = registry.async_get(entity_id)
        assert registry_entry is not None
        assert registry_entry.translation_key == key
        assert registry_entry.has_entity_name
        assert registry_entry.device_id == device.id
        assert registry_entry.entity_category == CATEGORIES.get(platform_key)
        assert registry_entry.disabled_by is None
        state = state_of(hass, entity_id)
        assert state.attributes[ATTR_FRIENDLY_NAME] == f"Ventilation {name}"
        assert state.state != "unavailable"


@pytest.mark.usefixtures("entities")
async def test_sensor_device_classes_and_options(
    hass: HomeAssistant, unit: MockUnit
) -> None:
    """Device classes, units and enum options (04-interface.md#entities)."""
    await setup_entry(hass)
    setpoint = state_of(hass, SETPOINT)
    assert setpoint.attributes["device_class"] == "volume_flow_rate"
    assert setpoint.attributes["state_class"] == "measurement"
    assert setpoint.attributes["unit_of_measurement"] == "m³/h"
    reason = state_of(hass, REASON)
    assert reason.attributes["device_class"] == "enum"
    assert reason.attributes["options"] == [
        "minimum",
        "co2",
        "humidity_high",
        "dryness_protection",
        "away",
        "quiet_limit",
        "fallback",
        "boost",
        "shower",
        "cooking",
        "off",
        "safety_off",
    ]
    mode = state_of(hass, MODE)
    assert mode.attributes["device_class"] == "enum"
    assert mode.attributes["options"] == [
        "automatic",
        "fallback",
        "boost",
        "shower",
        "cooking",
        "off",
        "safety_off",
        "disabled",
    ]
    assert state_of(hass, ENDS_AT).attributes["device_class"] == "timestamp"
    assert state_of(hass, PROBLEM).attributes["device_class"] == "problem"
    assert state_of(hass, PROFILE).attributes["options"] == [
        "balanced",
        "air_quality",
        "quiet",
        "winter",
    ]
    assert state_of(hass, BOOST_DURATION).attributes["options"] == [
        "15",
        "30",
        "60",
        "120",
    ]
    assert state_of(hass, PAUSE_DURATION).attributes["options"] == [
        "1h",
        "4h",
        "8h",
        "indefinite",
    ]
    level = state_of(hass, BOOST_LEVEL)
    assert level.attributes["min"] == 80
    assert level.attributes["max"] == 400
    assert level.attributes["step"] == 5
    assert level.attributes["mode"] == "box"


@pytest.mark.usefixtures("entities")
async def test_stale_entities_are_removed(hass: HomeAssistant, unit: MockUnit) -> None:
    """Registry entries of features that are not created are removed on setup."""
    entry = mock_entry()
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    stale = registry.async_get_or_create(
        "binary_sensor",
        DOMAIN,
        f"{entry.entry_id}_safety_warning",
        config_entry=entry,
    )
    other = registry.async_get_or_create(
        "binary_sensor", "other", "safety_warning", config_entry=None
    )

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert registry.async_get(stale.entity_id) is None
    assert registry.async_get(other.entity_id) is not None
    assert len(er.async_entries_for_config_entry(registry, entry.entry_id)) == 13


# --- Sensors ----------------------------------------------------------------------


@pytest.mark.usefixtures("entities")
async def test_sensors_follow_the_decision(
    hass: HomeAssistant, unit: MockUnit, freezer: FrozenDateTimeFactory
) -> None:
    """Setpoint, reason, mode and ends_at show the last decision (BHV-51, BHV-23)."""
    entry = await setup_entry(hass)
    controller = controller_of(entry)
    await advance(hass, freezer, 60)
    decision = controller.current_decision
    assert decision is not None

    assert float(state_of(hass, SETPOINT).state) == decision.setpoint
    assert state_of(hass, REASON).state == str(decision.reason)
    assert state_of(hass, MODE).state == "automatic"
    assert state_of(hass, ENDS_AT).state == "unknown"
    attributes = state_of(hass, SETPOINT).attributes
    assert set(attributes) >= {
        "mode",
        "reason",
        "profile",
        "target",
        "written",
        "last_write",
        "actuator_value",
        "contributions",
        "co2_max",
        "co2_max_entity",
        "humidity_max",
        "humidity_max_entity",
        "humidity_mean",
        "limits",
        "invalid_inputs",
        "write_blocked_by",
    }
    assert "off_method" not in attributes
    assert attributes["mode"] == "automatic"
    assert attributes["profile"] == "balanced"
    assert attributes["written"] == decision.last_written_flow == 80
    assert attributes["last_write"] == decision.last_write
    assert attributes["contributions"]["minimum"] == decision.contributions.minimum
    assert set(attributes["contributions"]) == {
        "minimum",
        "co2",
        "humidity_high",
        "fallback",
        "boost",
        "shower",
        "cooking",
    }
    assert attributes["co2_max"] == 800
    assert attributes["co2_max_entity"] == "sensor.living_room_co2"
    assert attributes["humidity_mean"] == 45
    assert attributes["invalid_inputs"] == []
    assert attributes["limits"] == []


@pytest.mark.usefixtures("entities")
async def test_bhv16_setpoint_after_restart_shows_what_the_unit_runs_at(
    hass: HomeAssistant,
    unit: MockUnit,
    freezer: FrozenDateTimeFactory,
    hass_storage: dict[str, Any],
) -> None:
    """Restart sequence (02-control.md#output-shaping, setpoint while nothing is written).

    The unit runs at the last written 190 m³/h. During the startup grace the
    setpoint stays 190 while the target changes (85, then 240). The first write
    after the grace publishes 240 once: the evaluations before the delayed
    confirmation (grace end and periodic timer at the same time, the actuator
    still reports 190) hold the written value instead of showing 190 again.
    """
    unit.delay[NUMBER_FLOW] = 20  # confirms on its next poll, with a new context
    hass.states.async_set(NUMBER_FLOW, "190", {"min": 0, "max": 400, "step": 1})
    hass.states.async_set(CO2_LIVING, "860", {"device_class": "carbon_dioxide"})
    entry = mock_entry()
    key = f"{DOMAIN}.{entry.entry_id}"
    hass_storage[key] = {
        "key": key,
        "version": 1,
        "minor_version": 1,
        "data": {
            "last_written_flow": 190,
            "last_write": "2026-10-14T19:00:00+00:00",
            "last_mode": "automatic",
        },
    }
    published: list[tuple[float, float | None, float | None]] = []

    @callback
    def record(event: Event[EventStateChangedData]) -> None:
        new_state = event.data["new_state"]
        if new_state is not None and new_state.state not in ("unknown", "unavailable"):
            attributes = new_state.attributes
            published.append(
                (
                    float(new_state.state),
                    attributes.get("target"),
                    attributes.get("written"),
                )
            )

    async_track_state_change_event(hass, [SETPOINT], record)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert published == [(190, 85, 190)]

    await advance(hass, freezer, 20)
    hass.states.async_set(CO2_LIVING, "1125", {"device_class": "carbon_dioxide"})
    await hass.async_block_till_done()
    await advance(hass, freezer, 10)  # debounced evaluation, still in the grace
    assert published[-1] == (190, 240, 190)
    assert state_of(hass, SETPOINT).attributes["write_blocked_by"] == "grace"

    for _ in range(12):  # grace end, write, delayed confirmation, verification
        await advance(hass, freezer, 5)
    assert len(unit.calls_for("number", "set_value")) == 1
    assert state_of(hass, NUMBER_FLOW).state == "240.0"
    after_write = published[published.index((240, 240, 240)) :]
    assert {setpoint for setpoint, _, _ in after_write} == {240}
    assert [setpoint for setpoint, _, _ in published[:2]] == [190, 190]


def test_setpoint_unrecorded_attributes() -> None:
    """`actuator_value` and `write_blocked_by` are not recorded."""
    assert SetpointSensor._unrecorded_attributes == frozenset(
        {"actuator_value", "write_blocked_by"}
    )


@pytest.mark.usefixtures("entities")
async def test_off_mode_shows_zero_and_off_method(
    hass: HomeAssistant, unit: MockUnit, freezer: FrozenDateTimeFactory
) -> None:
    """In an off mode the setpoint is 0 and `off_method` is shown."""
    await setup_entry(hass)
    await advance(hass, freezer, 60)
    await select(hass, PAUSE_DURATION, "indefinite")
    await press(hass, PAUSE)

    setpoint = state_of(hass, SETPOINT)
    assert float(setpoint.state) == 0
    assert setpoint.attributes["off_method"] == "minimum"
    assert state_of(hass, MODE).state == "off"
    assert state_of(hass, REASON).state == "off"
    assert state_of(hass, ENDS_AT).state == "unknown"


@pytest.mark.usefixtures("entities")
async def test_problem_sensor_after_problem_delay(
    hass: HomeAssistant, unit: MockUnit, freezer: FrozenDateTimeFactory
) -> None:
    """The problem sensor turns on after `problem_delay` and lists the problems."""
    await setup_entry(hass)
    await advance(hass, freezer, 60)
    assert state_of(hass, PROBLEM).state == "off"
    assert state_of(hass, PROBLEM).attributes["problems"] == []

    hass.states.async_set(NUMBER_FLOW, "unavailable")
    await hass.async_block_till_done()
    since = dt_util.now()
    assert state_of(hass, PROBLEM).state == "off"
    assert state_of(hass, PROBLEM).attributes["problems"] == []

    for _ in range(10):
        await advance(hass, freezer, 60)
    problem = state_of(hass, PROBLEM)
    assert problem.state == "on"
    assert problem.attributes["problems"] == [
        {"key": "actuator_unavailable", "entity_id": NUMBER_FLOW, "since": since}
    ]


# --- Buttons, switch, selects, number ----------------------------------------------


@pytest.mark.usefixtures("entities")
async def test_boost_button_uses_parameter_entities(
    hass: HomeAssistant, unit: MockUnit, freezer: FrozenDateTimeFactory
) -> None:
    """The boost button uses the boost duration and level entities (BHV-20)."""
    entry = await setup_entry(hass)
    controller = controller_of(entry)
    await advance(hass, freezer, 60)
    await select(hass, BOOST_DURATION, "60")
    await set_number(hass, BOOST_LEVEL, 250)
    assert state_of(hass, BOOST_DURATION).state == "60"
    assert float(state_of(hass, BOOST_LEVEL).state) == 250

    await press(hass, BOOST)

    manual = controller.state.manual
    assert isinstance(manual, Boost)
    assert manual.level == 250
    assert manual.source is Source.BUTTON
    assert manual.until == dt_util.now() + timedelta(minutes=60)
    assert state_of(hass, MODE).state == "boost"
    assert state_of(hass, ENDS_AT).state == dt_util.as_utc(manual.until).isoformat(
        timespec="seconds"
    )
    assert unit.calls_for("number", "set_value")[-1].data["value"] == 250

    await press(hass, RESUME)
    assert controller.state.manual is None
    assert state_of(hass, MODE).state == "automatic"


@pytest.mark.usefixtures("entities")
@pytest.mark.parametrize(
    ("option", "duration"),
    [("1h", timedelta(hours=1)), ("8h", timedelta(hours=8)), ("indefinite", None)],
)
async def test_pause_button_uses_pause_duration(
    hass: HomeAssistant,
    unit: MockUnit,
    freezer: FrozenDateTimeFactory,
    option: str,
    duration: timedelta | None,
) -> None:
    """The pause button pauses for the pause duration entity (BHV-21)."""
    entry = await setup_entry(hass)
    controller = controller_of(entry)
    await advance(hass, freezer, 60)
    await select(hass, PAUSE_DURATION, option)

    await press(hass, PAUSE)

    manual = controller.state.manual
    assert isinstance(manual, Pause)
    assert manual.until == (dt_util.now() + duration if duration else None)
    assert state_of(hass, MODE).state == "off"


@pytest.mark.usefixtures("entities")
async def test_control_switch(
    hass: HomeAssistant, unit: MockUnit, freezer: FrozenDateTimeFactory
) -> None:
    """The control switch disables control; boost and pause are rejected (BHV-03)."""
    entry = await setup_entry(hass)
    controller = controller_of(entry)
    await advance(hass, freezer, 60)
    assert state_of(hass, CONTROL).state == "on"
    await press(hass, BOOST)

    await hass.services.async_call(
        "switch", "turn_off", {ATTR_ENTITY_ID: CONTROL}, blocking=True
    )
    assert state_of(hass, CONTROL).state == "off"
    assert not controller.state.control_enabled
    assert controller.state.manual is None
    assert state_of(hass, MODE).state == "disabled"

    for button in (BOOST, PAUSE):
        with pytest.raises(ServiceValidationError) as err:
            await press(hass, button)
        assert err.value.translation_domain == DOMAIN
        assert err.value.translation_key == "control_disabled"
    await press(hass, RESUME)  # nothing to end, no error

    await hass.services.async_call(
        "switch", "turn_on", {ATTR_ENTITY_ID: CONTROL}, blocking=True
    )
    assert state_of(hass, CONTROL).state == "on"
    assert state_of(hass, MODE).state == "automatic"


@pytest.mark.usefixtures("entities")
async def test_profile_select(
    hass: HomeAssistant, unit: MockUnit, freezer: FrozenDateTimeFactory
) -> None:
    """The profile select changes the profile, also while control is off (BHV-26)."""
    entry = await setup_entry(hass)
    controller = controller_of(entry)
    await advance(hass, freezer, 60)
    assert state_of(hass, PROFILE).state == "balanced"

    await select(hass, PROFILE, "quiet")
    assert controller.state.profile is Profile.QUIET
    assert state_of(hass, PROFILE).state == "quiet"
    assert state_of(hass, SETPOINT).attributes["profile"] == "quiet"

    await hass.services.async_call(
        "switch", "turn_off", {ATTR_ENTITY_ID: CONTROL}, blocking=True
    )
    await select(hass, PROFILE, "winter")
    assert controller.state.profile is Profile.WINTER


@pytest.mark.usefixtures("entities")
async def test_switch_and_profile_before_start(
    hass: HomeAssistant, unit: MockUnit
) -> None:
    """Commands before Home Assistant has started still update the entities."""
    hass.set_state(CoreState.not_running)
    entry = await setup_entry(hass)
    controller = controller_of(entry)
    assert not controller.started
    assert state_of(hass, SETPOINT).state == "unknown"

    await select(hass, PROFILE, "air_quality")
    await hass.services.async_call(
        "switch", "turn_off", {ATTR_ENTITY_ID: CONTROL}, blocking=True
    )

    assert state_of(hass, PROFILE).state == "air_quality"
    assert state_of(hass, CONTROL).state == "off"
    assert controller.current_decision is None


# --- Parameter entities: initial values and restore -------------------------------


@pytest.mark.parametrize(
    ("minutes", "option"),
    [
        (5, "15"),
        (20, "15"),
        (25, "30"),
        (45, "30"),
        (50, "60"),
        (90, "60"),
        (100, "120"),
    ],
)
def test_nearest_boost_duration(minutes: int, option: str) -> None:
    """The initial boost duration is the option nearest to the default (ties: shorter)."""
    assert nearest_boost_duration(timedelta(minutes=minutes)) == option


@pytest.mark.usefixtures("entities")
async def test_parameter_entities_initial_values(
    hass: HomeAssistant, unit: MockUnit
) -> None:
    """Without a restored state the options defaults are used."""
    entry = await setup_entry(
        hass, boost_default_duration=50 * 60, boost_default_level=320
    )
    controller = controller_of(entry)
    assert state_of(hass, BOOST_DURATION).state == "60"
    assert state_of(hass, PAUSE_DURATION).state == "1h"
    assert float(state_of(hass, BOOST_LEVEL).state) == 320
    assert controller.boost_duration == timedelta(minutes=60)
    assert controller.boost_level == 320
    assert controller.pause_duration == timedelta(hours=1)


@pytest.mark.usefixtures("entities")
async def test_parameter_entities_restore(hass: HomeAssistant, unit: MockUnit) -> None:
    """Selects and the number restore their last value after a restart."""
    mock_restore_cache_with_extra_data(
        hass,
        [
            (State(BOOST_DURATION, "120"), {}),
            (State(PAUSE_DURATION, "indefinite"), {}),
            (State(BOOST_LEVEL, "260"), _number_data(260)),
        ],
    )
    entry = await setup_entry(hass)
    controller = controller_of(entry)

    assert state_of(hass, BOOST_DURATION).state == "120"
    assert state_of(hass, PAUSE_DURATION).state == "indefinite"
    assert float(state_of(hass, BOOST_LEVEL).state) == 260
    assert controller.boost_duration == timedelta(minutes=120)
    assert controller.pause_duration is None
    assert controller.boost_level == 260


@pytest.mark.usefixtures("entities")
async def test_parameter_entities_ignore_invalid_restore(
    hass: HomeAssistant, unit: MockUnit
) -> None:
    """Unknown options fall back to the initial value; a level is clamped."""
    mock_restore_cache_with_extra_data(
        hass,
        [
            (State(BOOST_DURATION, "45"), {}),
            (State(PAUSE_DURATION, "unknown"), {}),
            (State(BOOST_LEVEL, "900"), _number_data(900)),
        ],
    )
    await setup_entry(hass)

    assert state_of(hass, BOOST_DURATION).state == "30"
    assert state_of(hass, PAUSE_DURATION).state == "1h"
    assert float(state_of(hass, BOOST_LEVEL).state) == 400


@pytest.mark.usefixtures("entities")
async def test_parameter_entities_survive_reload(
    hass: HomeAssistant, unit: MockUnit
) -> None:
    """Changed parameter values are kept across a reload of the entry."""
    entry = await setup_entry(hass)
    await select(hass, BOOST_DURATION, "15")
    await select(hass, PAUSE_DURATION, "4h")
    await set_number(hass, BOOST_LEVEL, 215)

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    controller = controller_of(entry)
    assert state_of(hass, BOOST_DURATION).state == "15"
    assert state_of(hass, PAUSE_DURATION).state == "4h"
    assert float(state_of(hass, BOOST_LEVEL).state) == 215
    assert controller.boost_duration == timedelta(minutes=15)
    assert controller.pause_duration == timedelta(hours=4)
    assert controller.boost_level == 215


def _number_data(value: float) -> dict[str, Any]:
    """Return restore data of a number entity."""
    return {
        "native_max_value": 400.0,
        "native_min_value": 80.0,
        "native_step": 5.0,
        "native_unit_of_measurement": "m³/h",
        "native_value": value,
    }


@pytest.mark.usefixtures("entities")
async def test_entities_removed_on_unload(hass: HomeAssistant, unit: MockUnit) -> None:
    """Unloading the entry leaves the entities unavailable and stops updates."""
    entry: MockConfigEntry = await setup_entry(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert state_of(hass, SETPOINT).state == "unavailable"
