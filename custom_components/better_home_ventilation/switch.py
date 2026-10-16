"""Control switch (BHV-03); its state is kept in the controller store."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .controller import BetterVentilationConfigEntry, VentilationController
from .engine.models import ControlSwitchCommand, Source
from .entity import PushedEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BetterVentilationConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the control switch of an entry."""
    async_add_entities([ControlSwitch(entry.runtime_data)])


class ControlSwitch(PushedEntity, SwitchEntity):
    """Off: the integration writes nothing and rejects boost and pause."""

    def __init__(self, controller: VentilationController) -> None:
        """Initialize the switch."""
        super().__init__(controller, "control")

    @property
    def is_on(self) -> bool:
        """Return True if control is enabled."""
        return self.controller.state.control_enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable control."""
        await self.controller.async_command(
            ControlSwitchCommand(source=Source.BUTTON, enabled=True)
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable control (ends a boost or pause)."""
        await self.controller.async_command(
            ControlSwitchCommand(source=Source.BUTTON, enabled=False)
        )
