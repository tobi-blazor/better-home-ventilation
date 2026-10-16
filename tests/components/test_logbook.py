"""Logbook descriptions of mode changes (C12, BHV-52)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from unittest.mock import patch

from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.logbook import DOMAIN as LOGBOOK_DOMAIN
from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import async_capture_events

from custom_components.better_home_ventilation.const import DOMAIN, EVENT_MODE_CHANGED
from custom_components.better_home_ventilation.logbook import (
    async_describe_events,
    describe_mode_change,
)

from .common import CO2_LIVING, HUMIDITY_LIVING, MockUnit, advance
from .test_controller import controller_of, setup_entry

type Describe = Callable[[Event], dict[str, Any]]


@pytest.fixture
def mock_recorder_before_hass(async_test_recorder: Any) -> None:
    """Prepare the recorder before `hass` (the logbook depends on it)."""


@pytest.fixture(autouse=True)
def daytime(freezer: FrozenDateTimeFactory) -> None:
    """Run every test at 13:00 local time (US/Pacific in tests)."""
    freezer.move_to("2026-10-14T20:00:00+00:00")


def describer(hass: HomeAssistant) -> Describe:
    """Return the describe callback of the integration's logbook platform."""
    described: dict[str, tuple[str, Describe]] = {}

    def register(domain: str, event_type: str, describe: Describe) -> None:
        described[event_type] = (domain, describe)

    async_describe_events(hass, register)
    domain, describe = described[EVENT_MODE_CHANGED]
    assert domain == DOMAIN
    return describe


async def boost_and_timeout(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> list[Event]:
    """Press boost (30 min), let it time out and return the mode change events."""
    events = async_capture_events(hass, EVENT_MODE_CHANGED)
    await setup_entry(hass)
    await advance(hass, freezer, 60)
    await hass.services.async_call(
        "button", "press", {"entity_id": "button.ventilation_boost"}, blocking=True
    )
    await advance(hass, freezer, 29 * 60)
    for entity_id in (CO2_LIVING, HUMIDITY_LIVING):  # keep the inputs fresh
        state = hass.states.get(entity_id)
        assert state is not None
        hass.states.async_set(entity_id, state.state, state.attributes)
    await advance(hass, freezer, 60)
    assert [event.data["to_mode"] for event in events] == ["boost", "automatic"]
    return events


@pytest.mark.usefixtures("entities")
async def test_c12_logbook_describes_mode_changed(
    recorder_mock: Any,
    hass: HomeAssistant,
    unit: MockUnit,
    freezer: FrozenDateTimeFactory,
) -> None:
    """The logbook platform describes mode changes, linked to the mode sensor."""
    # The frontend package is not installed in the test environment.
    hass.config.components.add("frontend")
    with patch(
        "homeassistant.components.logbook.frontend.async_register_built_in_panel"
    ):
        assert await async_setup_component(hass, LOGBOOK_DOMAIN, {})
    events = await boost_and_timeout(hass, freezer)
    await hass.async_block_till_done()
    domain, describe = hass.data[LOGBOOK_DOMAIN].external_events[EVENT_MODE_CHANGED]
    assert domain == DOMAIN

    assert describe(events[0]) == {
        "name": "Ventilation",
        "message": "switched to Boost (button, until 13:31)",
        "entity_id": "sensor.ventilation_mode",
    }
    assert describe(events[1]) == {
        "name": "Ventilation",
        "message": "back to Automatic (timeout)",
        "entity_id": "sensor.ventilation_mode",
    }


@pytest.mark.usefixtures("entities")
async def test_logbook_in_german_with_renamed_device(
    hass: HomeAssistant, unit: MockUnit, freezer: FrozenDateTimeFactory
) -> None:
    """Messages use the server language; the name follows a renamed device."""
    hass.config.language = "de"
    events = await boost_and_timeout(hass, freezer)
    device_id = events[0].data["device_id"]
    dr.async_get(hass).async_update_device(device_id, name_by_user="Lüftung")
    describe = describer(hass)

    assert describe(events[0]) == {
        "name": "Lüftung",
        "message": "auf Boost umgeschaltet (Taste, bis 13:31)",
        "entity_id": "sensor.ventilation_modus",
    }
    assert describe(events[1])["message"] == "zurück zu Automatik (Zeit abgelaufen)"


@pytest.mark.usefixtures("entities")
async def test_logbook_control_switch_and_long_pause(
    hass: HomeAssistant, unit: MockUnit, freezer: FrozenDateTimeFactory
) -> None:
    """Trigger names for non-command changes; the date for a later day."""
    events = async_capture_events(hass, EVENT_MODE_CHANGED)
    entry = await setup_entry(hass)
    await advance(hass, freezer, 60)
    await hass.services.async_call(
        DOMAIN, "pause", {"duration": 24 * 60}, blocking=True
    )
    await hass.services.async_call(
        "switch",
        "turn_off",
        {"entity_id": "switch.ventilation_control"},
        blocking=True,
    )
    describe = describer(hass)

    assert describe(events[0])["message"] == (
        "switched to Off (action, until 2026-10-15 13:01)"
    )
    assert describe(events[1])["message"] == "switched to Control off (control switch)"
    assert controller_of(entry).state.manual is None


async def test_logbook_without_entry(hass: HomeAssistant) -> None:
    """Without a loaded entry the name comes from the event, without entity."""
    describe = describer(hass)
    event: Event = Event(
        EVENT_MODE_CHANGED,
        {
            "entry_id": "unknown",
            "device_id": "unknown",
            "name": "Ventilation",
            "from_mode": "boost",
            "to_mode": "automatic",
            "trigger": "timeout",
            "source": None,
            "ends_at": None,
        },
    )
    assert describe(event) == {
        "name": "Ventilation",
        "message": "back to Automatic (timeout)",
    }


def test_describe_mode_change_templates() -> None:
    """Placeholders are filled; broken templates and dates are tolerated."""
    common = {
        "component.better_home_ventilation.common.logbook_switched_to": (
            "to {mode}: {details}"
        ),
        "component.better_home_ventilation.common.logbook_until": "until {when}",
        "component.better_home_ventilation.common.logbook_source_voice": "voice",
    }
    data = {
        "to_mode": "boost",
        "trigger": "command",
        "source": "voice",
        "ends_at": "not a date",
    }
    assert describe_mode_change(data, common, {}) == "to boost: voice"
    assert describe_mode_change(data, {}, {}) == "logbook_switched_to"
    data["ends_at"] = "2026-10-14T20:30:00+00:00"
    assert describe_mode_change(data, common, {}, dt_util.utcnow()) == (
        "to boost: voice, until {when}"
    )
