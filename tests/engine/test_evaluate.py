"""evaluate(): startup, write gate, off/on, readiness, problems, events.

docs/spec/02-control.md#startup, #readiness-and-availability, #off-and-on,
#writing; docs/spec/03-modes.md#mode-change-events.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from engine.models import (
    ControllerState,
    ControlSwitchCommand,
    CorrectReadiness,
    Effect,
    FireEvent,
    Mode,
    OffMethod,
    Pause,
    PauseCommand,
    ProblemKey,
    ReadinessCondition,
    ScheduleAt,
    Source,
    TurnOff,
    TurnOn,
    WriteBlock,
    WriteFlow,
)

from .helpers import (
    CO2_A,
    CO2_B,
    FAN,
    OFF_SWITCH,
    READY,
    T0,
    Sim,
    make_options,
    of_type,
    running_state,
    writes,
)


def _trigger(effects: list[Effect]) -> object:
    events = of_type(effects, FireEvent)
    assert len(events) == 1
    return events[0].data["trigger"]


# --- Startup -----------------------------------------------------------------


def test_e39_startup_grace_writes_nothing() -> None:
    """E39: no WriteFlow during the startup grace; first write after it is bypass."""
    sim = Sim(
        values={CO2_A: 1600, CO2_B: "unavailable"},
        state=ControllerState(),
        started_at=T0 - timedelta(seconds=30),
    )
    decision, effects = sim.run()
    assert not writes(effects)
    assert decision.write_blocked_by is WriteBlock.GRACE
    assert decision.setpoint == 400
    assert decision.problems == ()  # no input problems during the grace
    assert of_type(effects, ScheduleAt) == [ScheduleAt(when=T0 + timedelta(seconds=30))]

    sim.advance(seconds=30)
    decision, effects = sim.run()
    assert [(w.flow, w.bypass) for w in writes(effects)] == [(400, True)]
    assert [p.key for p in decision.problems] == [ProblemKey.INPUTS_INVALID]
    assert sim.state.written_since_start


def test_startup_mode_change_event() -> None:
    """A mode that differs from the stored last mode fires ``startup``."""
    sim = Sim(state=ControllerState(last_mode=Mode.OFF))
    decision, effects = sim.run()
    assert decision.mode is Mode.AUTOMATIC
    assert _trigger(effects) == "startup"
    fresh = Sim(state=ControllerState())
    _, effects = fresh.run()
    assert not of_type(effects, FireEvent)


def test_bhv19_fallback_events_inputs_lost_and_restored() -> None:
    """Automatic -> fallback = ``inputs_lost``, back = ``inputs_restored``."""
    sim = Sim(values={CO2_A: "unknown", CO2_B: "unknown"})
    decision, effects = sim.run()
    assert decision.mode is Mode.FALLBACK
    assert _trigger(effects) == "inputs_lost"
    sim.set(CO2_A, 700)
    decision, effects = sim.run()
    assert decision.mode is Mode.AUTOMATIC
    assert _trigger(effects) == "inputs_restored"


# --- External stop/start and off/on --------------------------------------------


def _with_off(**kwargs: Any) -> Sim:
    return Sim(make_options(off_entity=OFF_SWITCH, off_state="on"), off="off", **kwargs)


def test_e52_bhv21_external_stop_and_start_of_off_actuator() -> None:
    """E52: stopped externally = pause; started externally = resume; unavailable: none."""
    sim = _with_off()
    sim.run()
    sim.set_off("on", foreign=True)
    decision, effects = sim.run()
    assert decision.mode is Mode.OFF
    assert sim.state.manual == Pause(until=None, started=T0, source=Source.EXTERNAL)
    assert not of_type(effects, TurnOff)
    event = of_type(effects, FireEvent)[0].data
    assert (event["trigger"], event["source"]) == ("command", "external")

    sim.set_off("off", foreign=True)
    decision, _ = sim.run()
    assert decision.mode is Mode.AUTOMATIC

    sim.set_off("unavailable")
    decision, _ = sim.run()
    assert (ProblemKey.ACTUATOR_UNAVAILABLE, OFF_SWITCH) in {
        (p.key, p.entity_id) for p in decision.problems
    }
    sim.set_off("on", foreign=True)
    decision, effects = sim.run()
    assert decision.mode is Mode.AUTOMATIC
    assert not of_type(effects, TurnOn)


def test_external_change_ignored_while_disabled() -> None:
    """While control is disabled an external stop is not a command."""
    sim = _with_off(state=running_state(control_enabled=False, last_mode=Mode.DISABLED))
    sim.run()
    sim.set_off("on", foreign=True)
    decision, _ = sim.run()
    assert decision.mode is Mode.DISABLED
    assert sim.state.manual is None


def test_own_stop_is_not_a_command() -> None:
    """A change with the integration's own context is not a command."""
    sim = _with_off()
    sim.run()
    sim.set_off("on", foreign=False)
    decision, _ = sim.run()
    assert decision.mode is Mode.AUTOMATIC


def test_e55_switch_on_again_after_disabled() -> None:
    """E55: pause (stopped by us), control off, control on -> TurnOn, then bypass write."""
    sim = _with_off()
    sim.run()
    sim.command(PauseCommand(source=Source.BUTTON))
    decision, effects = sim.run()
    assert of_type(effects, TurnOff) == [TurnOff(entity_id=OFF_SWITCH)]
    assert decision.off_method is OffMethod.OFF_ACTUATOR
    assert sim.state.stopped_by_integration

    sim.command(ControlSwitchCommand(source=Source.BUTTON, enabled=False))
    decision, effects = sim.run()
    assert decision.mode is Mode.DISABLED
    assert not of_type(effects, TurnOn)
    assert not writes(effects)

    sim.command(ControlSwitchCommand(source=Source.BUTTON, enabled=True))
    decision, effects = sim.run()
    assert decision.mode is Mode.AUTOMATIC
    actions = [e for e in effects if isinstance(e, TurnOn | WriteFlow)]
    assert actions == [
        TurnOn(entity_id=OFF_SWITCH),
        WriteFlow(flow=80, native=80, bypass=True),
    ]
    # kept until the running state is observed (a failed TurnOn is retried)
    assert sim.state.stopped_by_integration
    _, effects = sim.run()
    assert not sim.state.stopped_by_integration
    assert not of_type(effects, TurnOn)


def test_e55_external_stop_is_not_switched_on() -> None:
    """E55 variant: an external stop is a pause and is not switched on again."""
    sim = _with_off()
    sim.run()
    sim.set_off("on", foreign=True)
    sim.run()
    sim.command(ControlSwitchCommand(source=Source.BUTTON, enabled=False))
    sim.run()
    sim.command(ControlSwitchCommand(source=Source.BUTTON, enabled=True))
    decision, effects = sim.run()
    assert decision.mode is Mode.AUTOMATIC
    assert not of_type(effects, TurnOn)
    assert [w.bypass for w in writes(effects)] == [True]


def test_off_actuator_unavailable_blocks_turn_off() -> None:
    """Off mode with an unavailable off actuator: no TurnOff, ``unavailable``."""
    sim = _with_off()
    sim.set_off("unavailable")
    sim.command(PauseCommand(source=Source.BUTTON))
    decision, effects = sim.run()
    assert not of_type(effects, TurnOff)
    assert decision.write_blocked_by is WriteBlock.UNAVAILABLE


def test_e57_fan_without_off_actuator_stopped_externally() -> None:
    """E57: fan turned off externally = pause; turned on externally = automatic."""
    sim = Sim(
        make_options(actuator=FAN),
        actuator="on",
        actuator_attrs={"percentage": 20.0, "step": 1.0},
    )
    decision, effects = sim.run()
    assert not writes(effects)
    sim.set_actuator("off", foreign=True)
    decision, effects = sim.run()
    assert decision.mode is Mode.OFF
    assert decision.off_method is OffMethod.MINIMUM
    assert sim.state.manual == Pause(until=None, started=T0, source=Source.EXTERNAL)
    assert not writes(effects)

    sim.set_actuator("on", foreign=True, percentage=20.0)
    decision, effects = sim.run()
    assert decision.mode is Mode.AUTOMATIC
    assert [(w.native, w.bypass) for w in writes(effects)] == [(20, True)]


# --- Availability and readiness ----------------------------------------------


def test_bhv05_actuator_unavailable_suspends_writes() -> None:
    """Unavailable actuator: no write, problem after ``problem_delay``."""
    sim = Sim(values={CO2_A: 1600, CO2_B: 1600}, actuator="unavailable")
    sim.state = running_state(last_written_flow=80)
    decision, effects = sim.run()
    assert not writes(effects)
    assert decision.write_blocked_by is WriteBlock.UNAVAILABLE
    assert decision.problems[0].key is ProblemKey.ACTUATOR_UNAVAILABLE
    assert not decision.problem
    sim.advance(minutes=10)
    decision, _ = sim.run()
    assert decision.problem
    assert decision.problems[0].since == T0

    sim.set_actuator("80")
    decision, effects = sim.run()
    assert writes(effects)
    assert decision.problems == ()


def _ready_sim(**kwargs: Any) -> Sim:
    condition = ReadinessCondition(
        entity_id=READY, expected_state="Remote", auto_correct=True
    )
    options = make_options(readiness_conditions=[condition])
    return Sim(options, values={READY: "Local"}, **kwargs)


def test_e56_bhv04_readiness_corrected_while_shaping_holds() -> None:
    """E56: holding + unmet readiness -> CorrectReadiness only; then bypass write."""
    sim = _ready_sim()
    decision, effects = sim.run()
    assert of_type(effects, CorrectReadiness) == [
        CorrectReadiness(entity_id=READY, to_state="Remote", from_state="Local")
    ]
    assert not writes(effects)
    assert decision.write_blocked_by is WriteBlock.NOT_READY
    assert of_type(effects, ScheduleAt) == [ScheduleAt(when=T0 + timedelta(seconds=5))]

    sim.advance(seconds=5)
    sim.set(READY, "Remote")
    decision, effects = sim.run()
    assert [(w.flow, w.bypass) for w in writes(effects)] == [(80, True)]
    assert decision.problems == ()


def test_e56_no_correction_while_disabled_or_in_grace() -> None:
    """E56: no CorrectReadiness while control is disabled or during the grace."""
    disabled = _ready_sim(
        state=running_state(control_enabled=False, last_mode=Mode.DISABLED)
    )
    decision, effects = disabled.run()
    assert not of_type(effects, CorrectReadiness)
    assert decision.write_blocked_by is WriteBlock.DISABLED
    assert [p.key for p in decision.problems] == [ProblemKey.NOT_READY]

    grace = _ready_sim(started_at=T0)
    _, effects = grace.run()
    assert not of_type(effects, CorrectReadiness)


def test_bhv04_three_attempts_then_not_ready_and_retries() -> None:
    """At most one correction per 60 s; after 3 attempts ``not_ready``; retry every 5 min."""
    sim = _ready_sim()
    attempts: list[float] = []
    for second in range(0, 600, 5):
        sim.now = T0 + timedelta(seconds=second)
        decision, effects = sim.run()
        if of_type(effects, CorrectReadiness):
            attempts.append(second)
        if second == 125:
            assert [p.key for p in decision.problems] == [ProblemKey.NOT_READY]
        if second == 115:
            assert decision.problems == ()
    assert attempts == [0, 60, 120, 420]


def test_bhv04_without_auto_correct_not_ready_at_once() -> None:
    """Without ``auto_correct`` the condition is a problem at once, no correction."""
    condition = ReadinessCondition(entity_id=READY, expected_state="Remote")
    sim = Sim(make_options(readiness_conditions=[condition]), values={READY: "Local"})
    decision, effects = sim.run()
    assert not of_type(effects, CorrectReadiness)
    assert [(p.key, p.entity_id) for p in decision.problems] == [
        (ProblemKey.NOT_READY, READY)
    ]


def test_bhv04_unavailable_readiness_entity_is_not_corrected() -> None:
    """An unavailable readiness entity is not met and not corrected."""
    sim = _ready_sim()
    sim.set(READY, "unavailable")
    decision, effects = sim.run()
    assert not of_type(effects, CorrectReadiness)
    assert decision.write_blocked_by is WriteBlock.NOT_READY


def test_bhv04_number_condition_with_tolerance() -> None:
    """``number`` conditions compare numerically with a tolerance of 0.5."""
    entity = "number.ventilation_control_mode"
    condition = ReadinessCondition(
        entity_id=entity, expected_state="2", auto_correct=True
    )
    sim = Sim(make_options(readiness_conditions=[condition]), values={entity: "2.4"})
    _, effects = sim.run()
    assert not of_type(effects, CorrectReadiness)
    sim.set(entity, "3")
    _, effects = sim.run()
    assert of_type(effects, CorrectReadiness)


def test_reported_problems_pass_through() -> None:
    """``write_failed``/``write_mismatch`` from the glue are listed as problems."""
    sim = Sim()
    sim.reported_problems = frozenset({ProblemKey.WRITE_FAILED})
    decision, _ = sim.run()
    assert [(p.key, p.entity_id) for p in decision.problems] == [
        (ProblemKey.WRITE_FAILED, None)
    ]


def test_no_valid_co2_problem() -> None:
    """Fallback raises ``no_valid_co2`` plus one ``inputs_invalid`` per sensor."""
    sim = Sim(values={CO2_A: "unknown", CO2_B: "unknown"})
    decision, _ = sim.run()
    keys = [(p.key, p.entity_id) for p in decision.problems]
    assert keys == [
        (ProblemKey.INPUTS_INVALID, CO2_A),
        (ProblemKey.INPUTS_INVALID, CO2_B),
        (ProblemKey.NO_VALID_CO2, None),
    ]
