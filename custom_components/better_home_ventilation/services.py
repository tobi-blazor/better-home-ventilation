"""Services ``boost``, ``pause``, ``resume`` and ``override_safety`` (BHV-22, BHV-42).

docs/spec/04-interface.md#services: registered once in ``async_setup``. Every
service takes an optional ``config_entry_id``; without it the only entry is
used. Errors are translated ``ServiceValidationError``s.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv

if TYPE_CHECKING:
    # Since HA 2026.9 `voluptuous` is an alias of `probatio` at runtime
    # (docs/spec/05-architecture.md, compatibility); type check against it.
    import probatio as vol
else:
    import voluptuous as vol

from .const import (
    ATTR_CONFIG_ENTRY_ID,
    ATTR_DURATION,
    ATTR_LEVEL,
    DOMAIN,
    SERVICE_BOOST,
    SERVICE_OVERRIDE_SAFETY,
    SERVICE_PAUSE,
    SERVICE_RESUME,
)
from .controller import BetterVentilationConfigEntry, VentilationController
from .engine.models import (
    BoostCommand,
    OverrideSafetyCommand,
    PauseCommand,
    ResumeCommand,
    Source,
)

_ENTRY = {vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string}

BOOST_SCHEMA = vol.Schema(
    {
        **_ENTRY,
        vol.Optional(ATTR_DURATION): vol.All(
            vol.Coerce(int), vol.Range(min=5, max=720)
        ),
        # The range min_flow…max_flow is checked by the engine
        # (translated `level_out_of_range`).
        vol.Optional(ATTR_LEVEL): vol.Coerce(float),
    }
)
PAUSE_SCHEMA = vol.Schema(
    {
        **_ENTRY,
        vol.Optional(ATTR_DURATION): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=10080)
        ),
    }
)
RESUME_SCHEMA = vol.Schema(_ENTRY)
OVERRIDE_SAFETY_SCHEMA = vol.Schema(_ENTRY)


def _error(key: str, **placeholders: str) -> ServiceValidationError:
    return ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key=key,
        translation_placeholders=placeholders or None,
    )


@callback
def async_resolve_controller(
    hass: HomeAssistant, entry_id: str | None
) -> VentilationController:
    """Return the controller of the addressed entry.

    Without ``entry_id`` exactly one (enabled) entry must exist
    (``entry_required``). An unknown id raises ``entry_not_found``, an entry
    that is not loaded ``entry_not_loaded``.
    """
    entry: BetterVentilationConfigEntry | None
    if entry_id is None:
        entries = hass.config_entries.async_entries(
            DOMAIN, include_ignore=False, include_disabled=False
        )
        if len(entries) != 1:
            raise _error("entry_required")
        entry = entries[0]
    else:
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN:
            raise _error("entry_not_found", entry_id=entry_id)
    if entry.state is not ConfigEntryState.LOADED:
        raise _error("entry_not_loaded", entry_title=entry.title)
    return entry.runtime_data


def _minutes(call: ServiceCall) -> timedelta | None:
    minutes = call.data.get(ATTR_DURATION)
    return timedelta(minutes=minutes) if minutes is not None else None


async def _async_boost(call: ServiceCall) -> None:
    controller = async_resolve_controller(
        call.hass, call.data.get(ATTR_CONFIG_ENTRY_ID)
    )
    await controller.async_command(
        BoostCommand(
            source=Source.SERVICE,
            duration=_minutes(call),
            level=call.data.get(ATTR_LEVEL),
            default_duration=controller.boost_duration,
            default_level=controller.boost_level,
        )
    )


async def _async_pause(call: ServiceCall) -> None:
    controller = async_resolve_controller(
        call.hass, call.data.get(ATTR_CONFIG_ENTRY_ID)
    )
    await controller.async_command(
        PauseCommand(source=Source.SERVICE, duration=_minutes(call))
    )


async def _async_resume(call: ServiceCall) -> None:
    controller = async_resolve_controller(
        call.hass, call.data.get(ATTR_CONFIG_ENTRY_ID)
    )
    await controller.async_command(ResumeCommand(source=Source.SERVICE))


async def _async_override_safety(call: ServiceCall) -> None:
    """Lift the safety shutdown for the current warnings (``no_active_warning``)."""
    controller = async_resolve_controller(
        call.hass, call.data.get(ATTR_CONFIG_ENTRY_ID)
    )
    await controller.async_command(OverrideSafetyCommand(source=Source.SERVICE))


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the services of the integration."""
    hass.services.async_register(DOMAIN, SERVICE_BOOST, _async_boost, BOOST_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_PAUSE, _async_pause, PAUSE_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_RESUME, _async_resume, RESUME_SCHEMA)
    hass.services.async_register(
        DOMAIN,
        SERVICE_OVERRIDE_SAFETY,
        _async_override_safety,
        OVERRIDE_SAFETY_SCHEMA,
    )
