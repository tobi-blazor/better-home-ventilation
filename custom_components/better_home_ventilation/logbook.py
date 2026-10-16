"""Logbook descriptions of mode changes (docs/spec/04-interface.md#logbook).

The logbook describes events synchronously and has no translation support for
descriptions (``homeassistant.components.logbook``, HA 2026.4 and 2026.10; no
core logbook platform translates its messages). The message is therefore built
from the integration's cached backend translations in the language of the
Home Assistant server (``hass.config.language``): the sentence templates come
from the ``common`` category, the mode names from the ``mode`` sensor's state
translations. Translations are loaded when the integration is set up; without
them English is used, and the raw keys as a last resort.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from homeassistant.components.logbook import (
    LOGBOOK_ENTRY_ENTITY_ID,
    LOGBOOK_ENTRY_MESSAGE,
    LOGBOOK_ENTRY_NAME,
)
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.translation import async_get_cached_translations
from homeassistant.util import dt as dt_util

from .const import DOMAIN, EVENT_MODE_CHANGED
from .engine.models import Mode, Trigger
from .entity import unique_id

_COMMON = f"component.{DOMAIN}.common."
_MODE_STATE = f"component.{DOMAIN}.entity.sensor.mode.state."


def _format_time(ends_at: datetime, fired: datetime | None) -> str:
    """Return ``HH:MM``, with the date if it is not the day of the event."""
    local = dt_util.as_local(ends_at)
    if fired is not None and dt_util.as_local(fired).date() != local.date():
        return local.strftime("%Y-%m-%d %H:%M")
    return local.strftime("%H:%M")


@callback
def _cached_translations(hass: HomeAssistant, category: str) -> dict[str, str]:
    """Return the cached translations in the server language, else English."""
    return async_get_cached_translations(
        hass, hass.config.language, category, DOMAIN
    ) or async_get_cached_translations(hass, "en", category, DOMAIN)


def describe_mode_change(
    data: Mapping[str, Any],
    common: Mapping[str, str],
    entity: Mapping[str, str],
    fired: datetime | None = None,
) -> str:
    """Return the logbook message of a ``mode_changed`` event.

    Examples (en): "switched to Boost (button, until 18:30)",
    "back to Automatic (timeout)".
    """

    def text(key: str, **placeholders: str) -> str:
        template = common.get(_COMMON + key, key)
        try:
            return template.format(**placeholders)
        except KeyError, IndexError:
            return template

    to_mode = str(data.get("to_mode"))
    mode = entity.get(_MODE_STATE + to_mode, to_mode)
    trigger = data.get("trigger")
    source = data.get("source")
    if trigger == Trigger.COMMAND and source:
        details = [text(f"logbook_source_{source}")]
    else:
        details = [text(f"logbook_trigger_{trigger}")]
    if ends_at := data.get("ends_at"):
        parsed = dt_util.parse_datetime(str(ends_at))
        if parsed is not None:
            details.append(text("logbook_until", time=_format_time(parsed, fired)))
    key = "logbook_back_to" if to_mode == Mode.AUTOMATIC else "logbook_switched_to"
    return text(key, mode=mode, details=", ".join(details))


@callback
def async_describe_events(
    hass: HomeAssistant,
    async_describe_event: Callable[[str, str, Callable[[Event], dict[str, Any]]], None],
) -> None:
    """Describe the mode change events of the integration."""

    @callback
    def async_describe_mode_changed(event: Event) -> dict[str, Any]:
        data = event.data
        fired_timestamp = getattr(event, "time_fired_timestamp", None)
        message = describe_mode_change(
            data,
            _cached_translations(hass, "common"),
            _cached_translations(hass, "entity"),
            dt_util.utc_from_timestamp(fired_timestamp)
            if fired_timestamp is not None
            else None,
        )
        name = data.get("name")
        if (device_id := data.get("device_id")) and (
            device := dr.async_get(hass).async_get(device_id)
        ):
            name = device.name_by_user or device.name or name
        description: dict[str, Any] = {
            LOGBOOK_ENTRY_NAME: name,
            LOGBOOK_ENTRY_MESSAGE: message,
        }
        if (entry_id := data.get("entry_id")) and (
            entity_id := er.async_get(hass).async_get_entity_id(
                "sensor", DOMAIN, unique_id(entry_id, "mode")
            )
        ):
            description[LOGBOOK_ENTRY_ENTITY_ID] = entity_id
        return description

    async_describe_event(DOMAIN, EVENT_MODE_CHANGED, async_describe_mode_changed)
