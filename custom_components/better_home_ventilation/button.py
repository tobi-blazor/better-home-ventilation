"""Buttons: boost, pause, resume (BHV-20, BHV-21).

The buttons use the parameter entities (``boost_duration``, ``boost_level``,
``pause_duration``) and raise ``ServiceValidationError`` (``control_disabled``)
for boost and pause while control is switched off.
"""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .controller import BetterVentilationConfigEntry, VentilationController
from .engine.models import (
    BoostCommand,
    Command,
    PauseCommand,
    ResumeCommand,
    Source,
)
from .entity import VentilationEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BetterVentilationConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the buttons of an entry."""
    controller = entry.runtime_data
    async_add_entities(
        VentilationButton(controller, key) for key in ("boost", "pause", "resume")
    )


def button_command(controller: VentilationController, key: str) -> Command:
    """Return the command of a button with the values of the parameter entities."""
    if key == "boost":
        return BoostCommand(
            source=Source.BUTTON,
            default_duration=controller.boost_duration,
            default_level=controller.boost_level,
        )
    if key == "pause":
        return PauseCommand(source=Source.BUTTON, duration=controller.pause_duration)
    return ResumeCommand(source=Source.BUTTON)


class VentilationButton(VentilationEntity, ButtonEntity):
    """A manual command as a button."""

    async def async_press(self) -> None:
        """Send the command to the controller."""
        await self.controller.async_command(
            button_command(self.controller, str(self.translation_key))
        )
