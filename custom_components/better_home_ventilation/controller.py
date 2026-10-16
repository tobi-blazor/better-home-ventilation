"""Controller: the Home Assistant glue around the pure engine.

docs/spec/05-architecture.md#engine-and-glue: the controller collects the
inputs from the state machine, runs ``engine.evaluate`` and applies the
returned effects (writes, off/on, readiness corrections, events, timers). It
owns the subscriptions, the debouncer, the periodic timer, the evaluation lock,
the store and the decision log. Entities (M5) read :attr:`current_decision`
and register with :meth:`async_add_listener`.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timedelta
import logging
from typing import Any, Final

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import (
    CALLBACK_TYPE,
    Event,
    EventStateChangedData,
    HassJob,
    HomeAssistant,
    callback,
)
from homeassistant.exceptions import (
    ServiceValidationError,
    UnsupportedStorageVersionError,
)
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.event import (
    EventEntityRegistryUpdatedData,
    async_track_entity_registry_updated_event,
    async_track_point_in_utc_time,
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.storage import Store
from homeassistant.loader import async_get_integration
from homeassistant.util import dt as dt_util

from .actuator import ActuatorWriter, actuator_snapshot, off_actuator_snapshot
from .const import (
    CONF_AUTO_CORRECT,
    CONF_DEBOUNCE,
    CONF_ENTITY_ID,
    CONF_EVALUATION_INTERVAL,
    CONF_EXPECTED_STATE,
    DEFAULTS,
    DOMAIN,
    EVENT_READINESS_CORRECTED,
    MANUFACTURER,
    MODEL,
    SUBENTRY_READINESS_CONDITION,
)
from .engine import CommandRejected, apply_command, evaluate
from .engine.models import (
    Command,
    ControllerState,
    CorrectReadiness,
    Decision,
    Effect,
    FireEvent,
    InputValue,
    Options,
    ReadinessCondition,
    ScheduleAt,
    Snapshot,
    TurnOff,
    TurnOn,
    WriteFlow,
    is_unavailable,
)
from .engine.readiness import condition_met
from .engine.serialize import (
    STORE_MINOR_VERSION,
    STORE_VERSION,
    state_from_dict,
    state_to_dict,
)

_LOGGER = logging.getLogger(__name__)

#: Number of decisions kept in memory for diagnostics.
DECISION_LOG_SIZE: Final = 100

type BetterVentilationConfigEntry = ConfigEntry[VentilationController]


def store_key(entry_id: str) -> str:
    """Return the storage key of an entry (``better_home_ventilation.<entry_id>``)."""
    return f"{DOMAIN}.{entry_id}"


def create_store(hass: HomeAssistant, entry_id: str) -> Store[dict[str, Any]]:
    """Return the store of the controller state of an entry (BHV-50)."""
    return Store(
        hass,
        STORE_VERSION,
        store_key(entry_id),
        minor_version=STORE_MINOR_VERSION,
    )


def readiness_conditions(entry: ConfigEntry) -> list[ReadinessCondition]:
    """Return the readiness conditions of the entry's subentries."""
    return [
        ReadinessCondition(
            entity_id=str(subentry.data[CONF_ENTITY_ID]),
            expected_state=str(subentry.data[CONF_EXPECTED_STATE]),
            auto_correct=bool(subentry.data.get(CONF_AUTO_CORRECT, False)),
        )
        for subentry in entry.subentries.values()
        if subentry.subentry_type == SUBENTRY_READINESS_CONDITION
    ]


def _seconds(options: Mapping[str, Any], key: str) -> timedelta:
    """Return a duration option (stored in seconds) with its default."""
    value = options.get(key)
    return timedelta(seconds=float(value if value is not None else DEFAULTS[key]))


def _replace_entity(value: Any, old: str, new: str) -> Any:
    """Replace an entity id in an option value (a string or a list of strings)."""
    if value == old:
        return new
    if isinstance(value, list) and old in value:
        return [new if item == old else item for item in value]
    return value


class VentilationController:
    """Runs the engine for one config entry and controls one unit."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the controller from the entry options and subentries."""
        self.hass = hass
        self.entry = entry
        self.options = Options.from_mapping(entry.options, readiness_conditions(entry))
        self.device_id: str | None = None
        #: True if the store was corrupt or from a future version (repair in 0.2).
        self.state_reset = False
        self._state = ControllerState()
        self._store = create_store(hass, entry.entry_id)
        self._saved: dict[str, Any] | None = None
        self._lock = asyncio.Lock()
        self._follow_up: asyncio.Future[None] | None = None
        self._writer = ActuatorWriter(
            hass, self.options, self._lock, self._async_request_soon
        )
        self._started_at: datetime | None = None
        self._stopped = False
        self._decision: Decision | None = None
        self._decisions: deque[Decision] = deque(maxlen=DECISION_LOG_SIZE)
        self._listeners: list[Callable[[], None]] = []
        self._unsubs: list[CALLBACK_TYPE] = []
        self._stop_unsub: CALLBACK_TYPE | None = None
        self._schedule_unsub: CALLBACK_TYPE | None = None
        self._schedule_job = HassJob(
            self._async_scheduled,
            "better_home_ventilation scheduled evaluation",
            cancel_on_shutdown=True,
        )
        self._debouncer: Debouncer[Any] = Debouncer(
            hass,
            _LOGGER,
            cooldown=_seconds(entry.options, CONF_DEBOUNCE).total_seconds(),
            immediate=False,
            function=self.async_request_evaluation,
        )
        self._logged_unavailable: set[str] = set()
        self._logged_not_met: set[str] = set()

    # --- Public API ----------------------------------------------------------

    @property
    def state(self) -> ControllerState:
        """Current controller state."""
        return self._state

    @property
    def current_decision(self) -> Decision | None:
        """Decision of the last evaluation (None before the first one)."""
        return self._decision

    @property
    def decisions(self) -> tuple[Decision, ...]:
        """The last 100 decisions, oldest first (diagnostics)."""
        return tuple(self._decisions)

    @property
    def started(self) -> bool:
        """True once the controller runs (after Home Assistant started)."""
        return self._started_at is not None and not self._stopped

    @callback
    def async_add_listener(self, update_callback: Callable[[], None]) -> CALLBACK_TYPE:
        """Call ``update_callback`` after every evaluation; returns the unsubscribe."""
        self._listeners.append(update_callback)

        @callback
        def remove_listener() -> None:
            if update_callback in self._listeners:
                self._listeners.remove(update_callback)

        return remove_listener

    async def async_command(self, command: Command) -> None:
        """Apply a command, persist it and evaluate immediately.

        Raises ``ServiceValidationError`` (translation key = rejection code,
        ``control_disabled`` or ``level_out_of_range``) if the engine rejects
        the command.
        """
        try:
            self._state = apply_command(
                self._state, command, self.options, dt_util.now()
            )
        except CommandRejected as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key=err.code
            ) from err
        self._async_save_if_changed()
        await self.async_request_evaluation()

    # --- Lifecycle -------------------------------------------------------------

    async def async_setup(self) -> None:
        """Load the store, create the device and follow entity renames."""
        await self._async_load_state()
        integration = await async_get_integration(self.hass, DOMAIN)
        device = dr.async_get(self.hass).async_get_or_create(
            config_entry_id=self.entry.entry_id,
            identifiers={(DOMAIN, self.entry.entry_id)},
            name=self.entry.title,
            manufacturer=MANUFACTURER,
            model=MODEL,
            entry_type=dr.DeviceEntryType.SERVICE,
            sw_version=str(integration.version) if integration.version else None,
        )
        self.device_id = device.id
        self._unsubs.append(
            async_track_entity_registry_updated_event(
                self.hass, self._configured_entities(), self._async_registry_updated
            )
        )
        self._stop_unsub = self.hass.bus.async_listen_once(
            EVENT_HOMEASSISTANT_STOP, self._async_on_stop
        )

    async def async_start(self, _hass: HomeAssistant | None = None) -> None:
        """Start control once Home Assistant has started (02-control.md#startup)."""
        if self._stopped or self._started_at is not None:
            return
        self._started_at = dt_util.now()
        options = self.options
        debounced = {
            *options.co2_sensors,
            *options.humidity_sensors,
            *options.presence_entities,
        }
        immediate = {
            options.actuator,
            *([options.off_entity] if options.off_entity else []),
            *(condition.entity_id for condition in options.readiness_conditions),
        }
        debounced -= immediate
        if debounced:
            self._unsubs.append(
                async_track_state_change_event(
                    self.hass, sorted(debounced), self._async_input_changed
                )
            )
        self._unsubs.append(
            async_track_state_change_event(
                self.hass, sorted(immediate), self._async_unit_changed
            )
        )
        self._unsubs.append(
            async_track_time_interval(
                self.hass,
                self._async_periodic,
                _seconds(self.entry.options, CONF_EVALUATION_INTERVAL),
                name="better_home_ventilation periodic evaluation",
                cancel_on_shutdown=True,
            )
        )
        await self.async_request_evaluation()

    async def async_shutdown(self) -> None:
        """Cancel all listeners and timers and flush the store (unload)."""
        if self._stop_unsub is not None:
            self._stop_unsub()
            self._stop_unsub = None
        self._async_stop()
        async with self._lock:
            await self._store.async_save(state_to_dict(self._state))

    @callback
    def _async_on_stop(self, _event: Event) -> None:
        """Cancel the timers when Home Assistant stops (the store writes itself)."""
        self._stop_unsub = None  # a `listen_once` listener removes itself
        self._async_stop()

    @callback
    def _async_stop(self) -> None:
        """Cancel every subscription, timer and pending verification."""
        self._stopped = True
        unsubs, self._unsubs = self._unsubs, []
        for unsub in unsubs:
            unsub()
        self._debouncer.async_shutdown()
        self._cancel_schedule()
        self._writer.async_cancel()

    # --- Store -------------------------------------------------------------------

    async def _async_load_state(self) -> None:
        """Load the controller state; corrupt or future data starts with defaults."""
        version = STORE_VERSION
        try:
            data: object = await self._store.async_load()
        except UnsupportedStorageVersionError:
            data, version = {}, STORE_VERSION + 1
        result = state_from_dict(data, dt_util.now(), version=version)
        if result.reset:
            # TODO(0.2, BHV-50): raise the repair issue `state_reset`.
            _LOGGER.warning(
                "Stored state of %s could not be read; starting with defaults",
                self.entry.title,
            )
            self.state_reset = True
        if result.expired is not None:
            _LOGGER.info(
                "The %s of %s ended while Home Assistant was offline",
                result.expired,
                self.entry.title,
            )
        self._state = result.state
        self._saved = state_to_dict(result.state) if data else None

    @callback
    def _async_save_if_changed(self) -> None:
        """Save the persisted part of the state (delayed by 1 s) if it changed."""
        data = state_to_dict(self._state)
        if data != self._saved:
            self._saved = data
            self._store.async_delay_save(self._data_to_save, 1)

    @callback
    def _data_to_save(self) -> dict[str, Any]:
        """Return the data written by the delayed save."""
        return state_to_dict(self._state)

    # --- Triggers ------------------------------------------------------------------

    @callback
    def _async_input_changed(self, _event: Event[EventStateChangedData]) -> None:
        """Debounce changes of sensors and presence entities."""
        self._debouncer.async_schedule_call()

    @callback
    def _async_unit_changed(self, _event: Event[EventStateChangedData]) -> None:
        """Evaluate immediately on actuator, off actuator and readiness changes."""
        self._async_request_soon()

    async def _async_periodic(self, _now: datetime) -> None:
        """Periodic evaluation (``evaluation_interval``, BHV-17)."""
        await self.async_request_evaluation()

    async def _async_scheduled(self, _now: datetime) -> None:
        """Evaluate at the time requested by the engine (``ScheduleAt``)."""
        self._schedule_unsub = None
        await self.async_request_evaluation()

    @callback
    def _async_request_soon(self) -> None:
        """Request an evaluation from a callback."""
        if not self._stopped:
            self.entry.async_create_task(
                self.hass,
                self.async_request_evaluation(),
                "better_home_ventilation evaluation",
            )

    async def async_request_evaluation(self) -> None:
        """Run an evaluation; never overlapping (02-control.md#evaluation-loop).

        If the lock is held (an evaluation or a verification runs), exactly one
        follow-up evaluation is queued; further requests join it and return
        when it is done.
        """
        if self._stopped or self._started_at is None:
            return
        if not self._lock.locked():
            async with self._lock:
                await self._async_evaluate()
            return
        if self._follow_up is not None:
            await asyncio.shield(self._follow_up)
            return
        follow_up = self._follow_up = self.hass.loop.create_future()
        try:
            async with self._lock:
                self._follow_up = None
                await self._async_evaluate()
        finally:
            if self._follow_up is follow_up:  # cancelled while waiting
                self._follow_up = None
            if not follow_up.done():
                follow_up.set_result(None)

    # --- Evaluation ------------------------------------------------------------------

    def _snapshot(self, now: datetime, started_at: datetime) -> Snapshot:
        """Build the engine snapshot from the state machine."""
        options = self.options
        states: dict[str, InputValue] = {}
        for entity_id in (
            *options.co2_sensors,
            *options.humidity_sensors,
            *options.presence_entities,
            *(condition.entity_id for condition in options.readiness_conditions),
        ):
            if (state := self.hass.states.get(entity_id)) is not None:
                states[entity_id] = InputValue(
                    state=state.state, last_reported=state.last_reported
                )
        actuator_state = self.hass.states.get(options.actuator)
        actuator = actuator_snapshot(
            options,
            actuator_state,
            foreign_change=actuator_state is not None
            and not self._writer.is_own(actuator_state.context),
        )
        self._writer.observe(actuator)
        off_actuator = None
        if options.off_entity is not None:
            off_state = self.hass.states.get(options.off_entity)
            off_actuator = off_actuator_snapshot(
                off_state,
                foreign_change=off_state is not None
                and not self._writer.is_own(off_state.context),
            )
        return Snapshot(
            now=now,
            started_at=started_at,
            states=states,
            actuator=actuator,
            off_actuator=off_actuator,
            reported_problems=self._writer.problems,
        )

    async def _async_evaluate(self) -> None:
        """Run one evaluation and apply its effects (called with the lock held)."""
        if self._stopped or self._started_at is None:
            return
        snapshot = self._snapshot(dt_util.now(), self._started_at)
        decision, self._state, effects = evaluate(snapshot, self._state, self.options)
        self._decision = decision
        self._decisions.append(decision)
        self._async_save_if_changed()
        self._log_availability(snapshot)
        await self._async_apply(effects)
        for update_callback in list(self._listeners):
            update_callback()

    async def _async_apply(self, effects: Iterable[Effect]) -> None:
        """Execute the effects in order; schedule or cancel the next evaluation."""
        scheduled: datetime | None = None
        for effect in effects:
            if self._stopped:
                return
            match effect:
                case CorrectReadiness():
                    if await self._writer.async_correct(effect):
                        self._fire(
                            EVENT_READINESS_CORRECTED,
                            {
                                "entry_id": self.entry.entry_id,
                                "entity_id": effect.entity_id,
                                "from_state": effect.from_state,
                                "to_state": effect.to_state,
                            },
                        )
                case TurnOff(entity_id=entity_id):
                    await self._writer.async_turn_off(entity_id)
                case TurnOn(entity_id=entity_id):
                    await self._writer.async_turn_on(entity_id)
                case WriteFlow():
                    await self._writer.async_write(effect)
                case FireEvent(event_type=event_type, data=data):
                    self._fire(
                        event_type,
                        {
                            **data,
                            "entry_id": self.entry.entry_id,
                            "device_id": self.device_id,
                            "name": self.entry.title,
                        },
                    )
                case ScheduleAt(when=when):
                    scheduled = when
        if self._stopped:
            return
        self._cancel_schedule()
        if scheduled is not None:
            self._schedule_unsub = async_track_point_in_utc_time(
                self.hass, self._schedule_job, scheduled
            )

    @callback
    def _cancel_schedule(self) -> None:
        if self._schedule_unsub is not None:
            self._schedule_unsub()
            self._schedule_unsub = None

    @callback
    def _fire(self, event_type: str, data: Mapping[str, Any]) -> None:
        self.hass.bus.async_fire(event_type, dict(data))

    @callback
    def _log_availability(self, snapshot: Snapshot) -> None:
        """Log once when the actuator or a readiness condition fails and recovers.

        Quality rule ``log-when-unavailable``: one ``info`` message per change,
        nothing in between above ``debug``.
        """
        options = self.options
        unavailable = {
            entity_id
            for entity_id, state in (
                (options.actuator, snapshot.actuator.state),
                (
                    options.off_entity,
                    snapshot.off_actuator.state if snapshot.off_actuator else None,
                ),
            )
            if entity_id is not None and is_unavailable(state)
        }
        not_met = {
            condition.entity_id
            for condition in options.readiness_conditions
            if not condition_met(condition, snapshot.states.get(condition.entity_id))
        }
        for entity_id in sorted(unavailable - self._logged_unavailable):
            _LOGGER.info("%s is unavailable; writes are suspended", entity_id)
        for entity_id in sorted(self._logged_unavailable - unavailable):
            _LOGGER.info("%s is available again", entity_id)
        for entity_id in sorted(not_met - self._logged_not_met):
            _LOGGER.info(
                "Readiness condition on %s is not met; writes are suspended",
                entity_id,
            )
        for entity_id in sorted(self._logged_not_met - not_met):
            _LOGGER.info("Readiness condition on %s is met again", entity_id)
        self._logged_unavailable = unavailable
        self._logged_not_met = not_met

    # --- Entity registry -----------------------------------------------------------------

    def _configured_entities(self) -> list[str]:
        """Every entity id configured in the options and readiness subentries."""
        options = self.options
        entities = [
            options.actuator,
            *([options.off_entity] if options.off_entity else []),
            *options.co2_sensors,
            *options.humidity_sensors,
            *options.presence_entities,
            *(condition.entity_id for condition in options.readiness_conditions),
        ]
        return list(dict.fromkeys(entities))

    @callback
    def _async_registry_updated(
        self, event: Event[EventEntityRegistryUpdatedData]
    ) -> None:
        """Follow entity id changes; removed entities keep the entry running.

        docs/spec/01-configuration.md#renamed-and-removed-entities: a rename is
        written to the options and readiness subentries; the update listener
        then reloads the entry.
        """
        data = event.data
        if data["action"] == "remove":
            # TODO(0.2, BHV-55): raise the repair issue `missing_entity`.
            _LOGGER.info(
                "%s was removed; %s continues with the remaining entities",
                data["entity_id"],
                self.entry.title,
            )
            return
        if data["action"] != "update" or "old_entity_id" not in data:
            return
        old, new = data["old_entity_id"], data["entity_id"]
        _LOGGER.debug("Following the rename of %s to %s", old, new)
        options = {
            key: _replace_entity(value, old, new)
            for key, value in self.entry.options.items()
        }
        if options != dict(self.entry.options):
            self.hass.config_entries.async_update_entry(self.entry, options=options)
        for subentry in list(self.entry.subentries.values()):
            self._async_rename_in_subentry(subentry, old, new)

    @callback
    def _async_rename_in_subentry(
        self, subentry: ConfigSubentry, old: str, new: str
    ) -> None:
        """Replace a renamed entity id in a readiness subentry."""
        if subentry.data.get(CONF_ENTITY_ID) != old:
            return
        self.hass.config_entries.async_update_subentry(
            self.entry,
            subentry,
            data={**subentry.data, CONF_ENTITY_ID: new},
            unique_id=new if subentry.unique_id == old else subentry.unique_id,
        )
