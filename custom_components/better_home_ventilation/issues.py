"""Repair issues ``missing_entity_<id>`` and ``state_reset_<entry_id>`` (BHV-55, BHV-50).

docs/spec/04-interface.md#repairs. The module is not called ``repairs.py``:
that name is the repairs platform of an integration (fix flows).

``missing_entity``: an entity configured in the options or a subentry (or the
device of a warning source) that is neither in the state machine nor in the
registry. Checked once 5 minutes after Home Assistant started (entities may
load late), and whenever a configured entity or device is removed from its
registry. The issue is deleted when the entity exists again (state or registry
entry) or no loaded entry uses it any more (after the options were changed).
There is one issue per entity for all entries that use it; each missing entity
is also the problem ``missing_entity`` of its entry.

``state_reset``: informational, raised when the stored controller state could
not be read; the user can ignore it, and it is not kept over a restart.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
from typing import Final

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import (
    CALLBACK_TYPE,
    Event,
    EventStateChangedData,
    HassJob,
    HomeAssistant,
    callback,
)
from homeassistant.helpers import (
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
)
from homeassistant.helpers.event import (
    EventEntityRegistryUpdatedData,
    async_call_later,
    async_track_device_registry_updated_event,
    async_track_entity_registry_updated_event,
    async_track_state_change_event,
)
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.translation import async_get_cached_translations
from homeassistant.util import dt as dt_util
from homeassistant.util.hass_dict import HassKey

from .const import (
    CONF_ACTUATOR,
    CONF_CO2_SENSORS,
    CONF_ENTITY_ID,
    CONF_HUMIDITY_SENSORS,
    CONF_OFF_ENTITY,
    CONF_PRESENCE_ENTITIES,
    CONF_SOURCE_DEVICE,
    CONF_SOURCE_ENTITY,
    DOMAIN,
    SUBENTRY_READINESS_CONDITION,
    SUBENTRY_WARNING_SOURCE,
)

_LOGGER = logging.getLogger(__name__)

ISSUE_MISSING_ENTITY: Final = "missing_entity"
ISSUE_STATE_RESET: Final = "state_reset"
#: Time after Home Assistant started before a missing entity is reported.
MISSING_ENTITY_DELAY: Final = timedelta(minutes=5)

#: Option keys that hold entity ids, in the order they are listed.
ENTITY_OPTIONS: Final = (
    CONF_ACTUATOR,
    CONF_OFF_ENTITY,
    CONF_CO2_SENSORS,
    CONF_HUMIDITY_SENSORS,
    CONF_PRESENCE_ENTITIES,
)

_COMMON: Final = f"component.{DOMAIN}.common."

#: When Home Assistant started (set by :func:`async_setup_issues`).
_HA_STARTED: HassKey[datetime] = HassKey(f"{DOMAIN}_ha_started")
#: Missing entities per entry: entry id -> (title, {id: where}).
_MISSING: HassKey[dict[str, tuple[str, dict[str, str]]]] = HassKey(
    f"{DOMAIN}_missing_entities"
)


@dataclass(frozen=True, kw_only=True)
class Target:
    """A configured entity or warning source device.

    ``where`` is the ``common`` translation key of the place it is configured
    in (``issue_where_<where>``), ``title`` the subentry title for readiness
    conditions and warning sources.
    """

    target_id: str
    where: str
    title: str = ""
    device: bool = False


def configured_targets(entry: ConfigEntry) -> dict[str, Target]:
    """Return every entity and device configured in the options and subentries.

    The first place an id is configured in wins.
    """
    targets: dict[str, Target] = {}

    def add(target: Target) -> None:
        targets.setdefault(target.target_id, target)

    for key in ENTITY_OPTIONS:
        value = entry.options.get(key)
        values = [value] if isinstance(value, str) else list(value or ())
        for entity_id in values:
            if entity_id:
                add(Target(target_id=str(entity_id), where=key))
    for subentry in entry.subentries.values():
        if subentry.subentry_type == SUBENTRY_READINESS_CONDITION:
            if entity_id := subentry.data.get(CONF_ENTITY_ID):
                add(
                    Target(
                        target_id=str(entity_id),
                        where=SUBENTRY_READINESS_CONDITION,
                        title=subentry.title,
                    )
                )
        elif subentry.subentry_type == SUBENTRY_WARNING_SOURCE:
            if entity_id := subentry.data.get(CONF_SOURCE_ENTITY):
                add(
                    Target(
                        target_id=str(entity_id),
                        where=SUBENTRY_WARNING_SOURCE,
                        title=subentry.title,
                    )
                )
            elif device_id := subentry.data.get(CONF_SOURCE_DEVICE):
                add(
                    Target(
                        target_id=str(device_id),
                        where=SUBENTRY_WARNING_SOURCE,
                        title=subentry.title,
                        device=True,
                    )
                )
    return targets


@callback
def async_setup_issues(hass: HomeAssistant) -> None:
    """Remember when Home Assistant started (start of the 5 minute wait)."""

    @callback
    def started(_hass: HomeAssistant) -> None:
        hass.data.setdefault(_HA_STARTED, dt_util.utcnow())

    async_at_started(hass, started)


@callback
def _cached_common(hass: HomeAssistant) -> dict[str, str]:
    """Return the cached ``common`` texts in the server language, else English."""
    return async_get_cached_translations(
        hass, hass.config.language, "common", DOMAIN
    ) or async_get_cached_translations(hass, "en", "common", DOMAIN)


def _where_text(common: Mapping[str, str], target: Target) -> str:
    """Return the translated place of a target (``issue_where_<where>``)."""
    template = common.get(f"{_COMMON}issue_where_{target.where}", target.where)
    try:
        return template.format(title=target.title)
    except KeyError, IndexError, ValueError:
        return template


@callback
def _async_sync_missing(hass: HomeAssistant) -> None:
    """Create or delete the ``missing_entity_<id>`` issues of all entries."""
    usages: dict[str, list[str]] = {}
    for title, missing in hass.data.get(_MISSING, {}).values():
        for target_id, where in missing.items():
            usages.setdefault(target_id, []).append(f"{title}: {where}")
    prefix = f"{ISSUE_MISSING_ENTITY}_"
    registry = ir.async_get(hass)
    for domain, issue_id in list(registry.issues):
        if (
            domain == DOMAIN
            and issue_id.startswith(prefix)
            and issue_id.removeprefix(prefix) not in usages
        ):
            ir.async_delete_issue(hass, DOMAIN, issue_id)
    for target_id, places in usages.items():
        ir.async_create_issue(
            hass,
            DOMAIN,
            f"{prefix}{target_id}",
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_MISSING_ENTITY,
            translation_placeholders={
                "entity": target_id,
                "usages": "; ".join(sorted(places)),
            },
        )


@callback
def async_remove_entry_issues(hass: HomeAssistant, entry_id: str) -> None:
    """Delete the issues of a removed entry (``state_reset``, its missing entities)."""
    ir.async_delete_issue(hass, DOMAIN, state_reset_issue_id(entry_id))
    registry = hass.data.get(_MISSING, {})
    if entry_id in registry:
        del registry[entry_id]
        _async_sync_missing(hass)


def state_reset_issue_id(entry_id: str) -> str:
    """Return the id of the ``state_reset`` issue of an entry."""
    return f"{ISSUE_STATE_RESET}_{entry_id}"


@callback
def async_create_state_reset_issue(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Raise the informational ``state_reset`` issue of an entry (BHV-50)."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        state_reset_issue_id(entry.entry_id),
        is_fixable=False,
        is_persistent=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_STATE_RESET,
        translation_placeholders={"entry": entry.title},
    )


class MissingEntities:
    """Finds the configured entities of one entry that no longer exist (BHV-55)."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        on_change: Callable[[], None],
    ) -> None:
        """Collect the configured entities and devices of the entry."""
        self.hass = hass
        self.entry = entry
        self.targets = configured_targets(entry)
        self._on_change = on_change
        #: Ids of the missing entities and devices (problem ``missing_entity``).
        self.missing: frozenset[str] = frozenset()
        self._unsubs: list[CALLBACK_TYPE] = []
        self._state_unsub: CALLBACK_TYPE | None = None
        self._check_unsub: CALLBACK_TYPE | None = None
        self._stopped = False

    # --- Lifecycle --------------------------------------------------------------

    @callback
    def async_start(self) -> None:
        """Follow the registries; check all targets 5 min after HA started.

        Entities that the previous controller of the entry (before a reload)
        found missing and that are still missing stay reported without the
        wait.
        """
        if self._stopped:
            return
        entities = sorted(
            target_id for target_id, target in self.targets.items() if not target.device
        )
        if entities:
            self._unsubs.append(
                async_track_entity_registry_updated_event(
                    self.hass, entities, self._async_entity_registry_updated
                )
            )
        devices = sorted(
            target_id for target_id, target in self.targets.items() if target.device
        )
        if devices:
            self._unsubs.append(
                async_track_device_registry_updated_event(
                    self.hass, devices, self._async_device_registry_updated
                )
            )
        previous = self.hass.data.get(_MISSING, {}).get(self.entry.entry_id)
        if previous is not None:
            self._async_set(
                frozenset(
                    target_id
                    for target_id in previous[1]
                    if target_id in self.targets and self._is_missing(target_id)
                ),
                force=True,
            )
        started = self.hass.data.get(_HA_STARTED, dt_util.utcnow())
        delay = (started + MISSING_ENTITY_DELAY - dt_util.utcnow()).total_seconds()
        self._check_unsub = async_call_later(
            self.hass,
            max(delay, 0),
            HassJob(
                self._async_check,
                "better_home_ventilation missing entity check",
                cancel_on_shutdown=True,
            ),
        )

    @callback
    def async_stop(self) -> None:
        """Stop following; the issues stay for a reload, not for a disabled entry."""
        self._stopped = True
        if self._check_unsub is not None:
            self._check_unsub()
            self._check_unsub = None
        if self._state_unsub is not None:
            self._state_unsub()
            self._state_unsub = None
        unsubs, self._unsubs = self._unsubs, []
        for unsub in unsubs:
            unsub()
        registry = self.hass.data.get(_MISSING, {})
        if self.entry.disabled_by is not None and self.entry.entry_id in registry:
            del registry[self.entry.entry_id]
            _async_sync_missing(self.hass)

    # --- Checks -------------------------------------------------------------------

    def _is_missing(self, target_id: str) -> bool:
        """Return True if a target exists neither as a state nor in its registry."""
        target = self.targets[target_id]
        if target.device:
            return dr.async_get(self.hass).async_get(target_id) is None
        return (
            self.hass.states.get(target_id) is None
            and er.async_get(self.hass).async_get(target_id) is None
        )

    @callback
    def _async_check(self, _now: datetime) -> None:
        """Check every target (5 minutes after Home Assistant started)."""
        self._check_unsub = None
        missing = frozenset(
            target_id for target_id in self.targets if self._is_missing(target_id)
        )
        self._async_set(missing)

    @callback
    def _async_entity_registry_updated(
        self, event: Event[EventEntityRegistryUpdatedData]
    ) -> None:
        """Report a removed entity at once; drop a missing one that is back.

        A rename of a configured entity is followed by the controller (the
        options change and the entry reloads).
        """
        data = event.data
        entity_id = data["entity_id"]
        if entity_id not in self.targets:
            return
        if data["action"] == "remove":
            self._async_set(self.missing | {entity_id})
        elif entity_id in self.missing and not self._is_missing(entity_id):
            self._async_set(self.missing - {entity_id})

    @callback
    def _async_device_registry_updated(
        self, event: Event[dr.EventDeviceRegistryUpdatedData]
    ) -> None:
        """Report a removed source device at once; drop one that is back."""
        data = event.data
        device_id = data["device_id"]
        if data["action"] == "remove":
            self._async_set(self.missing | {device_id})
        elif device_id in self.missing and not self._is_missing(device_id):
            self._async_set(self.missing - {device_id})

    @callback
    def _async_state_changed(self, event: Event[EventStateChangedData]) -> None:
        """Drop a missing entity that has a state again."""
        if event.data["new_state"] is not None:
            self._async_set(self.missing - {event.data["entity_id"]})

    @callback
    def _async_set(self, missing: frozenset[str], *, force: bool = False) -> None:
        """Update the missing targets, their issues and the state subscription."""
        if self._stopped or (missing == self.missing and not force):
            return
        for target_id in sorted(missing - self.missing):
            _LOGGER.warning(
                "%s, configured in %s, does not exist", target_id, self.entry.title
            )
        changed = missing != self.missing
        self.missing = missing
        if self._state_unsub is not None:
            self._state_unsub()
            self._state_unsub = None
        entities = sorted(
            target_id for target_id in missing if not self.targets[target_id].device
        )
        if entities:
            self._state_unsub = async_track_state_change_event(
                self.hass, entities, self._async_state_changed
            )
        common = _cached_common(self.hass)
        registry = self.hass.data.setdefault(_MISSING, {})
        registry[self.entry.entry_id] = (
            self.entry.title,
            {
                target_id: _where_text(common, self.targets[target_id])
                for target_id in sorted(missing)
            },
        )
        if not missing:
            del registry[self.entry.entry_id]
        _async_sync_missing(self.hass)
        if changed:
            self._on_change()
