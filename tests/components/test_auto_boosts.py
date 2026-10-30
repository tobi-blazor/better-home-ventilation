"""Shower and cooking detection in Home Assistant (BHV-30, BHV-31, BHV-32).

Entities `shower_detected` and `cooking_detected` (created only when
configured), end to end with a rising bathroom humidity, the cooking trigger
entity, restart with an active auto boost, renames, repairs and diagnostics.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.better_home_ventilation.const import (
    CONF_BATHROOM_HUMIDITY_SENSORS,
    CONF_COOKING_SENSORS,
    CONF_COOKING_TRIGGER,
    DOMAIN,
    EVENT_MODE_CHANGED,
)
from custom_components.better_home_ventilation.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.better_home_ventilation.engine.models import (
    AutoBoostKind,
    Mode,
    ResumeCommand,
    Source,
)
from custom_components.better_home_ventilation.issues import configured_targets

from .common import (
    BATHROOM_HUMIDITY,
    HOB_POWER,
    KITCHEN_VOC,
    MockUnit,
    advance,
    entry_options,
)
from .test_controller import controller_of, setup_entry
from .test_lifecycle import register

SHOWER = "binary_sensor.ventilation_shower_detected"
COOKING = "binary_sensor.ventilation_cooking_detected"
MODE = "sensor.ventilation_mode"
ENDS_AT = "sensor.ventilation_ends_at"
HUMIDITY = {"device_class": "humidity", "unit_of_measurement": "%"}


@pytest.fixture(autouse=True)
def daytime(freezer: FrozenDateTimeFactory) -> None:
    """Run every test at 13:00 local time, outside the night window."""
    freezer.move_to("2026-10-14T20:00:00+00:00")


def _state(hass: HomeAssistant, entity_id: str) -> str:
    state = hass.states.get(entity_id)
    assert state is not None, entity_id
    return state.state


async def _set(
    hass: HomeAssistant, entity_id: str, value: str, attributes: dict[str, Any]
) -> None:
    """Set a state and let its listeners run before the clock moves."""
    hass.states.async_set(entity_id, value, attributes)
    await hass.async_block_till_done()


def _mode_events(hass: HomeAssistant) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []

    @callback
    def listener(event: Event) -> None:
        events.append(dict(event.data))

    hass.bus.async_listen(EVENT_MODE_CHANGED, listener)
    return events


def _written(unit: MockUnit) -> list[float]:
    return [float(call.data["value"]) for call in unit.calls_for("number", "set_value")]


# --- Entities ---------------------------------------------------------------------


@pytest.mark.usefixtures("entities")
async def test_detection_entities_only_when_configured(
    hass: HomeAssistant, unit: MockUnit
) -> None:
    """Without detector sensors neither entity exists."""
    await setup_entry(hass)
    assert hass.states.get(SHOWER) is None
    assert hass.states.get(COOKING) is None


@pytest.mark.usefixtures("entities")
async def test_detection_entities_are_created_and_removed(
    hass: HomeAssistant, unit: MockUnit
) -> None:
    """Configured -> created (off, translated name); removed -> deleted on reload."""
    entry = await setup_entry(
        hass, bathroom_humidity_sensors=[BATHROOM_HUMIDITY], cooking_trigger=HOB_POWER
    )
    registry = er.async_get(hass)
    shower = registry.async_get(SHOWER)
    assert shower is not None
    assert shower.unique_id == f"{entry.entry_id}_shower_detected"
    assert shower.translation_key == "shower_detected"
    assert shower.original_device_class is None
    state = hass.states.get(SHOWER)
    assert state is not None
    assert state.name == "Ventilation Shower detected"
    cooking = registry.async_get(COOKING)
    assert cooking is not None
    assert cooking.unique_id == f"{entry.entry_id}_cooking_detected"

    hass.config_entries.async_update_entry(
        entry, options=entry_options(**{CONF_COOKING_SENSORS: [KITCHEN_VOC]})
    )
    await hass.async_block_till_done()
    assert registry.async_get(SHOWER) is None
    assert registry.async_get(COOKING) is not None

    hass.config_entries.async_update_entry(entry, options=entry_options())
    await hass.async_block_till_done()
    assert registry.async_get(COOKING) is None
    assert len(er.async_entries_for_config_entry(registry, entry.entry_id)) == 13


# --- End to end -------------------------------------------------------------------


@pytest.mark.usefixtures("entities")
async def test_bhv30_bathroom_humidity_rise_boosts_until_it_dries(
    hass: HomeAssistant, unit: MockUnit, freezer: FrozenDateTimeFactory
) -> None:
    """55 -> 64 % starts the shower boost (250 m³/h); back at 60 % it ends."""
    events = _mode_events(hass)
    entry = await setup_entry(hass, bathroom_humidity_sensors=[BATHROOM_HUMIDITY])
    controller = controller_of(entry)
    await advance(hass, freezer, 61)  # startup grace over
    assert _state(hass, SHOWER) == "off"
    assert _written(unit)[-1] == 80

    await _set(hass, BATHROOM_HUMIDITY, "58", HUMIDITY)
    await advance(hass, freezer, 120)
    assert _state(hass, MODE) == "automatic"
    await _set(hass, BATHROOM_HUMIDITY, "64", HUMIDITY)
    await advance(hass, freezer, 11)  # debounced evaluation

    assert _state(hass, SHOWER) == "on"
    assert _state(hass, MODE) == "shower"
    assert _state(hass, "sensor.ventilation_reason") == "shower"
    assert _written(unit)[-1] == 250
    started = dt_util.utcnow().replace(microsecond=0)
    ends_at = dt_util.parse_datetime(_state(hass, ENDS_AT))
    assert ends_at is not None
    assert abs(ends_at - (started + timedelta(hours=1))) < timedelta(seconds=2)
    assert events[-1]["to_mode"] == "shower"
    assert events[-1]["trigger"] == "detector_start"
    setpoint = hass.states.get("sensor.ventilation_setpoint")
    assert setpoint is not None
    assert setpoint.attributes["contributions"]["shower"] == 250
    stored = controller.state.auto_boosts
    assert [boost.kind for boost in stored.values()] == [AutoBoostKind.SHOWER]
    assert next(iter(stored.values())).baseline == 55

    await _set(hass, BATHROOM_HUMIDITY, "60", HUMIDITY)
    await advance(hass, freezer, 11)
    assert _state(hass, SHOWER) == "off"
    assert _state(hass, MODE) == "automatic"
    assert _written(unit)[-1] == 80
    assert events[-1]["trigger"] == "detector_end"


@pytest.mark.usefixtures("entities")
async def test_bhv31_cooking_trigger_and_resume(
    hass: HomeAssistant, unit: MockUnit, freezer: FrozenDateTimeFactory
) -> None:
    """The hob power starts the cooking boost; `resume()` ends it."""
    entry = await setup_entry(hass, cooking_trigger=HOB_POWER)
    controller = controller_of(entry)
    await advance(hass, freezer, 61)

    await _set(hass, HOB_POWER, "1500", {"unit_of_measurement": "W"})
    await advance(hass, freezer, 11)
    assert _state(hass, COOKING) == "on"
    assert _state(hass, MODE) == "cooking"

    await controller.async_command(ResumeCommand(source=Source.BUTTON))
    await hass.async_block_till_done()
    assert _state(hass, COOKING) == "off"
    assert _state(hass, MODE) == "automatic"
    await advance(hass, freezer, 60 * 15)
    assert _state(hass, MODE) == "automatic"  # the hob is still on


@pytest.mark.usefixtures("entities")
async def test_restart_with_an_active_shower_keeps_its_deadline(
    hass: HomeAssistant, unit: MockUnit, hass_storage: dict[str, Any]
) -> None:
    """An auto boost in the store continues after a restart until its deadline."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Ventilation",
        data={},
        options=entry_options(**{CONF_BATHROOM_HUMIDITY_SENSORS: [BATHROOM_HUMIDITY]}),
        version=1,
        minor_version=3,
    )
    key = f"{DOMAIN}.{entry.entry_id}"
    now = dt_util.utcnow()
    deadline = (now + timedelta(minutes=40)).replace(microsecond=0)
    hass_storage[key] = {
        "key": key,
        "version": 1,
        "minor_version": 3,
        "data": {
            "auto_boosts": {
                f"shower:{BATHROOM_HUMIDITY}": {
                    "kind": "shower",
                    "entity_id": BATHROOM_HUMIDITY,
                    "baseline": 50.0,
                    "started": (now - timedelta(minutes=20)).isoformat(),
                    "deadline": deadline.isoformat(),
                }
            },
            "detector_cooldown": {},
            "last_mode": "shower",
        },
    }
    hass.states.async_set(BATHROOM_HUMIDITY, "70", HUMIDITY)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    controller = controller_of(entry)
    decision = controller.current_decision
    assert decision is not None
    assert decision.mode is Mode.SHOWER
    assert decision.ends_at == deadline
    assert _state(hass, SHOWER) == "on"
    ends_at = dt_util.parse_datetime(_state(hass, ENDS_AT))
    assert ends_at == deadline


# --- Renames, repairs, diagnostics ---------------------------------------------------


@pytest.mark.usefixtures("entities")
async def test_renamed_bathroom_sensor_is_followed(
    hass: HomeAssistant, unit: MockUnit
) -> None:
    """A renamed detector sensor is written to the options (BHV-53)."""
    registry = register(hass, BATHROOM_HUMIDITY)
    entry = await setup_entry(hass, bathroom_humidity_sensors=[BATHROOM_HUMIDITY])
    registry.async_update_entity(
        BATHROOM_HUMIDITY, new_entity_id="sensor.main_bathroom_humidity"
    )
    await hass.async_block_till_done()
    assert entry.options[CONF_BATHROOM_HUMIDITY_SENSORS] == [
        "sensor.main_bathroom_humidity"
    ]
    assert controller_of(entry).options.bathroom_humidity_sensors == (
        "sensor.main_bathroom_humidity",
    )


@pytest.mark.usefixtures("entities")
async def test_renamed_cooking_trigger_is_followed(
    hass: HomeAssistant, unit: MockUnit
) -> None:
    """A renamed trigger entity (a single entity id) is followed as well."""
    registry = register(hass, HOB_POWER)
    entry = await setup_entry(hass, cooking_trigger=HOB_POWER)
    registry.async_update_entity(HOB_POWER, new_entity_id="sensor.induction_power")
    await hass.async_block_till_done()
    assert entry.options[CONF_COOKING_TRIGGER] == "sensor.induction_power"


def test_missing_entity_targets_cover_the_detector_entities() -> None:
    """Bathroom, cooking sensors and the trigger are checked for missing entities."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        options=entry_options(
            **{
                CONF_BATHROOM_HUMIDITY_SENSORS: [BATHROOM_HUMIDITY],
                CONF_COOKING_SENSORS: [KITCHEN_VOC],
                CONF_COOKING_TRIGGER: HOB_POWER,
            }
        ),
    )
    targets = configured_targets(entry)
    assert targets[BATHROOM_HUMIDITY].where == CONF_BATHROOM_HUMIDITY_SENSORS
    assert targets[KITCHEN_VOC].where == CONF_COOKING_SENSORS
    assert targets[HOB_POWER].where == CONF_COOKING_TRIGGER


@pytest.mark.usefixtures("entities")
async def test_diagnostics_include_the_detectors(
    hass: HomeAssistant, unit: MockUnit, freezer: FrozenDateTimeFactory
) -> None:
    """The download shows the detector samples, auto boosts and sensor states."""
    entry = await setup_entry(
        hass,
        bathroom_humidity_sensors=[BATHROOM_HUMIDITY],
        cooking_sensors=[KITCHEN_VOC],
        cooking_trigger=HOB_POWER,
    )
    await advance(hass, freezer, 61)
    await _set(hass, BATHROOM_HUMIDITY, "65", HUMIDITY)
    await advance(hass, freezer, 11)

    data = await async_get_config_entry_diagnostics(hass, entry)
    detectors = data["controller"]["detectors"]
    assert set(detectors) == {
        f"shower:{BATHROOM_HUMIDITY}",
        f"cooking:{KITCHEN_VOC}",
        f"cooking_trigger:{HOB_POWER}",
    }
    assert [value for _, value in detectors[f"shower:{BATHROOM_HUMIDITY}"]["samples"]][
        -1
    ] == 65
    assert detectors[f"cooking_trigger:{HOB_POWER}"]["armed"] is True
    assert list(data["stored_state"]["auto_boosts"]) == [f"shower:{BATHROOM_HUMIDITY}"]
    assert data["entities"][BATHROOM_HUMIDITY]["state"] == "65"
    assert data["entities"][HOB_POWER]["state"] == "0"
    assert data["decisions"][-1]["auto_boosts"][0]["kind"] == "shower"
