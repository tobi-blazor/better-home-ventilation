"""Control switch (BHV-03) and safety override switch (BHV-42).

Their states are kept in the controller store.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .controller import BetterVentilationConfigEntry, VentilationController
from .engine.models import (
    ControlSwitchCommand,
    OverrideSafetyCommand,
    RemoveOverrideCommand,
    Source,
)
from .entity import PushedEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BetterVentilationConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the control switch and, with warning sources, the safety override."""
    controller = entry.runtime_data
    entities: list[SwitchEntity] = [ControlSwitch(controller)]
    if controller.has_warning_sources:
        entities.append(SafetyOverrideSwitch(controller))
    async_add_entities(entities)


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


class SafetyOverrideSwitch(PushedEntity, SwitchEntity):
    """On while the active warnings are overridden ("Keep ventilating").

    Turning on is ``override_safety()``; turning off removes the overrides, so
    an active warning shuts the unit down again. Without an active warning,
    turning on has no effect and the switch stays off (04-interface.md).
    """

    def __init__(self, controller: VentilationController) -> None:
        """Initialize the switch."""
        super().__init__(controller, "safety_override")

    @property
    def is_on(self) -> bool:
        """Return True if warnings are overridden."""
        return bool(self.controller.state.safety.overridden)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Override the active warnings."""
        try:
            await self.controller.async_command(
                OverrideSafetyCommand(source=Source.BUTTON)
            )
        except ServiceValidationError:
            # No active warning: nothing to override, the switch stays off.
            self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Remove the overrides; an active warning shuts down again."""
        await self.controller.async_command(RemoveOverrideCommand(source=Source.BUTTON))
