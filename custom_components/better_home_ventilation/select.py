"""Selects: profile (BHV-26), boost duration and pause duration.

The profile is kept in the controller store. The two duration selects are
parameter entities (``EntityCategory.CONFIG``) that restore their last option
after a restart and pass their value to the controller, which uses it for the
buttons and as the boost duration default of the service.
"""

from __future__ import annotations

from datetime import timedelta

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import BOOST_DURATION_OPTIONS, PAUSE_DURATION_DEFAULT, PAUSE_DURATION_HOURS
from .controller import BetterVentilationConfigEntry, VentilationController
from .engine.models import Profile, ProfileCommand, Source
from .entity import PushedEntity, VentilationEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BetterVentilationConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the selects of an entry."""
    controller = entry.runtime_data
    async_add_entities(
        [
            ProfileSelect(controller),
            BoostDurationSelect(controller),
            PauseDurationSelect(controller),
        ]
    )


def nearest_boost_duration(duration: timedelta) -> str:
    """Return the boost duration option nearest to a duration (ties: shorter)."""
    minutes = duration.total_seconds() / 60
    return min(BOOST_DURATION_OPTIONS, key=lambda option: abs(int(option) - minutes))


class ProfileSelect(PushedEntity, SelectEntity):
    """Strategy of the automatic demand; accepted while control is off."""

    def __init__(self, controller: VentilationController) -> None:
        """Initialize the select."""
        super().__init__(controller, "profile")
        self._attr_options = [str(profile) for profile in Profile]

    @property
    def current_option(self) -> str:
        """Return the active profile."""
        return str(self.controller.state.profile)

    async def async_select_option(self, option: str) -> None:
        """Change the profile."""
        await self.controller.async_command(
            ProfileCommand(source=Source.BUTTON, profile=Profile(option))
        )


class _ParameterSelect(VentilationEntity, SelectEntity, RestoreEntity):
    """Parameter select that restores its last option."""

    _attr_entity_category = EntityCategory.CONFIG

    def _initial_option(self) -> str:
        """Return the option used without a valid restored state."""
        raise NotImplementedError

    def _apply(self, option: str) -> None:
        """Pass the option to the controller."""
        raise NotImplementedError

    def _set(self, option: str) -> None:
        self._attr_current_option = option
        self._apply(option)

    async def async_added_to_hass(self) -> None:
        """Restore the last option, otherwise use the initial one."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state in self.options:
            self._set(last.state)
        else:
            self._set(self._initial_option())

    async def async_select_option(self, option: str) -> None:
        """Select an option."""
        self._set(option)
        self.async_write_ha_state()


class BoostDurationSelect(_ParameterSelect):
    """Duration of the boost button (minutes)."""

    def __init__(self, controller: VentilationController) -> None:
        """Initialize the select."""
        super().__init__(controller, "boost_duration")
        self._attr_options = list(BOOST_DURATION_OPTIONS)

    def _initial_option(self) -> str:
        return nearest_boost_duration(self.controller.options.boost_default_duration)

    def _apply(self, option: str) -> None:
        self.controller.boost_duration = timedelta(minutes=int(option))


class PauseDurationSelect(_ParameterSelect):
    """Duration of the pause button (``indefinite`` = until resumed)."""

    def __init__(self, controller: VentilationController) -> None:
        """Initialize the select."""
        super().__init__(controller, "pause_duration")
        self._attr_options = list(PAUSE_DURATION_HOURS)

    def _initial_option(self) -> str:
        return PAUSE_DURATION_DEFAULT

    def _apply(self, option: str) -> None:
        hours = PAUSE_DURATION_HOURS[option]
        self.controller.pause_duration = (
            timedelta(hours=hours) if hours is not None else None
        )
