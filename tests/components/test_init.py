"""Setup of the integration skeleton."""

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.better_home_ventilation.const import DOMAIN


async def test_setup_component_with_empty_config(hass: HomeAssistant) -> None:
    """The integration sets up without YAML configuration."""
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()

    assert DOMAIN in hass.config.components


async def test_setup_and_unload_entry(hass: HomeAssistant) -> None:
    """A config entry loads and unloads (skeleton without a controller)."""
    entry = MockConfigEntry(domain=DOMAIN, title="Ventilation", data={}, options={})
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
