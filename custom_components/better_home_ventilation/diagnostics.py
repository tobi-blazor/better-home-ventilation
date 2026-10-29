"""Diagnostics download of an entry (BHV-54, docs/spec/04-interface.md#diagnostics).

Content: the entry options, all subentries, the persisted controller state,
the current states of all configured entities (state, ``last_changed``,
``last_reported``), the last 100 decisions, the active problems and the
current warnings. The options contain no secrets; the notify services are
redacted anyway (they carry phone names), and person/device_tracker states
are reduced to ``home``/``not_home`` (no zone names). The download can be
fed to the replay tool (``tools/replay.py``) as its options file.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Final

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_NOTIFY_SERVICES
from .controller import BetterVentilationConfigEntry, VentilationController
from .engine.models import entity_domain, is_unavailable
from .engine.serialize import state_to_dict

#: Keys redacted everywhere in the download.
TO_REDACT: Final = frozenset({CONF_NOTIFY_SERVICES})
#: Domains whose state is a location; reduced to ``home``/``not_home``.
PRESENCE_DOMAINS: Final = frozenset({"person", "device_tracker"})


def jsonable(value: Any) -> Any:
    """Convert engine data (dataclasses, enums, datetimes, sets) to JSON types."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, timedelta):
        return value.total_seconds()
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: jsonable(getattr(value, field.name)) for field in fields(value)
        }
    if isinstance(value, Mapping):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, set | frozenset):
        return sorted(jsonable(item) for item in value)
    if isinstance(value, list | tuple):
        return [jsonable(item) for item in value]
    return value


def redact_presence(entity_id: str, state: str) -> str:
    """Reduce a person/device_tracker state to ``home``/``not_home``."""
    if entity_domain(entity_id) not in PRESENCE_DOMAINS or is_unavailable(state):
        return state
    return "home" if state == "home" else "not_home"


def _entity_ids(controller: VentilationController) -> list[str]:
    """Every entity the controller reads, warning source devices expanded."""
    options = controller.options
    return list(
        dict.fromkeys(
            [
                options.actuator,
                *([options.off_entity] if options.off_entity else []),
                *options.co2_sensors,
                *options.humidity_sensors,
                *options.presence_entities,
                *(condition.entity_id for condition in options.readiness_conditions),
                *(
                    entity_id
                    for source in options.warning_sources
                    for entity_id in source.entities
                ),
            ]
        )
    )


def _entity_states(
    hass: HomeAssistant, controller: VentilationController
) -> dict[str, Any]:
    states: dict[str, Any] = {}
    for entity_id in _entity_ids(controller):
        state = hass.states.get(entity_id)
        states[entity_id] = (
            None
            if state is None
            else {
                "state": redact_presence(entity_id, state.state),
                "last_changed": state.last_changed.isoformat(),
                "last_reported": state.last_reported.isoformat(),
            }
        )
    return states


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: BetterVentilationConfigEntry
) -> dict[str, Any]:
    """Return the diagnostics of a config entry."""
    controller = entry.runtime_data
    decision = controller.current_decision
    data = {
        "entry": {
            "title": entry.title,
            "version": entry.version,
            "minor_version": entry.minor_version,
            "options": dict(entry.options),
        },
        "subentries": [
            {
                "subentry_id": subentry.subentry_id,
                "subentry_type": subentry.subentry_type,
                "title": subentry.title,
                "unique_id": subentry.unique_id,
                "data": dict(subentry.data),
            }
            for subentry in entry.subentries.values()
        ],
        "controller": {
            "started": controller.started,
            "state_reset": controller.state_reset,
            "missing_entities": sorted(controller.missing_entities),
            "missing_notify_services": len(controller.missing_notify_services),
        },
        "stored_state": state_to_dict(controller.state),
        "entities": _entity_states(hass, controller),
        "problems": jsonable(decision.problems) if decision is not None else [],
        "warnings": {
            "active": jsonable(decision.warnings) if decision is not None else [],
            "overridden_incidents": (
                jsonable(decision.overridden_incidents) if decision is not None else []
            ),
            "awaiting_restart": decision.awaiting_restart
            if decision is not None
            else False,
        },
        "decisions": [jsonable(item) for item in controller.decisions],
    }
    return async_redact_data(data, TO_REDACT)
