"""Pure control engine of Better Home Ventilation.

Nothing in this package imports ``homeassistant``, does I/O, reads the clock or
logs; ``now`` is always passed in. Only relative imports are used inside the
package, so it can be imported as ``engine`` (engine tests without Home
Assistant) and as ``custom_components.better_home_ventilation.engine``.
See docs/spec/05-architecture.md#engine-and-glue.

Public API: :func:`evaluate` and :func:`apply_command`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime

from .demand import compute_demand, update_dryness
from .mapping import (
    adjacent_stage,
    current_native,
    flow_range,
    flow_to_native,
    native_to_flow,
    off_native,
    same_native,
)
from .models import (
    EVENT_MODE_CHANGED,
    ActuatorDomain,
    Boost,
    CommandRejected,
    ControllerState,
    Decision,
    Effect,
    FireEvent,
    Mode,
    OffMethod,
    Options,
    Problem,
    ProblemId,
    ProblemKey,
    ScheduleAt,
    Snapshot,
    Trigger,
    TurnOff,
    TurnOn,
    WriteBlock,
    WriteFlow,
    is_unavailable,
)
from .modes import (
    apply_command,
    detect_external_command,
    expire_timers,
    mode_target,
    resolve_mode,
    unit_running,
)
from .readiness import ReadinessResult, check_readiness
from .shaping import clamp, quantise, shape
from .validity import read_inputs

__all__ = ["CommandRejected", "apply_command", "evaluate"]


def evaluate(
    snapshot: Snapshot, state: ControllerState, options: Options
) -> tuple[Decision, ControllerState, list[Effect]]:
    """Run one evaluation (docs/spec/02-control.md#evaluation-loop).

    Returns the decision, the new controller state and the effects, which the
    glue executes in order: readiness corrections, off/on, write, events,
    schedule.
    """
    now = snapshot.now
    grace_until = snapshot.started_at + options.startup_grace
    in_grace = now < grace_until
    first_evaluation = not state.started

    # 1. Timers and external stop/start change the controller state first.
    state = expire_timers(state, now)
    state = detect_external_command(state, snapshot, options)

    # 2. Inputs, dryness and the automatic demand.
    readings = read_inputs(snapshot.states, options, now)
    summary = readings.summary()
    dryness = update_dryness(
        active=state.dry_active,
        humidity_high_since=state.dry_humidity_high_since,
        h_mean=summary.h_mean,
        h_max=summary.h_max,
        options=options,
        now=now,
    )
    state = replace(
        state,
        dry_active=dryness.active,
        dry_humidity_high_since=dryness.humidity_high_since,
    )
    demand = compute_demand(readings, dryness, state.profile, options, now)

    # 3. Mode and target.
    mode = resolve_mode(state, fallback=demand.fallback)
    previous_mode = state.last_mode
    mode_changed = previous_mode is not None and mode != previous_mode
    target_raw, reason = mode_target(mode, demand, state)

    # 4. Readiness (in every evaluation), then the gated write.
    readiness = check_readiness(
        options.readiness_conditions,
        snapshot.states,
        state.readiness,
        now=now,
        may_correct=mode is not Mode.DISABLED and not in_grace,
    )
    state = replace(state, readiness=readiness.trackers)
    out = _write_stage(
        snapshot,
        state,
        options,
        mode=mode,
        target_raw=target_raw,
        bypass_reason=mode_changed or readiness.restored,
        ready=readiness.ready,
        in_grace=in_grace,
    )
    state = out.state
    effects: list[Effect] = [*readiness.corrections, *out.effects]

    # 5. Mode change event (BHV-52).
    manual = state.manual
    ends_at = manual.until if manual is not None else None
    if mode_changed:
        trigger = _trigger(
            state, mode, previous_mode, first_evaluation=first_evaluation
        )
        effects.append(
            FireEvent(
                event_type=EVENT_MODE_CHANGED,
                data={
                    "from_mode": str(previous_mode),
                    "to_mode": str(mode),
                    "reason": str(reason),
                    "setpoint": out.setpoint,
                    "trigger": str(trigger),
                    "source": (
                        str(state.pending_source)
                        if state.pending_source is not None
                        else None
                    ),
                    "warning": None,
                    "ends_at": ends_at.isoformat() if ends_at is not None else None,
                },
            )
        )

    # 6. Problems, each with the time it first appeared.
    problem_since = {
        pid: state.problem_since.get(pid, now)
        for pid in _problem_ids(
            snapshot,
            options,
            invalid=readings.invalid,
            readiness=readiness,
            fallback=demand.fallback,
            in_grace=in_grace,
        )
    }
    problems = tuple(
        Problem(key=key, entity_id=entity_id, since=since)
        for (key, entity_id), since in problem_since.items()
    )

    # 7. Next scheduled evaluation (timer end, readiness re-check, grace end).
    wake = [*readiness.wake_at]
    if ends_at is not None:
        wake.append(ends_at)
    if in_grace:
        wake.append(grace_until)
    future = [when for when in wake if when > now]
    if future:
        effects.append(ScheduleAt(when=min(future)))

    state = replace(
        state,
        last_mode=mode,
        started=True,
        pending_trigger=None,
        pending_source=None,
        problem_since=problem_since,
    )
    decision = Decision(
        time=now,
        mode=mode,
        profile=state.profile,
        reason=reason,
        setpoint=out.setpoint,
        target=out.target,
        written=out.written,
        contributions=replace(
            demand.contributions,
            boost=manual.level
            if mode is Mode.BOOST and isinstance(manual, Boost)
            else None,
        ),
        inputs=summary,
        limits=demand.limits,
        invalid_inputs=readings.invalid,
        problems=problems,
        problem=any(now - p.since >= options.problem_delay for p in problems),
        ends_at=ends_at,
        write_blocked_by=out.blocked_by,
        off_method=out.off_method,
        actuator_value=current_native(options, snapshot.actuator),
        last_write=state.last_write,
        last_written_flow=state.last_written_flow,
    )
    return decision, state, effects


@dataclass(frozen=True, kw_only=True)
class _Output:
    """Result of the write stage of one evaluation."""

    state: ControllerState
    setpoint: float
    target: float
    written: float | None = None
    blocked_by: WriteBlock | None = None
    off_method: OffMethod | None = None
    effects: tuple[Effect, ...] = ()


def _write_stage(
    snapshot: Snapshot,
    state: ControllerState,
    options: Options,
    *,
    mode: Mode,
    target_raw: float,
    bypass_reason: bool,
    ready: bool,
    in_grace: bool,
) -> _Output:
    """Shape the target, map it and decide off/on and writes behind the gate.

    Gate order (02-control.md#readiness-and-availability): control switch off
    -> startup grace -> availability -> readiness. A gate reason is reported
    in ``write_blocked_by`` even when no write is due.

    The setpoint is what the unit should run at: the written value, or
    ``current`` when nothing is written (shaping holds or a gate blocks the
    write). ``disabled`` shows the target (shadow run, BHV-03).
    """
    now = snapshot.now
    actuator = snapshot.actuator
    native_now = current_native(options, actuator)
    flow_now = native_to_flow(options, native_now)
    if actuator.awaiting_confirmation and state.last_written_flow is not None:
        # The unit is still taking the last written value (02-control.md,
        # output shaping): it counts as current until the actuator confirms it.
        flow_now = state.last_written_flow
        native_now = flow_to_native(flow_now, options, actuator)
    current = flow_now if flow_now is not None else state.last_written_flow
    running = unit_running(options, snapshot)
    low, high = flow_range(options, actuator)

    def gate(*, uses_off_actuator: bool) -> WriteBlock | None:
        if mode is Mode.DISABLED:
            return WriteBlock.DISABLED
        if in_grace:
            return WriteBlock.GRACE
        off = snapshot.off_actuator
        if is_unavailable(actuator.state) or (
            uses_off_actuator and (off is None or is_unavailable(off.state))
        ):
            return WriteBlock.UNAVAILABLE
        if not ready:
            return WriteBlock.NOT_READY
        return None

    # Control disabled: the demand is shown, nothing is written (BHV-03).
    if mode is Mode.DISABLED:
        target = clamp(quantise(target_raw, options.flow_quantum), low, high)
        return _Output(
            state=state, setpoint=target, target=target, blocked_by=WriteBlock.DISABLED
        )

    # Off: stop the unit with the off actuator or the lowest value; no shaping.
    if mode is Mode.OFF:
        if options.off_entity is not None:
            blocked = gate(uses_off_actuator=True)
            if running is False or blocked is not None:
                return _Output(
                    state=state,
                    setpoint=0.0,
                    target=0.0,
                    blocked_by=blocked,
                    off_method=OffMethod.OFF_ACTUATOR,
                )
            return _Output(
                state=replace(state, stopped_by_integration=True),
                setpoint=0.0,
                target=0.0,
                off_method=OffMethod.OFF_ACTUATOR,
                effects=(TurnOff(entity_id=options.off_entity),),
            )
        lowest = off_native(options, actuator)
        blocked = gate(uses_off_actuator=False)
        if blocked is not None or same_native(native_now, lowest):
            return _Output(
                state=state,
                setpoint=0.0,
                target=0.0,
                blocked_by=blocked,
                off_method=OffMethod.MINIMUM,
            )
        flow = native_to_flow(options, lowest) or 0.0
        return _Output(
            state=_written(state, now, flow),
            setpoint=0.0,
            target=0.0,
            written=flow,
            off_method=OffMethod.MINIMUM,
            effects=(WriteFlow(flow=flow, native=lowest, bypass=True),),
        )

    # Running modes: switch the unit on again if the integration stopped it.
    target = clamp(quantise(target_raw, options.flow_quantum), low, high)
    turn_on = (
        options.off_entity is not None
        and running is False
        and state.stopped_by_integration
    )
    bypass = (
        state.bypass_pending
        or bypass_reason
        or not state.written_since_start
        or current is None
        or turn_on
    )
    blocked = gate(uses_off_actuator=turn_on)
    shaped = shape(
        target,
        current,
        bypass=bypass,
        now=now,
        last_write=state.last_write,
        options=options,
    )
    value = shaped.value
    hold_reason = shaped.blocked_by
    # What the unit runs at when nothing is written; 0 while it waits to be
    # switched on again (stopped by the integration, a gate may keep it so).
    held = 0.0 if turn_on else (current if current is not None else target)
    native = None
    if value is not None:
        native = flow_to_native(value, options, actuator, flow_now)
        if options.actuator_domain == ActuatorDomain.SELECT:
            if not bypass and isinstance(native_now, str) and native == native_now:
                toward = flow_to_native(target, options, actuator, flow_now)
                native = adjacent_stage(options, native_now, str(toward))
            value = native_to_flow(options, native)
        if value is None or (not bypass and same_native(native, native_now)):
            value, native, hold_reason = None, None, WriteBlock.DEADBAND

    if value is None or native is None:
        return _Output(
            state=state,
            setpoint=held,
            target=target,
            blocked_by=blocked or hold_reason,
        )
    if blocked is not None:
        # Nothing reaches the unit: it keeps running at `current`.
        return _Output(
            state=replace(state, bypass_pending=state.bypass_pending or bypass_reason),
            setpoint=held,
            target=target,
            blocked_by=blocked,
        )
    effects: list[Effect] = []
    if turn_on and options.off_entity is not None:
        effects.append(TurnOn(entity_id=options.off_entity))
    effects.append(WriteFlow(flow=value, native=native, bypass=bypass))
    return _Output(
        state=replace(_written(state, now, value), bypass_pending=False),
        setpoint=value,
        target=target,
        written=value,
        effects=tuple(effects),
    )


def _written(state: ControllerState, now: datetime, flow: float) -> ControllerState:
    """Record a write in the controller state."""
    return replace(
        state,
        last_write=now,
        last_written_flow=flow,
        written_since_start=True,
    )


def _trigger(
    state: ControllerState,
    mode: Mode,
    previous: Mode | None,
    *,
    first_evaluation: bool,
) -> Trigger:
    """Trigger of a mode change: pending command/timer, startup, inputs."""
    if state.pending_trigger is not None:
        return state.pending_trigger
    if first_evaluation:
        return Trigger.STARTUP
    if mode is Mode.FALLBACK:
        return Trigger.INPUTS_LOST
    if previous is Mode.FALLBACK:
        return Trigger.INPUTS_RESTORED
    return Trigger.COMMAND


def _problem_ids(
    snapshot: Snapshot,
    options: Options,
    *,
    invalid: Sequence[str],
    readiness: ReadinessResult,
    fallback: bool,
    in_grace: bool,
) -> list[ProblemId]:
    """List the current problems; input problems are not raised during the grace."""
    ids: list[ProblemId] = []
    if is_unavailable(snapshot.actuator.state):
        ids.append((ProblemKey.ACTUATOR_UNAVAILABLE, options.actuator))
    if options.off_entity is not None and (
        snapshot.off_actuator is None or is_unavailable(snapshot.off_actuator.state)
    ):
        ids.append((ProblemKey.ACTUATOR_UNAVAILABLE, options.off_entity))
    ids.extend((ProblemKey.NOT_READY, entity_id) for entity_id in readiness.not_ready)
    if not in_grace:
        ids.extend((ProblemKey.INPUTS_INVALID, entity_id) for entity_id in invalid)
        if fallback:
            ids.append((ProblemKey.NO_VALID_CO2, None))
    ids.extend((key, None) for key in sorted(snapshot.reported_problems))
    return ids
