"""Modes, targets per mode, commands and timers (docs/spec/03-modes.md, 0.1 part).

The mode is never stored: it is resolved in every evaluation from the
controller state. Commands are pure functions on the controller state.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from .demand import DemandResult
from .models import (
    ActuatorDomain,
    Boost,
    BoostCommand,
    Command,
    CommandRejected,
    ControllerState,
    ControlSwitchCommand,
    Mode,
    Options,
    Pause,
    PauseCommand,
    ProfileCommand,
    Reason,
    ResumeCommand,
    Snapshot,
    Source,
    Trigger,
)

#: Code of :class:`CommandRejected` for boost/pause while control is disabled.
CONTROL_DISABLED = "control_disabled"
#: Code of :class:`CommandRejected` for a boost level outside min…max flow.
LEVEL_OUT_OF_RANGE = "level_out_of_range"


def resolve_mode(state: ControllerState, *, fallback: bool) -> Mode:
    """Resolve the mode by priority: disabled, off, boost, fallback, automatic."""
    if not state.control_enabled:
        return Mode.DISABLED
    if isinstance(state.manual, Pause):
        return Mode.OFF
    if isinstance(state.manual, Boost):
        return Mode.BOOST
    if fallback:
        return Mode.FALLBACK
    return Mode.AUTOMATIC


def mode_target(
    mode: Mode, demand: DemandResult, state: ControllerState
) -> tuple[float, Reason]:
    """Target flow (before shaping) and reason of a mode (03-modes.md#target-per-mode).

    ``boost`` uses ``max(level, demand_unquiet)``: a user boost is never capped
    by the quiet limit and never gets less than the air quality asks for.
    """
    if mode is Mode.OFF:
        return 0.0, Reason.OFF
    if mode is Mode.BOOST and isinstance(state.manual, Boost):
        level = state.manual.level
        if demand.demand_unquiet > level:
            return demand.demand_unquiet, demand.reason_unquiet
        return level, Reason.BOOST
    return demand.demand, demand.reason


def expire_timers(state: ControllerState, now: datetime) -> ControllerState:
    """End an expired boost or timed pause (trigger ``timeout``)."""
    manual = state.manual
    if manual is not None and manual.until is not None and now >= manual.until:
        return replace(
            state, manual=None, pending_trigger=Trigger.TIMEOUT, pending_source=None
        )
    return state


def _commanded(
    state: ControllerState, source: Source, trigger: Trigger = Trigger.COMMAND
) -> ControllerState:
    return replace(state, pending_trigger=trigger, pending_source=source)


def apply_command(
    state: ControllerState, command: Command, options: Options, now: datetime
) -> ControllerState:
    """Apply a manual command to the controller state (03-modes.md#commands).

    Raises :class:`CommandRejected` (``control_disabled``) for boost and pause
    while control is disabled, and (``level_out_of_range``) for a boost level
    outside ``min_flow``…``max_flow``.
    """
    match command:
        case ProfileCommand(profile=profile):
            if profile is state.profile:
                return state
            return replace(state, profile=profile, bypass_pending=True)
        case ControlSwitchCommand(enabled=enabled):
            if enabled == state.control_enabled:
                return state
            new = replace(state, control_enabled=enabled)
            if not enabled:
                new = replace(new, manual=None)
            return _commanded(new, command.source, Trigger.CONTROL_SWITCH)
        case ResumeCommand():
            if not state.control_enabled or state.manual is None:
                return state
            return _commanded(replace(state, manual=None), command.source)
        case PauseCommand(duration=duration):
            if not state.control_enabled:
                raise CommandRejected(CONTROL_DISABLED)
            pause = Pause(
                until=now + duration if duration is not None else None,
                started=now,
                source=command.source,
            )
            return _commanded(replace(state, manual=pause), command.source)
        case BoostCommand():
            return _boost(state, command, options, now)


def _boost(
    state: ControllerState, command: BoostCommand, options: Options, now: datetime
) -> ControllerState:
    """Start, extend or change a boost (BHV-20).

    SPEC-GAP: ``boost_max_duration`` also caps a new boost, not only an
    extension (the service accepts up to 720 min).
    """
    if not state.control_enabled:
        raise CommandRejected(CONTROL_DISABLED)
    if command.level is not None and not (
        options.min_flow <= command.level <= options.max_flow
    ):
        raise CommandRejected(LEVEL_OUT_OF_RANGE)
    duration = command.duration or command.default_duration
    if duration is None:
        duration = options.boost_default_duration
    cap = now + options.boost_max_duration
    manual = state.manual
    if isinstance(manual, Boost):
        boost = replace(
            manual,
            until=min(max(manual.until, now) + duration, cap),
            level=command.level if command.level is not None else manual.level,
        )
    else:
        level = command.level
        if level is None:
            level = (
                command.default_level
                if command.default_level is not None
                else options.boost_default_level
            )
        boost = Boost(
            until=min(now + duration, cap),
            level=level,
            started=now,
            source=command.source,
        )
    return _commanded(replace(state, manual=boost, bypass_pending=True), command.source)


# --- External stop/start of the unit -----------------------------------------


def unit_running(options: Options, snapshot: Snapshot) -> bool | None:
    """Return whether the unit runs according to the off actuator (or a ``fan``).

    None if there is no off actuator and the actuator is not a ``fan``, or the
    entity is unavailable/unknown or in an unexpected state.
    """
    if options.off_entity is not None:
        if snapshot.off_actuator is None:
            return None
        state = snapshot.off_actuator.state
        stopped = "off" if options.off_domain == "fan" else options.off_state
    elif options.actuator_domain == ActuatorDomain.FAN:
        state = snapshot.actuator.state
        stopped = "off"
    else:
        return None
    if state not in ("on", "off"):
        return None
    return state != stopped


def _foreign_change(options: Options, snapshot: Snapshot) -> bool:
    if options.off_entity is not None:
        return (
            snapshot.off_actuator is not None and snapshot.off_actuator.foreign_change
        )
    return snapshot.actuator.foreign_change


def detect_external_command(
    state: ControllerState, snapshot: Snapshot, options: Options
) -> ControllerState:
    """Turn an external stop/start of the unit into ``pause()``/``resume()``.

    docs/spec/02-control.md#writing: a change between the stopped and the
    running state with a foreign context is a command with source
    ``external``. Changes from or to ``unavailable``/``unknown`` and the first
    observation after startup are not commands; while control is disabled
    external changes are ignored. Also clears ``stopped_by_integration`` once
    the unit is seen running.
    """
    running = unit_running(options, snapshot)
    previous = state.unit_running_seen
    new = replace(state, unit_running_seen=running)
    if running:
        new = replace(new, stopped_by_integration=False)
    if (
        running is None
        or previous is None
        or running == previous
        or not _foreign_change(options, snapshot)
        or not state.control_enabled
    ):
        return new
    if running:
        return replace(
            new,
            manual=None,
            pending_trigger=Trigger.COMMAND,
            pending_source=Source.EXTERNAL,
        )
    return replace(
        new,
        manual=Pause(until=None, started=snapshot.now, source=Source.EXTERNAL),
        pending_trigger=Trigger.COMMAND,
        pending_source=Source.EXTERNAL,
    )
