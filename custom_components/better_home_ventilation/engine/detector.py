"""Automatic boosts: rise detectors and the cooking trigger entity.

docs/spec/03-modes.md#automatic-boosts (BHV-30, BHV-31). Shower and cooking
use the same rise detector, one instance per sensor; the cooking trigger
entity (hob power, hood) is an additional cooking detector. The auto boost of
a kind is active while at least one of its detectors is active.

Detector keys: ``shower:<entity>``, ``cooking:<entity>`` and
``cooking_trigger:<entity>``. Active auto boosts and cooldowns are part of the
persisted controller state; the samples are runtime only.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Final

from .models import (
    AutoBoost,
    AutoBoostKind,
    ControllerState,
    DetectorRuntime,
    InputValue,
    Options,
    Trigger,
    entity_domain,
    is_unavailable,
)
from .validity import parse_float

#: Domains whose trigger entity is active when its state is ``on``; any other
#: entity is active above ``cooking_trigger_threshold``.
ON_OFF_TRIGGER_DOMAINS: Final = frozenset(
    {"binary_sensor", "switch", "fan", "light", "input_boolean"}
)

#: Tolerance of the rise and end comparisons (float sums of sensor values).
_EPSILON: Final = 1e-9

TRIGGER_PREFIX: Final = "cooking_trigger"


@dataclass(frozen=True, kw_only=True)
class RiseDetector:
    """Parameters of the rise detector of one sensor."""

    key: str
    kind: AutoBoostKind
    entity_id: str
    rise: float
    window: timedelta
    end_offset: float
    max_duration: timedelta
    higher_is_worse: bool = True


def detector_key(kind: AutoBoostKind | str, entity_id: str) -> str:
    """Return the key of a detector (``<kind>:<entity>``)."""
    return f"{kind}:{entity_id}"


def rise_detectors(options: Options) -> tuple[RiseDetector, ...]:
    """Return the rise detectors of the configured bathroom and cooking sensors."""
    shower = tuple(
        RiseDetector(
            key=detector_key(AutoBoostKind.SHOWER, entity_id),
            kind=AutoBoostKind.SHOWER,
            entity_id=entity_id,
            rise=options.shower_rise,
            window=options.shower_window,
            end_offset=options.shower_end_offset,
            max_duration=options.shower_max_duration,
        )
        for entity_id in options.bathroom_humidity_sensors
    )
    cooking = tuple(
        RiseDetector(
            key=detector_key(AutoBoostKind.COOKING, entity_id),
            kind=AutoBoostKind.COOKING,
            entity_id=entity_id,
            rise=options.cooking_rise,
            window=options.cooking_window,
            end_offset=options.cooking_end_offset,
            max_duration=options.cooking_max_duration,
            higher_is_worse=options.cooking_higher_is_worse,
        )
        for entity_id in options.cooking_sensors
    )
    return (*shower, *cooking)


def trigger_active(entity_id: str, value: InputValue | None, threshold: float) -> bool:
    """Return True if the cooking trigger entity is active.

    ``on`` for ``binary_sensor``, ``switch``, ``fan``, ``light`` and
    ``input_boolean``; any other entity when its numeric state is above
    ``threshold``. Missing, ``unavailable`` and ``unknown`` are inactive; the
    state is taken as it is, without the stale check (an on/off entity does
    not report again while it does not change).
    """
    if value is None or is_unavailable(value.state):
        return False
    state = str(value.state).strip().lower()
    if entity_domain(entity_id) in ON_OFF_TRIGGER_DOMAINS:
        return state == "on"
    number = parse_float(state)
    return number is not None and number > threshold


@dataclass(frozen=True, kw_only=True)
class DetectorUpdate:
    """Result of :func:`update_detectors`.

    ``started``: kinds whose detector started an auto boost in this
    evaluation. ``ended``: ``(kind, trigger)`` of every auto boost that
    ended (``detector_end``, ``timeout`` or ``inputs_lost``).
    """

    state: ControllerState
    started: frozenset[AutoBoostKind] = frozenset()
    ended: tuple[tuple[AutoBoostKind, Trigger], ...] = ()


def _in_window(
    samples: tuple[tuple[datetime, float], ...], now: datetime, window: timedelta
) -> tuple[tuple[datetime, float], ...]:
    return tuple(sample for sample in samples if sample[0] >= now - window)


def update_detectors(
    state: ControllerState,
    values: Mapping[str, float],
    states: Mapping[str, InputValue],
    options: Options,
    now: datetime,
) -> DetectorUpdate:
    """Feed the detectors with the current values and start or end auto boosts.

    ``values`` are the valid values of the detector sensors (invalid ones are
    missing), ``states`` the raw states (for the trigger entity). Every
    evaluation adds the current valid value of each sensor as a sample;
    evaluations run on (debounced) state changes and periodically.

    Rise detector (higher = worse after negating when ``higher_is_worse`` is
    false): not active and not in cooldown, it starts when the value is at
    least ``rise`` above the minimum of the window (baseline = that minimum,
    deadline = now + maximum duration). Active, it ends at ``baseline +
    end_offset`` or below (``detector_end``), at the deadline (``timeout``)
    or after invalid input for longer than ``stale_timeout``
    (``inputs_lost``). Each end starts the cooldown and clears the samples.

    Trigger entity: starts when it is active (and was seen inactive since the
    last start), stays active until ``cooking_trigger_run_on`` after it
    turned inactive (``detector_end``), at most ``cooking_max_duration`` from
    the start (``timeout``).

    Auto boosts and cooldowns of detectors that are no longer configured are
    dropped.
    """
    boosts = dict(state.auto_boosts)
    cooldown = {
        key: until for key, until in state.detector_cooldown.items() if until > now
    }
    runtime: dict[str, DetectorRuntime] = {}
    started: set[AutoBoostKind] = set()
    ended: list[tuple[AutoBoostKind, Trigger]] = []

    def end(key: str, boost: AutoBoost, trigger: Trigger) -> None:
        del boosts[key]
        cooldown[key] = now + options.auto_boost_cooldown
        ended.append((boost.kind, trigger))

    for detector in rise_detectors(options):
        key = detector.key
        previous = state.detectors.get(key, DetectorRuntime())
        sign = 1.0 if detector.higher_is_worse else -1.0
        raw = values.get(detector.entity_id)
        samples = _in_window(previous.samples, now, detector.window)
        invalid_since = previous.invalid_since
        if raw is not None:
            samples = (*samples, (now, sign * raw))
            invalid_since = None
        elif invalid_since is None:
            invalid_since = now
        boost = boosts.get(key)
        if boost is not None:
            baseline = sign * (boost.baseline or 0.0)
            if (
                raw is not None
                and sign * raw <= baseline + detector.end_offset + _EPSILON
            ):
                end(key, boost, Trigger.DETECTOR_END)
            elif now >= boost.deadline:
                end(key, boost, Trigger.TIMEOUT)
            elif (
                invalid_since is not None
                and now - invalid_since > options.stale_timeout
            ):
                end(key, boost, Trigger.INPUTS_LOST)
            if key not in boosts:
                samples = ()
        elif key not in cooldown and raw is not None:
            lowest = min(value for _, value in samples)
            if sign * raw - lowest >= detector.rise - _EPSILON:
                boosts[key] = AutoBoost(
                    kind=detector.kind,
                    entity_id=detector.entity_id,
                    baseline=sign * lowest,
                    started=now,
                    deadline=now + detector.max_duration,
                )
                started.add(detector.kind)
        runtime[key] = DetectorRuntime(samples=samples, invalid_since=invalid_since)

    if (entity_id := options.cooking_trigger) is not None:
        key = detector_key(TRIGGER_PREFIX, entity_id)
        previous = state.detectors.get(key, DetectorRuntime())
        active = trigger_active(
            entity_id, states.get(entity_id), options.cooking_trigger_threshold
        )
        armed = previous.armed or not active
        boost = boosts.get(key)
        if boost is not None:
            cap = boost.started + options.cooking_max_duration
            deadline = (
                cap
                if active
                else min(boost.deadline, cap, now + options.cooking_trigger_run_on)
            )
            if now >= cap:
                end(key, boost, Trigger.TIMEOUT)
            elif now >= deadline:
                end(key, boost, Trigger.DETECTOR_END)
            else:
                boosts[key] = replace(boost, deadline=deadline)
                armed = armed and not active
        elif active and armed and key not in cooldown:
            boosts[key] = AutoBoost(
                kind=AutoBoostKind.COOKING,
                entity_id=entity_id,
                baseline=None,
                started=now,
                deadline=now + options.cooking_max_duration,
            )
            armed = False
            started.add(AutoBoostKind.COOKING)
        runtime[key] = DetectorRuntime(armed=armed)

    for key in [key for key in boosts if key not in runtime]:
        del boosts[key]
    cooldown = {key: until for key, until in cooldown.items() if key in runtime}
    return DetectorUpdate(
        state=replace(
            state,
            auto_boosts=boosts,
            detector_cooldown=cooldown,
            detectors=runtime,
        ),
        started=frozenset(started),
        ended=tuple(ended),
    )


def end_auto_boosts(
    state: ControllerState, options: Options, now: datetime
) -> ControllerState:
    """End every auto boost by a command (``resume()``, control switch off).

    The detectors enter their cooldown and their samples are cleared; a
    trigger entity has to turn inactive before it starts again.
    """
    if not state.auto_boosts:
        return state
    until = now + options.auto_boost_cooldown
    cooldown = dict(state.detector_cooldown)
    detectors = dict(state.detectors)
    for key in state.auto_boosts:
        cooldown[key] = until
        detectors[key] = DetectorRuntime(armed=False)
    return replace(
        state, auto_boosts={}, detector_cooldown=cooldown, detectors=detectors
    )


def active_kinds(state: ControllerState) -> frozenset[AutoBoostKind]:
    """Return the kinds with at least one active auto boost."""
    return frozenset(boost.kind for boost in state.auto_boosts.values())


def kind_deadline(state: ControllerState, kind: AutoBoostKind) -> datetime | None:
    """Return the latest deadline of the active auto boosts of a kind."""
    deadlines = [
        boost.deadline for boost in state.auto_boosts.values() if boost.kind is kind
    ]
    return max(deadlines) if deadlines else None
