"""Better Home Ventilation: demand-controlled ventilation for Home Assistant."""

from __future__ import annotations

import logging
from typing import Final

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.typing import ConfigType

from .config_flow import BetterHomeVentilationConfigFlow
from .const import DOMAIN
from .controller import (
    BetterVentilationConfigEntry,
    VentilationController,
    create_store,
)
from .entity import async_remove_stale_entities
from .services import async_setup_services

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

#: Entity platforms of the entry (docs/spec/04-interface.md#entities).
PLATFORMS: Final[list[Platform]] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the integration: register the services (BHV-22)."""
    async_setup_services(hass)
    return True


async def async_setup_entry(
    hass: HomeAssistant, entry: BetterVentilationConfigEntry
) -> bool:
    """Set up a config entry: controller, store, device, entities, start.

    Missing source entities do not fail the setup (they may load later); they
    count as invalid inputs. Control starts once Home Assistant has started.
    One update listener reloads the entry on every change of its options,
    subentries or title
    (docs/adr/0001-update-listener-instead-of-options-flow-with-reload.md).
    """
    controller = VentilationController(hass, entry)
    await controller.async_setup()
    entry.runtime_data = controller
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    async_remove_stale_entities(hass, entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(async_at_started(hass, controller.async_start))
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: BetterVentilationConfigEntry
) -> bool:
    """Unload the platforms, stop the controller and flush its store."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_shutdown()
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Delete the stored controller state of a removed entry (BHV-50)."""
    await create_store(hass, entry.entry_id).async_remove()


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry after its options or subentries changed (BHV-53)."""
    hass.config_entries.async_schedule_reload(entry.entry_id)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate an entry to the current version (no migration steps yet).

    A major version from the future (a downgrade) cannot be read and fails.
    Minor versions are backwards compatible: a newer minor version of the
    current major version is accepted as is.
    """
    current = BetterHomeVentilationConfigFlow.VERSION
    if entry.version > current:
        _LOGGER.error(
            "Cannot migrate %s from version %s.%s: newer than the supported version %s",
            entry.title,
            entry.version,
            entry.minor_version,
            current,
        )
        return False
    return True
