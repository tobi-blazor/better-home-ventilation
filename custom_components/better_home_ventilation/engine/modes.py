"""Modes, targets per mode, commands, timers, safety and reminders.

docs/spec/03-modes.md. The mode is never stored: it is resolved in every
evaluation from the controller state. Commands are pure functions on the
controller state.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Final

from .demand import DemandResult
from .detector import active_kinds, end_auto_boosts
from .models import (
    AUTO_BOOST_MODES,
    OFF_MODES,
    ActuatorDomain,
    AutoBoostKind,
    Boost,
    BoostCommand,
    ClearNotification,
    Command,
    CommandRejected,
    ControllerState,
    ControlSwitchCommand,
    Effect,
    KeepOffCommand,
    Mode,
    NotificationTag,
    Notify,
    NotifyKind,
    Options,
    OverrideSafetyCommand,
    Pause,
    PauseCommand,
    ProfileCommand,
    Reason,
    RemoveOverrideCommand,
    RestartPending,
    ResumeCommand,
    SafetyState,
    Snapshot,
    Source,
    Trigger,
    WarningInfo,
)
from .safety import by_priority, collect_warnings

#: Code of :class:`CommandRejected` for boost/pause while control is disabled.
CONTROL_DISABLED = "control_disabled"
#: Code of :class:`CommandRejected` for a boost level outside min…max flow.
LEVEL_OUT_OF_RANGE = "level_out_of_range"
#: Code of :class:`CommandRejected` for an override without a warning.
NO_ACTIVE_WARNING = "no_active_warning"

#: Interval of the reminders after the first one (BHV-24).
REMINDER_INTERVAL: Final = timedelta(hours=24)


def safety_applies(state: ControllerState, options: Options) -> bool:
    """Return True if safety asks for ``safety_off`` and may act now.

    While control is disabled safety acts only with ``safety_when_disabled``.
    """
    return state.safety.shuts_down and (
        state.control_enabled or options.safety_when_disabled
    )


def auto_boost_mode(state: ControllerState, options: Options) -> Mode | None:
    """Mode of the active auto boosts, or None (03-modes.md#mode-resolution).

    With both kinds active the one with the higher level wins (tie:
    ``cooking``); the configured levels are compared, before the quiet cap.
    """
    kinds = active_kinds(state)
    if not kinds:
        return None
    if len(kinds) == 1:
        return AUTO_BOOST_MODES[next(iter(kinds))]
    if options.cooking_level >= options.shower_level:
        return Mode.COOKING
    return Mode.SHOWER


def resolve_mode(state: ControllerState, options: Options, *, fallback: bool) -> Mode:
    """Resolve the mode by priority (03-modes.md#mode-resolution).

    ``safety_off``, ``disabled``, ``off``, ``boost``, ``shower``/``cooking``,
    ``fallback``, ``automatic``.
    """
    if safety_applies(state, options):
        return Mode.SAFETY_OFF
    if not state.control_enabled:
        return Mode.DISABLED
    if isinstance(state.manual, Pause):
        return Mode.OFF
    if isinstance(state.manual, Boost):
        return Mode.BOOST
    if (auto := auto_boost_mode(state, options)) is not None:
        return auto
    if fallback:
        return Mode.FALLBACK
    return Mode.AUTOMATIC


def auto_boost_levels(
    options: Options, kinds: frozenset[AutoBoostKind], *, quiet: bool
) -> dict[AutoBoostKind, float]:
    """Level of each active auto boost kind, capped while the quiet limit applies.

    BHV-32: ``auto_boost_quiet_cap`` replaces ``quiet_max_flow`` for auto
    boosts when the profile applies the quiet limit (night window, all day in
    ``quiet``, never in ``air_quality``). The humidity override of the quiet
    limit acts through the demand.
    """
    levels = {
        AutoBoostKind.SHOWER: options.shower_level,
        AutoBoostKind.COOKING: options.cooking_level,
    }
    return {
        kind: min(levels[kind], options.auto_boost_quiet_cap) if quiet else levels[kind]
        for kind in sorted(kinds)
    }


_AUTO_REASONS: Final = {Mode.SHOWER: Reason.SHOWER, Mode.COOKING: Reason.COOKING}
_AUTO_KINDS: Final = {mode: kind for kind, mode in AUTO_BOOST_MODES.items()}


def mode_target(
    mode: Mode,
    demand: DemandResult,
    state: ControllerState,
    auto_levels: Mapping[AutoBoostKind, float] | None = None,
) -> tuple[float, Reason]:
    """Target flow (before shaping) and reason of a mode (03-modes.md#target-per-mode).

    ``boost`` uses ``max(level, demand_unquiet)``: a user boost is never capped
    by the quiet limit and never gets less than the air quality asks for.
    ``shower``/``cooking`` use ``max(min(level, cap_if_quiet), demand)``
    (``auto_levels`` holds the capped levels); the reason is the one of the
    demand if the demand is higher.
    """
    if mode is Mode.SAFETY_OFF:
        return 0.0, Reason.SAFETY_OFF
    if mode is Mode.OFF:
        return 0.0, Reason.OFF
    if mode is Mode.BOOST and isinstance(state.manual, Boost):
        level = state.manual.level
        if demand.demand_unquiet > level:
            return demand.demand_unquiet, demand.reason_unquiet
        return level, Reason.BOOST
    if mode in _AUTO_REASONS and auto_levels:
        auto = auto_levels.get(_AUTO_KINDS[mode])
        if auto is not None and auto >= demand.demand:
            return auto, _AUTO_REASONS[mode]
    return demand.demand, demand.reason


def expire_timers(state: ControllerState, now: datetime) -> ControllerState:
    """End an expired boost, timed pause or restart wait (trigger ``timeout``).

    The end of the restart wait (BHV-43) restarts the unit and asks the next
    evaluation for the notification "restarted".
    """
    manual = state.manual
    if manual is not None and manual.until is not None and now >= manual.until:
        state = replace(
            state, manual=None, pending_trigger=Trigger.TIMEOUT, pending_source=None
        )
    restart = state.safety.restart_pending
    if restart is not None and now >= restart.deadline:
        state = replace(
            state,
            safety=replace(state.safety, restart_pending=None),
            pending_trigger=Trigger.TIMEOUT,
            pending_source=None,
            restarted_pending=True,
        )
    return state


def _commanded(
    state: ControllerState, source: Source, trigger: Trigger = Trigger.COMMAND
) -> ControllerState:
    return replace(state, pending_trigger=trigger, pending_source=source)


def _override(state: ControllerState, source: Source) -> ControllerState:
    """Add the incident keys of all active warnings to ``overridden`` (BHV-42)."""
    safety = state.safety
    incidents = {warning.incident_key for warning in safety.active.values()}
    return _commanded(
        replace(
            state,
            safety=replace(
                safety,
                overridden=safety.overridden | incidents,
                restart_pending=None,
            ),
        ),
        source,
        Trigger.OVERRIDE,
    )


def apply_command(
    state: ControllerState, command: Command, options: Options, now: datetime
) -> ControllerState:
    """Apply a manual command to the controller state (03-modes.md#commands).

    Raises :class:`CommandRejected` (``control_disabled``) for boost and pause
    while control is disabled, (``level_out_of_range``) for a boost level
    outside ``min_flow``…``max_flow`` and (``no_active_warning``) for an
    override without an active warning or restart wait.
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
                new = end_auto_boosts(replace(new, manual=None), options, now)
            return _commanded(new, command.source, Trigger.CONTROL_SWITCH)
        case ResumeCommand():
            return _resume(state, command.source, options, now)
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
        case OverrideSafetyCommand():
            if not state.safety.active and state.safety.restart_pending is None:
                raise CommandRejected(NO_ACTIVE_WARNING)
            return _override(state, command.source)
        case RemoveOverrideCommand():
            if not state.safety.overridden:
                return state
            return _commanded(
                replace(state, safety=replace(state.safety, overridden=frozenset())),
                command.source,
            )
        case KeepOffCommand():
            return _keep_off(state, command.source, now)


def _resume(
    state: ControllerState, source: Source, options: Options, now: datetime
) -> ControllerState:
    """``resume()``: end boost or pause and the auto boosts, confirm a restart.

    The auto boosts end with the cooldown of their detectors. The restart
    confirmation also acts while control is disabled (the
    ``safety_when_disabled`` restart); it does not lift an active warning.
    """
    restart = state.safety.restart_pending
    ends_manual = state.control_enabled and state.manual is not None
    ends_auto = state.control_enabled and bool(state.auto_boosts)
    if restart is None and not ends_manual and not ends_auto:
        return state
    new = replace(state, safety=replace(state.safety, restart_pending=None))
    if ends_manual:
        new = replace(new, manual=None)
    if ends_auto:
        new = end_auto_boosts(new, options, now)
    return _commanded(new, source)


def _keep_off(state: ControllerState, source: Source, now: datetime) -> ControllerState:
    """``keep_off()``: only during the restart wait; the unit stays off.

    With control enabled this is an indefinite pause; while control is
    disabled it only ends the restart wait and the unit is not switched back
    on. Outside the restart wait (e.g. a late notification action) it does
    nothing.
    """
    if state.safety.restart_pending is None:
        return state
    new = replace(state, safety=replace(state.safety, restart_pending=None))
    if state.control_enabled:
        new = replace(new, manual=Pause(until=None, started=now, source=source))
    else:
        new = replace(new, safety_restore=None)
    return _commanded(new, source)


def _boost(
    state: ControllerState, command: BoostCommand, options: Options, now: datetime
) -> ControllerState:
    """Start, extend or change a boost (BHV-20).

    ``boost_max_duration`` also caps a new boost, not only an extension
    (03-modes.md, commands; the service accepts up to 720 min).
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
    """Turn an external stop/start of the unit into a command.

    docs/spec/02-control.md#writing: a change between the stopped and the
    running state that is not the integration's own (``foreign_change``) is a
    command with source ``external``: a stop is ``pause()`` without duration,
    a start is ``resume()``. Changes from or to ``unavailable``/``unknown`` and
    the first observation after startup are not commands; while control is
    disabled external changes are ignored.

    A start during ``safety_off`` is an override of the current warnings
    (owner decision on Q11, 2026-10-29), also while control is disabled
    (``override_safety`` always acts); it ends a pause as well, because the
    unit was started. Also clears ``stopped_by_integration`` once the unit is
    seen running.
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
    ):
        return new
    if running and state.last_mode is Mode.SAFETY_OFF:
        overridden = _override(new, Source.EXTERNAL)
        if state.control_enabled:
            overridden = replace(overridden, manual=None)
        return overridden
    if not state.control_enabled:
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


# --- Safety state per evaluation ---------------------------------------------


@dataclass(frozen=True, kw_only=True)
class SafetyUpdate:
    """Result of :func:`update_safety`.

    ``new_blocking``: warnings that became active in this evaluation and are
    not overridden (most severe and newest first). ``restart_started``: the
    restart wait began. ``ended_without_wait``: the last blocking warning
    ended and ``restart_confirm_timeout`` is 0, so the unit restarts at once.
    """

    state: ControllerState
    new_blocking: tuple[WarningInfo, ...] = ()
    restart_started: bool = False
    ended_without_wait: bool = False
    unavailable: tuple[str, ...] = ()
    details_failed: tuple[str, ...] = ()


def update_safety(
    state: ControllerState, snapshot: Snapshot, options: Options, *, in_grace: bool
) -> SafetyUpdate:
    """Rebuild ``safety.active`` from the snapshot (03-modes.md#flow).

    - New warning keys are added; a new key that is not overridden ends a
      restart wait (it shuts down again).
    - Ended keys are removed; an incident key leaves ``overridden`` when no
      active warning has it any more.
    - When the last blocking warning ends while the unit is in ``safety_off``,
      the restart wait starts (``restart_confirm_timeout``).
    - Warnings loaded from the store that no source reported again count
      until the startup grace ends (fail-safe, 03-modes.md#persistence).
    """
    previous = state.safety
    report = collect_warnings(options.warning_sources, snapshot, previous.active)
    active = dict(report.warnings)
    unconfirmed = state.safety_unconfirmed - active.keys()
    if in_grace:
        for key in unconfirmed:
            if key in previous.active:
                active[key] = previous.active[key]
    else:
        unconfirmed = frozenset()
    incidents = {warning.incident_key for warning in active.values()}
    safety = SafetyState(
        active=active,
        overridden=previous.overridden & incidents,
        restart_pending=previous.restart_pending,
    )
    new_blocking = by_priority(
        warning
        for key, warning in safety.blocking.items()
        if key not in previous.active
    )
    restart_started = ended_without_wait = False
    if new_blocking:
        safety = replace(safety, restart_pending=None)
    elif (
        previous.blocking and not safety.blocking and state.last_mode is Mode.SAFETY_OFF
    ):
        if options.restart_confirm_timeout > timedelta(0):
            deadline = snapshot.now + options.restart_confirm_timeout
            safety = replace(safety, restart_pending=RestartPending(deadline=deadline))
            restart_started = True
        else:
            ended_without_wait = True
    return SafetyUpdate(
        state=replace(state, safety=safety, safety_unconfirmed=unconfirmed),
        new_blocking=new_blocking,
        restart_started=restart_started,
        ended_without_wait=ended_without_wait,
        unavailable=report.unavailable,
        details_failed=report.details_failed,
    )


def safety_notifications(
    state: ControllerState,
    update: SafetyUpdate,
    *,
    mode: Mode,
    previous_mode: Mode | None,
) -> tuple[ControllerState, list[Effect]]:
    """Notifications of the safety shutdown (BHV-41, BHV-43; 04-interface.md).

    - ``safety`` when the unit enters ``safety_off`` because of a warning, or a
      new warning shuts down while it already is in ``safety_off``.
    - ``restart`` when the restart wait begins (replaces ``safety``).
    - ``restarted`` when the unit leaves ``safety_off`` by the end of the
      restart wait (or at once with ``restart_confirm_timeout`` 0).
    - otherwise leaving ``safety_off`` (override, resume, keep off, control
      switch) clears the safety notification.
    """
    effects: list[Effect] = []
    was_safety = previous_mode is Mode.SAFETY_OFF
    restarted = state.restarted_pending or update.ended_without_wait
    if mode is Mode.SAFETY_OFF:
        blocking = by_priority(state.safety.blocking.values())
        if blocking and (update.new_blocking or not was_safety):
            older = tuple(w for w in blocking if w not in update.new_blocking)
            effects.append(
                Notify(kind=NotifyKind.SAFETY, warnings=(*update.new_blocking, *older))
            )
        elif update.restart_started and state.safety.restart_pending is not None:
            effects.append(
                Notify(
                    kind=NotifyKind.RESTART,
                    restart_at=state.safety.restart_pending.deadline,
                )
            )
    elif was_safety:
        if restarted:
            effects.append(Notify(kind=NotifyKind.RESTARTED))
        else:
            effects.append(ClearNotification(tag=NotificationTag.SAFETY))
    return replace(state, restarted_pending=False), effects


# --- Reminders (BHV-24) ------------------------------------------------------


def _reminder_due(
    state: ControllerState, off_since: datetime, mode: Mode, options: Options
) -> datetime:
    """Time of the next reminder of the off state that began at ``off_since``."""
    last = state.last_reminder
    if last is not None and last >= off_since:
        return last + REMINDER_INTERVAL
    manual = state.manual
    indefinite = mode is Mode.OFF and isinstance(manual, Pause) and manual.until is None
    threshold = options.pause_reminder_after if indefinite else options.off_notice_after
    return off_since + threshold


def update_reminders(
    state: ControllerState,
    mode: Mode,
    options: Options,
    *,
    now: datetime,
    in_grace: bool,
) -> tuple[ControllerState, list[Effect], datetime | None]:
    """Track ``off_since`` and send reminders for long off states (BHV-24).

    First reminder after ``pause_reminder_after`` for an indefinite pause,
    otherwise after ``off_notice_after``; then every 24 h while the off state
    lasts. Nothing is sent during the startup grace. When the off state ends,
    a sent reminder is cleared. Returns the new state, the effects and the
    time of the next reminder (None outside off states).
    """
    if mode not in OFF_MODES:
        effects: list[Effect] = []
        if state.last_reminder is not None:
            effects.append(ClearNotification(tag=NotificationTag.REMINDER))
        return replace(state, off_since=None, last_reminder=None), effects, None
    off_since = state.off_since or now
    state = replace(state, off_since=off_since)
    due = _reminder_due(state, off_since, mode, options)
    if in_grace or now < due:
        return state, [], due
    state = replace(state, last_reminder=now)
    reminder = Notify(kind=NotifyKind.REMINDER, off_since=off_since)
    return state, [reminder], now + REMINDER_INTERVAL


def wake_times(state: ControllerState) -> Sequence[datetime]:
    """Timer ends of the controller state (boost, pause, auto boosts, restart wait)."""
    times: list[datetime] = [boost.deadline for boost in state.auto_boosts.values()]
    if state.manual is not None and state.manual.until is not None:
        times.append(state.manual.until)
    if state.safety.restart_pending is not None:
        times.append(state.safety.restart_pending.deadline)
    return times
