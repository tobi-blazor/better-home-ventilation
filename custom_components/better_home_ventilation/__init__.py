"""Better Home Ventilation: demand-controlled ventilation for Home Assistant."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .config_flow import BetterHomeVentilationConfigFlow
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the integration (services and intents follow in later milestones)."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a config entry (the controller follows in milestone M4).

    One update listener reloads the entry on every change of its options,
    subentries or title (docs/adr/0001-update-listener-instead-of-options-flow-with-reload.md).
    """
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry; the update listener is removed by `async_on_unload`."""
    return True


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
