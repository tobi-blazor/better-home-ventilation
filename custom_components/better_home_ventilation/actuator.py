"""Actuator adapters: service calls, own contexts and write verification.

docs/spec/02-control.md#writing: every service call uses ``blocking=True``, a
timeout of 10 s and a context created by the integration, so own state changes
can be told apart from external ones. 30 s after a write the actuator value is
compared with the written value; one retry, then problem ``write_mismatch``.
Failed service calls raise problem ``write_failed``.

The pure part of the mapping (flow <-> native value) lives in
``engine/mapping.py``; this module only talks to Home Assistant.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
import logging
import math
from typing import Any, Final

from homeassistant.core import (
    CALLBACK_TYPE,
    Context,
    HassJob,
    HomeAssistant,
    State,
    callback,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_call_later
import voluptuous as vol

from .engine.mapping import Native, current_native, native_to_flow
from .engine.models import (
    ActuatorDomain,
    ActuatorSnapshot,
    CorrectReadiness,
    OffActuatorSnapshot,
    Options,
    ProblemKey,
    WriteFlow,
    entity_domain,
    is_unavailable,
)
from .engine.options import ACTUATOR_UNIT_PERCENT, OFF_STATE_ON

_LOGGER = logging.getLogger(__name__)

#: Timeout of every service call in seconds.
SERVICE_TIMEOUT: Final = 10
#: Delay between a write and its verification.
VERIFY_DELAY: Final = timedelta(seconds=30)
#: Minimum tolerance of the verification in m³/h.
MIN_TOLERANCE: Final = 5.0
#: Number of own context ids remembered to recognise own state changes.
CONTEXT_HISTORY: Final = 64

_SERVICE_ERRORS: Final = (HomeAssistantError, TimeoutError, vol.Invalid, ValueError)


def _float_attribute(attributes: Mapping[str, Any], name: str) -> float | None:
    """Return a numeric attribute as a finite float, or None."""
    value = attributes.get(name)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if math.isfinite(value) else None


def actuator_snapshot(
    options: Options, state: State | None, *, foreign_change: bool = False
) -> ActuatorSnapshot:
    """Build the engine view of the actuator from its HA state."""
    if state is None:
        return ActuatorSnapshot(state=None)
    attributes = state.attributes
    if options.actuator_domain == ActuatorDomain.FAN:
        return ActuatorSnapshot(
            state=state.state,
            foreign_change=foreign_change,
            step=_float_attribute(attributes, "percentage_step"),
            percentage=_float_attribute(attributes, "percentage"),
        )
    if options.actuator_domain == ActuatorDomain.NUMBER:
        return ActuatorSnapshot(
            state=state.state,
            foreign_change=foreign_change,
            step=_float_attribute(attributes, "step"),
            min_value=_float_attribute(attributes, "min"),
            max_value=_float_attribute(attributes, "max"),
        )
    return ActuatorSnapshot(state=state.state, foreign_change=foreign_change)


def off_actuator_snapshot(
    state: State | None, *, foreign_change: bool = False
) -> OffActuatorSnapshot:
    """Build the engine view of the off actuator from its HA state."""
    return OffActuatorSnapshot(
        state=state.state if state is not None else None,
        foreign_change=foreign_change,
    )


def verify_tolerance(options: Options, actuator: ActuatorSnapshot) -> float:
    """Tolerance of the verification: one actuator step or 5 m³/h (larger wins)."""
    step = actuator.step if actuator.step is not None and actuator.step > 0 else 1.0
    if options.actuator_domain == ActuatorDomain.FAN or (
        options.actuator_domain == ActuatorDomain.NUMBER
        and options.actuator_unit == ACTUATOR_UNIT_PERCENT
    ):
        step = step / 100 * options.max_flow
    return max(step, MIN_TOLERANCE)


def _write_service(options: Options, native: Native) -> tuple[str, str, dict[str, Any]]:
    """Service call that writes a native value to the actuator."""
    entity_id = options.actuator
    if options.actuator_domain == ActuatorDomain.SELECT:
        return "select", "select_option", {"entity_id": entity_id, "option": native}
    if options.actuator_domain == ActuatorDomain.FAN:
        # `fan.set_percentage` takes an int; truncating keeps a percentage
        # such as 66.666667 (two of three speeds) on its speed.
        percentage = int(float(native) + 1e-6)
        return (
            "fan",
            "set_percentage",
            {"entity_id": entity_id, "percentage": percentage},
        )
    return "number", "set_value", {"entity_id": entity_id, "value": float(native)}


def _off_service(options: Options, entity_id: str, *, stop: bool) -> tuple[str, str]:
    """Service that stops (``stop``) or starts the unit with the off actuator."""
    domain = entity_domain(entity_id)
    if domain == "fan":
        return domain, "turn_off" if stop else "turn_on"
    stopped_is_on = options.off_state == OFF_STATE_ON
    turn_on = stopped_is_on if stop else not stopped_is_on
    return domain, "turn_on" if turn_on else "turn_off"


def _correction_service(effect: CorrectReadiness) -> tuple[str, str, dict[str, Any]]:
    """Service call that sets a readiness entity to its expected state."""
    entity_id = effect.entity_id
    domain = entity_domain(entity_id)
    if domain in ("select", "input_select"):
        return (
            domain,
            "select_option",
            {"entity_id": entity_id, "option": effect.to_state},
        )
    if domain == "number":
        return (
            domain,
            "set_value",
            {"entity_id": entity_id, "value": float(effect.to_state)},
        )
    service = "turn_on" if effect.to_state == "on" else "turn_off"
    return domain, service, {"entity_id": entity_id}


class ActuatorWriter:
    """Writes to the actuator, the off actuator and readiness entities.

    Keeps the ids of the contexts it created (to recognise own state changes),
    verifies writes and reports ``write_failed`` and ``write_mismatch``.
    ``lock`` is the evaluation lock of the controller: a verification retry is
    a write and must not overlap an evaluation. ``on_problem_change`` is called
    (outside the lock) when the verification changes the reported problems.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        options: Options,
        lock: asyncio.Lock,
        on_problem_change: Callable[[], None],
    ) -> None:
        """Initialize the writer."""
        self._hass = hass
        self._options = options
        self._lock = lock
        self._on_problem_change = on_problem_change
        self._contexts: deque[str] = deque(maxlen=CONTEXT_HISTORY)
        self._failed = False
        self._mismatch = False
        # Last write and its verification state.
        self._written: WriteFlow | None = None
        self._retried = False
        self._exhausted: Native | None = None
        self._verify_unsub: CALLBACK_TYPE | None = None
        self._verify_job = HassJob(
            self._async_verify,
            "better_home_ventilation verify",
            cancel_on_shutdown=True,
        )

    # --- Problems and contexts -------------------------------------------------

    @property
    def problems(self) -> frozenset[ProblemKey]:
        """Problems detected by the writer, passed to the engine."""
        problems: set[ProblemKey] = set()
        if self._failed:
            problems.add(ProblemKey.WRITE_FAILED)
        if self._mismatch:
            problems.add(ProblemKey.WRITE_MISMATCH)
        return frozenset(problems)

    def is_own(self, context: Context | None) -> bool:
        """Return True if a state change was caused by a call of this writer."""
        return context is not None and context.id in self._contexts

    @callback
    def observe(self, actuator: ActuatorSnapshot) -> None:
        """Clear ``write_mismatch`` once the actuator shows the written value."""
        if (
            self._mismatch
            and self._written is not None
            and self._matches(self._written, actuator)
        ):
            self._mismatch = False
            self._exhausted = None

    @callback
    def async_cancel(self) -> None:
        """Cancel the pending verification."""
        if self._verify_unsub is not None:
            self._verify_unsub()
            self._verify_unsub = None

    # --- Writes ------------------------------------------------------------------

    async def async_write(self, effect: WriteFlow) -> bool:
        """Write a flow to the actuator and schedule its verification."""
        self.async_cancel()
        if self._exhausted is not None and effect.native != self._exhausted:
            # The target changed: a new verification cycle with one retry.
            self._exhausted = None
            self._mismatch = False
        self._written = effect
        self._retried = self._exhausted is not None
        domain, service, data = _write_service(self._options, effect.native)
        ok = await self._async_call(domain, service, data)
        if ok:
            self._verify_unsub = async_call_later(
                self._hass, VERIFY_DELAY, self._verify_job
            )
        return ok

    async def async_turn_off(self, entity_id: str) -> bool:
        """Stop the unit with the off actuator."""
        domain, service = _off_service(self._options, entity_id, stop=True)
        return await self._async_call(domain, service, {"entity_id": entity_id})

    async def async_turn_on(self, entity_id: str) -> bool:
        """Start the unit with the off actuator."""
        domain, service = _off_service(self._options, entity_id, stop=False)
        return await self._async_call(domain, service, {"entity_id": entity_id})

    async def async_correct(self, effect: CorrectReadiness) -> bool:
        """Set a readiness entity to its expected state (BHV-04)."""
        try:
            domain, service, data = _correction_service(effect)
        except ValueError:
            _LOGGER.warning(
                "Cannot correct %s: expected state %s is not a number",
                effect.entity_id,
                effect.to_state,
            )
            return False
        return await self._async_call(domain, service, data)

    # --- Internals -----------------------------------------------------------------

    def _matches(self, written: WriteFlow, actuator: ActuatorSnapshot) -> bool:
        """Compare the actuator value with the last written value."""
        native = current_native(self._options, actuator)
        if native is None:
            return False
        if isinstance(written.native, str) or isinstance(native, str):
            return native == written.native
        actual = native_to_flow(self._options, native)
        expected = native_to_flow(self._options, written.native)
        if actual is None or expected is None:
            return False
        return abs(actual - expected) <= verify_tolerance(self._options, actuator)

    async def _async_verify(self, _now: datetime) -> None:
        """Verify the last write; retry once, then report ``write_mismatch``."""
        self._verify_unsub = None
        changed = False
        async with self._lock:
            written = self._written
            state = self._hass.states.get(self._options.actuator)
            if written is None or state is None or is_unavailable(state.state):
                return
            if self._matches(written, actuator_snapshot(self._options, state)):
                changed = self._mismatch
                self._mismatch = False
                self._exhausted = None
            elif not self._retried:
                _LOGGER.debug(
                    "%s shows %s instead of %s, writing again",
                    self._options.actuator,
                    state.state,
                    written.native,
                )
                self._retried = True
                domain, service, data = _write_service(self._options, written.native)
                if await self._async_call(domain, service, data):
                    self._verify_unsub = async_call_later(
                        self._hass, VERIFY_DELAY, self._verify_job
                    )
            else:
                if not self._mismatch:
                    _LOGGER.info(
                        "%s does not take the written value %s (shows %s)",
                        self._options.actuator,
                        written.native,
                        state.state,
                    )
                changed = not self._mismatch
                self._mismatch = True
                self._exhausted = written.native
        if changed:
            self._on_problem_change()

    async def _async_call(
        self, domain: str, service: str, data: dict[str, Any]
    ) -> bool:
        """Call a service with an own context; errors raise ``write_failed``."""
        context = Context()
        self._contexts.append(context.id)
        try:
            async with asyncio.timeout(SERVICE_TIMEOUT):
                await self._hass.services.async_call(
                    domain, service, data, blocking=True, context=context
                )
        except _SERVICE_ERRORS as err:
            level = logging.DEBUG if self._failed else logging.WARNING
            _LOGGER.log(
                level,
                "Service %s.%s for %s failed: %s",
                domain,
                service,
                data.get("entity_id"),
                err or type(err).__name__,
            )
            self._failed = True
            return False
        if self._failed:
            _LOGGER.info("Service calls to %s work again", data.get("entity_id"))
        self._failed = False
        return True
