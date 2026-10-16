"""Entry setup, unload, update listener and migration (C4b)."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
import pytest

from custom_components.better_home_ventilation import async_migrate_entry
from custom_components.better_home_ventilation.const import DOMAIN

from .common import entry_options, mock_entry


async def test_setup_component_with_empty_config(hass: HomeAssistant) -> None:
    """The integration sets up without YAML configuration."""
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()

    assert DOMAIN in hass.config.components


async def test_setup_and_unload_entry(hass: HomeAssistant) -> None:
    """A config entry loads with one update listener and unloads cleanly."""
    entry = mock_entry()
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert len(entry.update_listeners) == 1

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert entry.update_listeners == []


async def test_bhv53_options_change_reloads_entry(hass: HomeAssistant) -> None:
    """Any options update reloads the entry through the update listener."""
    entry = mock_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    with patch.object(
        hass.config_entries,
        "async_reload",
        wraps=hass.config_entries.async_reload,
    ) as reload_spy:
        hass.config_entries.async_update_entry(
            entry, options=entry_options(co2_start=900)
        )
        await hass.async_block_till_done()

    reload_spy.assert_called_once_with(entry.entry_id)
    assert entry.state is ConfigEntryState.LOADED
    assert len(entry.update_listeners) == 1


@pytest.mark.parametrize(("version", "minor_version"), [(1, 1), (1, 0), (1, 2)])
async def test_migration_current_and_compatible_versions(
    hass: HomeAssistant, version: int, minor_version: int
) -> None:
    """Version 1 entries load; minor versions are backwards compatible."""
    entry = mock_entry()
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        entry, version=version, minor_version=minor_version
    )

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert entry.version == version
    assert entry.minor_version == minor_version


async def test_migration_rejects_future_major_version(hass: HomeAssistant) -> None:
    """An entry from a future major version fails cleanly (downgrade)."""
    entry = mock_entry()
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(entry, version=2)

    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.MIGRATION_ERROR
    assert await async_migrate_entry(hass, entry) is False
