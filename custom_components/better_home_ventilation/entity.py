"""Base entity of Better Home Ventilation.

docs/spec/04-interface.md#device: every entity belongs to the one service
device of its config entry (created by the controller), uses
``has_entity_name``, a ``translation_key`` and ``unique_id =
f"{entry_id}_{key}"``. Entities are pushed by the controller after every
evaluation and stay available while the entry is loaded.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN, ENTITY_KEYS, SAFETY_ENTITY_KEYS
from .controller import BetterVentilationConfigEntry, VentilationController
from .engine.models import Decision


def unique_id(entry_id: str, key: str) -> str:
    """Return the unique id of an entity of an entry."""
    return f"{entry_id}_{key}"


@callback
def async_remove_stale_entities(
    hass: HomeAssistant, entry: BetterVentilationConfigEntry
) -> None:
    """Remove registry entries of the entry that are no longer created.

    docs/spec/04-interface.md#entities ("Only what is configured"): entities of
    features that were removed (or of older versions) are removed from the
    entity registry during setup. The safety entities exist only with at
    least one warning source, so they are removed with the last one.
    """
    platforms = [ENTITY_KEYS]
    if entry.runtime_data.has_warning_sources:
        platforms.append(SAFETY_ENTITY_KEYS)
    expected = {
        unique_id(entry.entry_id, key)
        for keys_by_platform in platforms
        for keys in keys_by_platform.values()
        for key in keys
    }
    registry = er.async_get(hass)
    for registry_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if registry_entry.unique_id not in expected:
            registry.async_remove(registry_entry.entity_id)


class VentilationEntity(Entity):
    """Entity of one controller; names and icons come from the translations."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, controller: VentilationController, key: str) -> None:
        """Initialize the entity for the controller's entry."""
        entry_id = controller.entry.entry_id
        self.controller = controller
        self._attr_translation_key = key
        self._attr_unique_id = unique_id(entry_id, key)
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry_id)})

    @property
    def decision(self) -> Decision | None:
        """Decision of the last evaluation (None before the first one)."""
        return self.controller.current_decision


class PushedEntity(VentilationEntity):
    """Entity that writes its state after every evaluation of the controller."""

    async def async_added_to_hass(self) -> None:
        """Register with the controller."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.controller.async_add_listener(self._handle_controller_update)
        )

    @callback
    def _handle_controller_update(self) -> None:
        """Write the new state."""
        self.async_write_ha_state()
