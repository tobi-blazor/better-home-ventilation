"""Boost level (parameter entity of the boost button, BHV-20)."""

from __future__ import annotations

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberMode,
    RestoreNumber,
)
from homeassistant.const import EntityCategory, UnitOfVolumeFlowRate
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .controller import BetterVentilationConfigEntry, VentilationController
from .entity import VentilationEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BetterVentilationConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the boost level of an entry."""
    async_add_entities([BoostLevelNumber(entry.runtime_data)])


class BoostLevelNumber(VentilationEntity, RestoreNumber):
    """Flow of the boost button; restored after a restart.

    The range is ``min_flow``…``max_flow`` with step ``flow_quantum``. A
    restored value outside the current range is clamped; without a restored
    value the option ``boost_default_level`` is used.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_device_class = NumberDeviceClass.VOLUME_FLOW_RATE
    _attr_native_unit_of_measurement = UnitOfVolumeFlowRate.CUBIC_METERS_PER_HOUR
    _attr_mode = NumberMode.BOX

    def __init__(self, controller: VentilationController) -> None:
        """Initialize the number from the options."""
        super().__init__(controller, "boost_level")
        options = controller.options
        self._attr_native_min_value = options.min_flow
        self._attr_native_max_value = options.max_flow
        self._attr_native_step = options.flow_quantum

    def _set(self, value: float) -> None:
        value = min(max(value, self.native_min_value), self.native_max_value)
        self._attr_native_value = value
        self.controller.boost_level = value

    async def async_added_to_hass(self) -> None:
        """Restore the last level, otherwise use ``boost_default_level``."""
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last is not None and last.native_value is not None:
            self._set(float(last.native_value))
        else:
            self._set(self.controller.options.boost_default_level)

    async def async_set_native_value(self, value: float) -> None:
        """Set a new level."""
        self._set(value)
        self.async_write_ha_state()
