"""Fixtures for component tests."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .common import (
    CO2_BEDROOM,
    CO2_LIVING,
    CONTROL_MODE,
    FAN,
    HUMIDITY_BEDROOM,
    HUMIDITY_LIVING,
    NUMBER_FLOW,
    NUMBER_PERCENT,
    NUMBER_RAW,
    PERSON,
    SELECT_SINGLE,
    SELECT_STAGES,
    STAGE_OPTIONS,
    STANDBY_SWITCH,
    mock_entry,
)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable loading of custom integrations in all component tests."""


@pytest.fixture
def entities(hass: HomeAssistant) -> None:
    """Create the states of the example actuators and sensors."""
    flow = {"unit_of_measurement": "m³/h", "min": 0, "max": 400, "step": 1}
    hass.states.async_set(NUMBER_FLOW, "100", flow)
    hass.states.async_set(
        NUMBER_PERCENT, "50", {"unit_of_measurement": "%", "min": 0, "max": 100}
    )
    hass.states.async_set(NUMBER_RAW, "3", {"min": 0, "max": 10})
    hass.states.async_set(FAN, "on", {"percentage": 50})
    hass.states.async_set(SELECT_STAGES, "Stage 2", {"options": STAGE_OPTIONS})
    hass.states.async_set(SELECT_SINGLE, "Auto", {"options": ["Auto"]})
    hass.states.async_set(
        CO2_LIVING,
        "800",
        {"device_class": "carbon_dioxide", "unit_of_measurement": "ppm"},
    )
    hass.states.async_set(
        CO2_BEDROOM,
        "900",
        {"device_class": "carbon_dioxide", "unit_of_measurement": "ppm"},
    )
    hass.states.async_set(
        HUMIDITY_LIVING, "45", {"device_class": "humidity", "unit_of_measurement": "%"}
    )
    hass.states.async_set(
        HUMIDITY_BEDROOM, "50", {"device_class": "humidity", "unit_of_measurement": "%"}
    )
    hass.states.async_set(PERSON, "home")
    hass.states.async_set(STANDBY_SWITCH, "off")
    hass.states.async_set(
        CONTROL_MODE,
        "Manual",
        {"options": ["Manual", "Remote"], "friendly_name": "Unit control mode"},
    )


@pytest.fixture
async def entry(hass: HomeAssistant, entities: None) -> MockConfigEntry:
    """Return a loaded config entry with complete options."""
    config_entry = mock_entry()
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry
