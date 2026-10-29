"""Diagnostics download (BHV-54, C13)."""

from __future__ import annotations

from datetime import timedelta
import json

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
import pytest

from custom_components.better_home_ventilation.const import (
    CONF_CO2_SENSORS,
    CONF_NOTIFY_SERVICES,
    CONF_PRESENCE_ENTITIES,
)
from custom_components.better_home_ventilation.diagnostics import (
    async_get_config_entry_diagnostics,
    jsonable,
    redact_presence,
)
from custom_components.better_home_ventilation.engine.models import (
    BoostCommand,
    Source,
)

from .common import (
    CO2_LIVING,
    CONTROL_MODE,
    HUMIDITY_LIVING,
    NUMBER_FLOW,
    PERSON,
    MockUnit,
    advance,
)
from .test_controller import controller_of, readiness, setup_entry
from .test_safety import source

TRACKER = "device_tracker.resident_phone"


@pytest.fixture(autouse=True)
def daytime(freezer: FrozenDateTimeFactory) -> None:
    """Run every test at 13:00 local time, outside the night window."""
    freezer.move_to("2026-10-14T20:00:00+00:00")


@pytest.mark.usefixtures("entities")
async def test_c13_diagnostics_content(
    hass: HomeAssistant, unit: MockUnit, freezer: FrozenDateTimeFactory
) -> None:
    """Options, subentries, state, entities, decisions, problems and warnings."""
    hass.states.async_set(TRACKER, "Office", {"latitude": 1.0, "longitude": 2.0})
    hass.states.async_set("binary_sensor.smoke", "on")
    entry = await setup_entry(
        hass,
        subentries=[
            readiness(CONTROL_MODE, "Manual"),
            source(entity_id="binary_sensor.smoke", name="Smoke"),
        ],
        **{
            CONF_PRESENCE_ENTITIES: [PERSON, TRACKER],
            CONF_NOTIFY_SERVICES: ["mobile_app_example_phone"],
            CONF_CO2_SENSORS: [CO2_LIVING, "sensor.gone_co2"],
        },
    )
    controller = controller_of(entry)
    await advance(hass, freezer, 61)
    await controller.async_command(BoostCommand(source=Source.BUTTON))

    data = await async_get_config_entry_diagnostics(hass, entry)
    json.dumps(data)  # serialisable as the download

    assert data["entry"]["title"] == "Ventilation"
    options = data["entry"]["options"]
    assert options[CONF_NOTIFY_SERVICES] == "**REDACTED**"
    assert options[CONF_CO2_SENSORS] == [CO2_LIVING, "sensor.gone_co2"]
    assert {item["subentry_type"] for item in data["subentries"]} == {
        "readiness_condition",
        "warning_source",
    }
    assert data["stored_state"]["manual"]["type"] == "boost"
    assert data["controller"]["started"] is True
    assert data["controller"]["state_reset"] is False

    entities = data["entities"]
    assert entities[PERSON]["state"] == "home"
    assert entities[TRACKER]["state"] == "not_home"
    assert "latitude" not in json.dumps(entities)
    assert entities["sensor.gone_co2"] is None
    for entity_id in (
        NUMBER_FLOW,
        HUMIDITY_LIVING,
        CONTROL_MODE,
        "binary_sensor.smoke",
    ):
        assert set(entities[entity_id]) == {"state", "last_changed", "last_reported"}

    assert 1 < len(data["decisions"]) <= 100
    last = data["decisions"][-1]
    assert last["mode"] == "safety_off"
    assert last["contributions"]["minimum"] == 80
    assert {"key": "inputs_invalid", "entity_id": "sensor.gone_co2"}.items() <= next(
        problem
        for problem in data["problems"]
        if problem["entity_id"] == "sensor.gone_co2"
    ).items()
    [warning] = data["warnings"]["active"]
    assert warning["source_name"] == "Smoke"
    assert data["warnings"]["awaiting_restart"] is False


async def test_diagnostics_before_the_first_evaluation(
    hass: HomeAssistant, entities: None, unit: MockUnit
) -> None:
    """Before Home Assistant started there are no decisions yet."""
    hass.set_state(hass.state.__class__.starting)
    entry = await setup_entry(hass)
    data = await async_get_config_entry_diagnostics(hass, entry)
    assert data["decisions"] == []
    assert data["problems"] == []
    assert data["warnings"] == {
        "active": [],
        "overridden_incidents": [],
        "awaiting_restart": False,
    }
    await hass.async_start()
    await hass.async_block_till_done()


def test_presence_redaction_and_conversion() -> None:
    """Locations become home/not_home; sets and durations become JSON types."""
    assert redact_presence("person.a", "home") == "home"
    assert redact_presence("person.a", "Work") == "not_home"
    assert redact_presence("device_tracker.a", "unavailable") == "unavailable"
    assert redact_presence("binary_sensor.a", "on") == "on"
    assert jsonable({"b", "a"}) == ["a", "b"]
    assert jsonable(timedelta(minutes=1)) == 60.0
