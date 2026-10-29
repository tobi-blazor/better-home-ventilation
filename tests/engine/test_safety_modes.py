"""Safety shutdown in the engine: modes, commands, notifications (BHV-40-44).

docs/spec/03-modes.md#safety-shutdown, scenarios E23-E27, E30b, E30c, E30h,
E31, E39a and the owner decision on Q11.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from typing import Any

import pytest

from engine import CommandRejected, options as opt
from engine.models import (
    ClearNotification,
    ControllerState,
    ControlSwitchCommand,
    CorrectReadiness,
    Effect,
    FireEvent,
    KeepOffCommand,
    Mode,
    NotificationTag,
    Notify,
    NotifyKind,
    OverrideSafetyCommand,
    Pause,
    PauseCommand,
    ProblemKey,
    ReadinessCondition,
    Reason,
    RemoveOverrideCommand,
    RestartPending,
    ResumeCommand,
    SafetyRestore,
    ScheduleAt,
    Severity,
    Source,
    TurnOff,
    TurnOn,
    WarningSource,
    WriteBlock,
)
from engine.safety import NINA_DE, make_details
from engine.serialize import state_from_dict, state_to_dict

from .helpers import OFF_SWITCH, READY, T0, Sim, make_options, of_type, writes

SMOKE = "binary_sensor.hallway_smoke"
NINA_1 = "binary_sensor.warning_region_1"
NINA_2 = "binary_sensor.warning_region_2"
LEVEL = "sensor.weather_warning_level"

SMOKE_SOURCE = WarningSource(
    subentry_id="smoke",
    name="Smoke detector",
    entities=(SMOKE,),
    details=opt.DETAILS_NONE,
)
NINA_SOURCE = WarningSource(
    subentry_id="nina",
    name="NINA region",
    entities=(NINA_1, NINA_2),
    headline_keywords=NINA_DE[opt.CONF_HEADLINE_KEYWORDS],
    text_keywords=NINA_DE[opt.CONF_TEXT_KEYWORDS],
    exclude_keywords=NINA_DE[opt.CONF_EXCLUDE_KEYWORDS],
)
LEVEL_SOURCE = WarningSource(
    subentry_id="level",
    name="Weather level",
    entities=(LEVEL,),
    trigger=opt.TRIGGER_NUMERIC,
    threshold=3,
)

FIRE = make_details(key="nina.1", headline="Großbrand - Musterstadt", severity="Severe")
FIRE_UPDATE = make_details(
    key="nina.2",
    headline="1. AKTUALISIERUNG: Großbrand - Musterstadt",
    severity="Severe",
)
GAS = make_details(key="nina.3", headline="Gasaustritt in Musterstadt")
ALL_CLEAR = make_details(
    key="nina.4",
    headline="Entwarnung: Feuerwehreinsatz - Musterstadt",
    actions="Schließen Sie vorsorglich Fenster und Türen.",
)


def _sim(*, off: str | None = "off", **overrides: Any) -> Sim:
    """Return a running unit with an off switch (stopped = on) and three sources."""
    values: dict[str, Any] = {
        "warning_sources": (SMOKE_SOURCE, NINA_SOURCE, LEVEL_SOURCE),
    }
    if off is not None:
        values["off_entity"] = OFF_SWITCH
    values.update(overrides)
    sim = Sim(
        make_options(**values),
        off=off,
        values={SMOKE: "off", NINA_1: "off", NINA_2: "off", LEVEL: "0"},
    )
    sim.run()
    return sim


def _notify(effects: list[Effect], kind: NotifyKind) -> Notify:
    found = [n for n in of_type(effects, Notify) if n.kind is kind]
    assert len(found) == 1, effects
    return found[0]


def _event(effects: list[Effect]) -> dict[str, object]:
    events = of_type(effects, FireEvent)
    assert len(events) == 1, effects
    return dict(events[0].data)


CLEAR_SAFETY = ClearNotification(tag=NotificationTag.SAFETY)


def test_e23_bhv40_bhv41_warning_on() -> None:
    """E23: warning on -> safety_off, effects TurnOff and Notify(safety)."""
    sim = _sim()
    sim.warn(SMOKE)
    decision, effects = sim.run()
    assert decision.mode is Mode.SAFETY_OFF
    assert decision.reason is Reason.SAFETY_OFF
    assert decision.setpoint == 0
    assert not decision.awaiting_restart
    assert decision.ends_at is None
    assert of_type(effects, TurnOff) == [TurnOff(entity_id=OFF_SWITCH)]
    notify = _notify(effects, NotifyKind.SAFETY)
    assert notify.tag is NotificationTag.SAFETY
    assert [w.source_name for w in notify.warnings] == ["Smoke detector"]
    assert not notify.warnings[0].details
    event = _event(effects)
    assert event["to_mode"] == "safety_off"
    assert event["trigger"] == "warning"
    assert event["source"] is None
    assert event["warning"] == "Smoke detector"
    assert [w.entity_id for w in decision.warnings] == [SMOKE]

    # the warning stays: no new notification, no new TurnOff (only the
    # reminder is scheduled)
    sim.advance(minutes=1)
    decision, effects = sim.run()
    assert decision.mode is Mode.SAFETY_OFF
    assert effects == [ScheduleAt(when=T0 + timedelta(hours=24))]


def test_e24_bhv42_override_survives_slot_change() -> None:
    """E24: override, then NINA moves the same id from slot 2 to slot 1."""
    sim = _sim()
    sim.warn(NINA_2, FIRE)
    decision, effects = sim.run()
    assert decision.mode is Mode.SAFETY_OFF
    assert _event(effects)["warning"] == "Großbrand - Musterstadt"
    assert _notify(effects, NotifyKind.SAFETY).warnings[0].severity is Severity.SEVERE

    sim.command(OverrideSafetyCommand(source=Source.NOTIFICATION))
    decision, effects = sim.run()
    assert decision.mode is Mode.AUTOMATIC
    assert CLEAR_SAFETY in effects
    assert of_type(effects, TurnOn) == [TurnOn(entity_id=OFF_SWITCH)]
    event = _event(effects)
    assert (event["trigger"], event["source"]) == ("override", "notification")
    assert decision.overridden_incidents == {"nina:großbrand - musterstadt"}

    sim.clear(NINA_2)
    sim.warn(NINA_1, FIRE)
    sim.advance(minutes=5)
    decision, effects = sim.run()
    assert decision.mode is Mode.AUTOMATIC
    assert not of_type(effects, Notify)
    assert list(sim.state.safety.active) == ["nina:nina.1"]
    assert decision.warnings[0].entity_id == NINA_1


def test_e25_bhv42_new_incident_shuts_down_again() -> None:
    """E25: override, then a warning with a different incident -> safety_off."""
    sim = _sim()
    sim.warn(NINA_1, FIRE)
    sim.run()
    sim.command(OverrideSafetyCommand(source=Source.BUTTON))
    assert sim.run()[0].mode is Mode.AUTOMATIC

    sim.warn(NINA_2, GAS)
    decision, effects = sim.run()
    assert decision.mode is Mode.SAFETY_OFF
    notify = _notify(effects, NotifyKind.SAFETY)
    assert [w.headline for w in notify.warnings] == ["Gasaustritt in Musterstadt"]
    assert _event(effects)["trigger"] == "warning"
    assert of_type(effects, TurnOff)
    # the overridden fire is still active and overridden
    assert {w.headline for w in decision.warnings} == {
        "Großbrand - Musterstadt",
        "Gasaustritt in Musterstadt",
    }


def test_e26_bhv43_warning_ends_restart_after_timeout() -> None:
    """E26: warning ends -> restart_pending, Notify(restart); after 30 min automatic."""
    sim = _sim()
    sim.warn(SMOKE)
    sim.run()
    sim.advance(minutes=10)
    sim.clear(SMOKE)
    decision, effects = sim.run()
    deadline = sim.now + timedelta(minutes=30)
    assert decision.mode is Mode.SAFETY_OFF
    assert decision.awaiting_restart
    assert decision.reason is Reason.SAFETY_OFF
    assert decision.ends_at == deadline
    assert sim.state.safety.restart_pending == RestartPending(deadline=deadline)
    notify = _notify(effects, NotifyKind.RESTART)
    assert notify.restart_at == deadline
    assert notify.tag is NotificationTag.SAFETY
    assert ScheduleAt(when=deadline) in effects
    assert not of_type(effects, FireEvent)  # still safety_off

    sim.advance(minutes=29)
    decision, effects = sim.run()
    assert decision.mode is Mode.SAFETY_OFF
    assert not of_type(effects, Notify)

    sim.advance(minutes=1)
    decision, effects = sim.run()
    assert decision.mode is Mode.AUTOMATIC
    assert _event(effects)["trigger"] == "timeout"
    assert _notify(effects, NotifyKind.RESTARTED).tag is NotificationTag.SAFETY
    assert CLEAR_SAFETY not in effects
    assert of_type(effects, TurnOn) == [TurnOn(entity_id=OFF_SWITCH)]
    assert [(w.flow, w.bypass) for w in writes(effects)] == [(80, True)]
    assert sim.state.safety.restart_pending is None


def test_e27_bhv43_keep_off() -> None:
    """E27: warning ends, keep_off -> mode off, indefinite pause."""
    sim = _sim()
    sim.warn(SMOKE)
    sim.run()
    sim.clear(SMOKE)
    sim.run()
    sim.command(KeepOffCommand(source=Source.NOTIFICATION))
    decision, effects = sim.run()
    assert decision.mode is Mode.OFF
    assert sim.state.manual == Pause(
        until=None, started=sim.now, source=Source.NOTIFICATION
    )
    assert sim.state.safety.restart_pending is None
    assert CLEAR_SAFETY in effects
    event = _event(effects)
    assert (event["to_mode"], event["trigger"], event["source"]) == (
        "off",
        "command",
        "notification",
    )
    assert not of_type(effects, TurnOn)


def test_keep_off_outside_restart_wait_does_nothing() -> None:
    """A late "Keep off" (after the restart) changes nothing."""
    sim = _sim()
    state = sim.state
    sim.command(KeepOffCommand(source=Source.NOTIFICATION))
    assert sim.state is state


def test_bhv43_resume_confirms_restart() -> None:
    """The answer "Restart now" (resume) ends the restart wait at once."""
    sim = _sim()
    sim.warn(SMOKE)
    sim.run()
    sim.clear(SMOKE)
    sim.run()
    sim.command(ResumeCommand(source=Source.NOTIFICATION))
    decision, effects = sim.run()
    assert decision.mode is Mode.AUTOMATIC
    assert CLEAR_SAFETY in effects
    assert of_type(effects, TurnOn)
    assert _event(effects)["trigger"] == "command"


def test_resume_does_not_lift_an_active_warning() -> None:
    """``resume`` is no override: the warning keeps the unit off."""
    sim = _sim()
    sim.warn(SMOKE)
    sim.run()
    state = sim.state
    sim.command(ResumeCommand(source=Source.BUTTON))
    assert sim.state is state
    assert sim.run()[0].mode is Mode.SAFETY_OFF


def test_new_warning_during_restart_wait_shuts_down_again() -> None:
    """A new warning during restart_pending clears it and shuts down again."""
    sim = _sim()
    sim.warn(SMOKE)
    sim.run()
    sim.clear(SMOKE)
    sim.run()
    assert sim.state.safety.restart_pending is not None
    sim.advance(minutes=5)
    sim.warn(NINA_1, GAS)
    decision, effects = sim.run()
    assert decision.mode is Mode.SAFETY_OFF
    assert not decision.awaiting_restart
    assert sim.state.safety.restart_pending is None
    assert _notify(effects, NotifyKind.SAFETY).warnings[0].headline == GAS.headline


def test_restart_without_wait() -> None:
    """``restart_confirm_timeout`` 0: restart at once, trigger warning_ended."""
    sim = _sim(restart_confirm_timeout=0)
    sim.warn(SMOKE)
    sim.run()
    sim.clear(SMOKE)
    decision, effects = sim.run()
    assert decision.mode is Mode.AUTOMATIC
    event = _event(effects)
    assert (event["trigger"], event["source"]) == ("warning_ended", None)
    assert _notify(effects, NotifyKind.RESTARTED)
    assert of_type(effects, TurnOn)


def test_warning_ending_while_overridden_needs_no_restart() -> None:
    """An overridden warning that ends does not start a restart wait."""
    sim = _sim()
    sim.warn(NINA_1, FIRE)
    sim.run()
    sim.command(OverrideSafetyCommand(source=Source.SERVICE))
    sim.run()
    sim.clear(NINA_1)
    decision, effects = sim.run()
    assert decision.mode is Mode.AUTOMATIC
    assert sim.state.safety.restart_pending is None
    assert sim.state.safety.overridden == frozenset()
    assert effects == []


def test_e30b_bhv42_update_of_overridden_incident_stays_overridden() -> None:
    """E30b: "1. AKTUALISIERUNG: Großbrand - X" with a new id stays overridden."""
    sim = _sim()
    sim.warn(NINA_1, FIRE)
    sim.run()
    sim.command(OverrideSafetyCommand(source=Source.NOTIFICATION))
    sim.run()
    sim.warn(NINA_1, FIRE_UPDATE)
    decision, effects = sim.run()
    assert decision.mode is Mode.AUTOMATIC
    assert not of_type(effects, Notify)
    assert list(sim.state.safety.active) == ["nina:nina.2"]
    assert sim.state.safety.overridden == {"nina:großbrand - musterstadt"}


def test_update_of_blocking_incident_notifies_the_new_text() -> None:
    """An update of a warning that is not overridden replaces the notification."""
    sim = _sim()
    sim.warn(NINA_1, FIRE)
    sim.run()
    sim.warn(NINA_1, FIRE_UPDATE)
    decision, effects = sim.run()
    assert decision.mode is Mode.SAFETY_OFF
    assert _notify(effects, NotifyKind.SAFETY).warnings[0].headline == (
        FIRE_UPDATE.headline
    )
    assert not of_type(effects, FireEvent)


def test_e30c_details_call_fails_counts_as_warning() -> None:
    """E30c: a failed details call: warning (fail-safe), problem raised."""
    sim = _sim()
    sim.warn(NINA_1, ALL_CLEAR)
    sim.details_failed.add(NINA_1)
    decision, effects = sim.run()
    assert decision.mode is Mode.SAFETY_OFF
    assert _notify(effects, NotifyKind.SAFETY).warnings[0].source_name == "NINA region"
    problems = {(p.key, p.entity_id) for p in decision.problems}
    assert (ProblemKey.WARNING_DETAILS_FAILED, NINA_1) in problems


def test_all_clear_with_details_is_no_warning() -> None:
    """With details the NINA all-clear is excluded (counterpart of E30c)."""
    sim = _sim()
    sim.warn(NINA_1, ALL_CLEAR)
    decision, effects = sim.run()
    assert decision.mode is Mode.AUTOMATIC
    assert effects == []
    assert decision.warnings == ()


def test_active_source_without_details_counts() -> None:
    """No or empty details for an active entity: one warning without details."""
    sim = _sim()
    sim.set(NINA_1, "on")
    sim.details[NINA_1] = ()
    decision, _ = sim.run()
    assert decision.mode is Mode.SAFETY_OFF
    assert decision.warnings[0].details is False


def test_e30h_filters_apply_per_source() -> None:
    """E30h: smoke detector (no details) and NINA with filters; only smoke on."""
    sim = _sim()
    # details for the smoke detector are ignored (provider `none`), so NINA's
    # exclude list cannot drop it either
    sim.warn(SMOKE, ALL_CLEAR)
    decision, effects = sim.run()
    assert decision.mode is Mode.SAFETY_OFF
    warning = _notify(effects, NotifyKind.SAFETY).warnings[0]
    assert (warning.source_id, warning.headline, warning.details) == (
        "smoke",
        None,
        False,
    )


def test_numeric_source_level_change_keeps_the_warning() -> None:
    """E30f in operation: level 3 -> 4 is the same warning (no new notification)."""
    sim = _sim()
    sim.set(LEVEL, "2")
    assert sim.run()[0].mode is Mode.AUTOMATIC
    sim.set(LEVEL, "3")
    decision, effects = sim.run()
    assert decision.mode is Mode.SAFETY_OFF
    key = next(iter(sim.state.safety.active))
    assert key == f"level:{LEVEL}:{sim.now.isoformat()}"
    sim.advance(minutes=5)
    sim.set(LEVEL, "4")
    decision, effects = sim.run()
    assert list(sim.state.safety.active) == [key]
    assert not of_type(effects, Notify)


def test_e31_bhv44_safety_when_control_disabled() -> None:
    """E31: control disabled, warning on, safety_when_disabled -> safety_off.

    Only off and, after the restart, on again; no readiness correction.
    """
    condition = ReadinessCondition(
        entity_id=READY, expected_state="Remote", auto_correct=True
    )
    sim = _sim(readiness_conditions=(condition,))
    sim.set(READY, "Remote")
    sim.command(ControlSwitchCommand(source=Source.BUTTON, enabled=False))
    assert sim.run()[0].mode is Mode.DISABLED

    sim.warn(SMOKE)
    decision, effects = sim.run()
    assert decision.mode is Mode.SAFETY_OFF
    assert of_type(effects, TurnOff) == [TurnOff(entity_id=OFF_SWITCH)]
    assert _notify(effects, NotifyKind.SAFETY)
    assert sim.state.safety_restore is not None

    # a readiness condition that becomes unmet is not corrected while disabled
    sim.set(READY, "Local")
    decision, effects = sim.run()
    assert not of_type(effects, CorrectReadiness)

    sim.set(READY, "Remote")
    sim.clear(SMOKE)
    decision, effects = sim.run()
    assert decision.awaiting_restart
    sim.command(ResumeCommand(source=Source.NOTIFICATION))
    decision, effects = sim.run()
    assert decision.mode is Mode.DISABLED
    assert of_type(effects, TurnOn) == [TurnOn(entity_id=OFF_SWITCH)]
    assert not writes(effects)
    assert sim.state.safety_restore is None
    # nothing else is written while disabled
    sim.advance(minutes=10)
    assert sim.run()[1] == []


def test_safety_when_disabled_off() -> None:
    """Without ``safety_when_disabled`` a disabled control stays hands-off."""
    sim = _sim(safety_when_disabled=False)
    sim.command(ControlSwitchCommand(source=Source.BUTTON, enabled=False))
    sim.run()
    sim.warn(SMOKE)
    decision, effects = sim.run()
    assert decision.mode is Mode.DISABLED
    assert effects == []
    assert len(decision.warnings) == 1
    sim.clear(SMOKE)
    sim.run()
    assert sim.state.safety.restart_pending is None


def test_control_switched_off_during_safety_off_clears_notification() -> None:
    """Leaving safety_off by the control switch clears the safety notification."""
    sim = _sim(safety_when_disabled=False)
    sim.warn(SMOKE)
    sim.run()
    sim.command(ControlSwitchCommand(source=Source.BUTTON, enabled=False))
    decision, effects = sim.run()
    assert decision.mode is Mode.DISABLED
    assert effects == [
        CLEAR_SAFETY,
        *of_type(effects, FireEvent),
    ]
    # switched on again during the warning: safety_off and a notification
    sim.command(ControlSwitchCommand(source=Source.BUTTON, enabled=True))
    decision, effects = sim.run()
    assert decision.mode is Mode.SAFETY_OFF
    assert _notify(effects, NotifyKind.SAFETY)
    assert _event(effects)["trigger"] == "control_switch"


def test_keep_off_while_disabled_does_not_restore() -> None:
    """While control is disabled, keep_off only ends the restart wait."""
    sim = _sim()
    sim.command(ControlSwitchCommand(source=Source.BUTTON, enabled=False))
    sim.run()
    sim.warn(SMOKE)
    sim.run()
    sim.clear(SMOKE)
    sim.run()
    sim.command(KeepOffCommand(source=Source.NOTIFICATION))
    assert sim.state.manual is None
    decision, effects = sim.run()
    assert decision.mode is Mode.DISABLED
    assert not of_type(effects, TurnOn)


def test_safety_when_disabled_unit_already_stopped_stays_stopped() -> None:
    """A unit that was already stopped is not switched on after the restart."""
    sim = _sim()
    sim.command(PauseCommand(source=Source.BUTTON))
    sim.run()
    sim.command(ControlSwitchCommand(source=Source.BUTTON, enabled=False))
    sim.run()  # pause ended by the switch, unit stays stopped (hands-off)
    sim.warn(SMOKE)
    _, effects = sim.run()
    assert not of_type(effects, TurnOff)
    assert sim.state.safety_restore is None
    sim.clear(SMOKE)
    sim.run()
    sim.command(ResumeCommand(source=Source.NOTIFICATION))
    _, effects = sim.run()
    assert not of_type(effects, TurnOn)


def test_safety_when_disabled_with_lowest_value() -> None:
    """Without off actuator: lowest value, then the previous value again."""
    sim = _sim(off=None, actuator_min=0)
    sim.set_actuator("230")
    sim.command(ControlSwitchCommand(source=Source.BUTTON, enabled=False))
    sim.run()
    sim.warn(SMOKE)
    decision, effects = sim.run()
    assert decision.mode is Mode.SAFETY_OFF
    assert [(w.flow, w.native) for w in writes(effects)] == [(0, 0)]
    assert sim.state.safety_restore is not None
    assert sim.state.safety_restore.flow == 230

    sim.clear(SMOKE)
    sim.run()
    sim.command(ResumeCommand(source=Source.NOTIFICATION))
    decision, effects = sim.run()
    assert decision.mode is Mode.DISABLED
    assert [(w.flow, w.bypass) for w in writes(effects)] == [(230, True)]


def test_restore_waits_for_availability() -> None:
    """The restore after a disabled safety off waits until the unit is available."""
    sim = _sim()
    sim.command(ControlSwitchCommand(source=Source.BUTTON, enabled=False))
    sim.run()
    sim.warn(SMOKE)
    sim.run()
    sim.clear(SMOKE)
    sim.run()
    sim.set_off("unavailable")
    sim.command(ResumeCommand(source=Source.NOTIFICATION))
    decision, effects = sim.run()
    assert decision.write_blocked_by is WriteBlock.UNAVAILABLE
    assert effects[:1] == [CLEAR_SAFETY]
    assert sim.state.safety_restore is not None
    sim.set_off("on")
    _, effects = sim.run()
    assert of_type(effects, TurnOn)
    assert sim.state.safety_restore is None


def test_restore_dropped_when_unit_runs_or_control_enabled() -> None:
    """No restore if the unit already runs, or once control is enabled again."""
    sim = _sim()
    sim.command(ControlSwitchCommand(source=Source.BUTTON, enabled=False))
    sim.run()
    sim.warn(SMOKE)
    sim.run()
    sim.clear(SMOKE)
    sim.run()
    sim.set_off("off")  # started by someone; not foreign in this test
    sim.command(ResumeCommand(source=Source.NOTIFICATION))
    _, effects = sim.run()
    assert not of_type(effects, TurnOn)
    assert sim.state.safety_restore is None

    other = _sim(off=None)
    other.command(ControlSwitchCommand(source=Source.BUTTON, enabled=False))
    other.run()
    other.warn(SMOKE)
    other.run()
    assert other.state.safety_restore is not None
    other.command(ControlSwitchCommand(source=Source.BUTTON, enabled=True))
    other.run()
    assert other.state.safety_restore is None


def test_restore_without_previous_flow_or_with_same_value() -> None:
    """Lowest-value restore: nothing to write without a previous flow or when equal."""
    sim = _sim(off=None, actuator_min=0)
    sim.command(ControlSwitchCommand(source=Source.BUTTON, enabled=False))
    sim.run()
    sim.state = replace(sim.state, safety_restore=SafetyRestore(flow=None))
    _, effects = sim.run()
    assert not writes(effects)
    assert sim.state.safety_restore is None
    sim.set_actuator("230")
    sim.state = replace(sim.state, safety_restore=SafetyRestore(flow=230))
    _, effects = sim.run()
    assert not writes(effects)
    assert sim.state.safety_restore is None
    sim.set_actuator("unavailable")
    sim.state = replace(sim.state, safety_restore=SafetyRestore(flow=230))
    decision, effects = sim.run()
    assert decision.write_blocked_by is WriteBlock.UNAVAILABLE
    assert sim.state.safety_restore is not None
    off = _sim()
    off.command(ControlSwitchCommand(source=Source.BUTTON, enabled=False))
    off.run()
    off.state = replace(off.state, safety_restore=SafetyRestore(turn_on=False))
    _, effects = off.run()
    assert effects == []
    assert off.state.safety_restore is None


def test_e39a_safety_turn_off_during_startup_grace() -> None:
    """E39a: startup grace with an active warning: TurnOff is still issued."""
    options = make_options(off_entity=OFF_SWITCH, warning_sources=(SMOKE_SOURCE,))
    sim = Sim(
        options,
        off="off",
        values={SMOKE: "on"},
        state=ControllerState(),
        started_at=T0,
    )
    decision, effects = sim.run()
    assert decision.mode is Mode.SAFETY_OFF
    assert decision.setpoint == 0
    assert of_type(effects, TurnOff) == [TurnOff(entity_id=OFF_SWITCH)]
    assert _notify(effects, NotifyKind.SAFETY)
    assert not writes(effects)


def test_e39a_lowest_value_during_startup_grace() -> None:
    """E39a without off actuator: the lowest value is written during the grace."""
    options = make_options(actuator_min=0, warning_sources=(SMOKE_SOURCE,))
    sim = Sim(options, values={SMOKE: "on"}, state=ControllerState(), started_at=T0)
    decision, effects = sim.run()
    assert decision.mode is Mode.SAFETY_OFF
    assert [w.flow for w in writes(effects)] == [0]
    assert decision.setpoint == 0


def test_e39a_override_during_startup_grace_holds_the_stopped_unit() -> None:
    """E39a: an override in the grace shows 0 until the unit is switched on.

    The safety off passed the grace and stopped the unit; switching it on again
    is an ordinary write behind the grace gate, so the setpoint stays at what
    the unit runs at (0, stopped by the integration, 02-control.md#output-shaping).
    """
    options = make_options(off_entity=OFF_SWITCH, warning_sources=(SMOKE_SOURCE,))
    sim = Sim(
        options,
        off="off",
        values={SMOKE: "on"},
        state=ControllerState(),
        started_at=T0,
    )
    sim.run()
    sim.advance(seconds=10)
    sim.command(OverrideSafetyCommand(source=Source.SERVICE))
    decision, effects = sim.run()
    assert decision.mode is Mode.AUTOMATIC
    assert decision.write_blocked_by is WriteBlock.GRACE
    assert decision.setpoint == 0
    assert not of_type(effects, TurnOn)
    assert not writes(effects)
    sim.advance(seconds=60)
    decision, effects = sim.run()
    assert of_type(effects, TurnOn) == [TurnOn(entity_id=OFF_SWITCH)]
    assert [w.flow for w in writes(effects)] == [decision.setpoint]


def test_q11_external_start_during_safety_off_is_an_override() -> None:
    """Q11: an external start during safety_off overrides; a new incident shuts down."""
    sim = _sim()
    sim.warn(NINA_1, FIRE)
    sim.run()
    assert sim.off is not None
    assert sim.off.state == "on"  # stopped
    sim.run()  # the glue evaluates again on the own state change of the switch
    sim.advance(minutes=2)
    sim.set_off("off", foreign=True)
    decision, effects = sim.run()
    assert decision.mode is Mode.AUTOMATIC
    event = _event(effects)
    assert (event["trigger"], event["source"]) == ("override", "external")
    assert CLEAR_SAFETY in effects
    assert not of_type(effects, TurnOff)
    assert sim.state.safety.overridden == {"nina:großbrand - musterstadt"}

    sim.warn(NINA_2, GAS)
    decision, effects = sim.run()
    assert decision.mode is Mode.SAFETY_OFF
    assert of_type(effects, TurnOff)


def test_q11_external_start_ends_a_pause_and_a_restart_wait() -> None:
    """An external start during safety_off also ends a pause and the restart wait."""
    sim = _sim()
    sim.command(PauseCommand(source=Source.BUTTON))
    sim.run()
    sim.warn(SMOKE)
    sim.run()
    sim.clear(SMOKE)
    sim.run()
    assert sim.state.safety.restart_pending is not None
    sim.set_off("off", foreign=True)
    decision, effects = sim.run()
    assert decision.mode is Mode.AUTOMATIC
    assert sim.state.manual is None
    assert sim.state.safety.restart_pending is None
    assert _event(effects)["trigger"] == "override"


def test_q11_external_start_while_disabled_is_an_override() -> None:
    """While control is disabled, an external start during safety_off overrides too."""
    sim = _sim()
    sim.command(ControlSwitchCommand(source=Source.BUTTON, enabled=False))
    sim.run()
    sim.warn(SMOKE)
    sim.run()
    sim.run()  # own state change of the switch
    sim.set_off("off", foreign=True)
    decision, effects = sim.run()
    assert decision.mode is Mode.DISABLED
    assert _event(effects)["trigger"] == "override"
    assert not of_type(effects, TurnOff)
    assert sim.state.safety_restore is None


def test_external_stop_during_safety_off_is_a_pause() -> None:
    """An external stop during safety_off (unit not yet off) is a pause."""
    sim = _sim()
    sim.warn(SMOKE)
    sim.set_off("unavailable")
    decision, _ = sim.run()
    assert decision.write_blocked_by is WriteBlock.UNAVAILABLE
    sim.set_off("off")
    sim.run()
    sim.set_off("on", foreign=True)
    sim.run()
    assert isinstance(sim.state.manual, Pause)
    assert sim.state.manual.source is Source.EXTERNAL
    sim.clear(SMOKE)
    sim.run()
    sim.command(KeepOffCommand(source=Source.NOTIFICATION))
    assert sim.run()[0].mode is Mode.OFF


def test_override_without_warning_is_rejected() -> None:
    """``override_safety`` without an active warning: ``no_active_warning``."""
    sim = _sim()
    with pytest.raises(CommandRejected) as err:
        sim.command(OverrideSafetyCommand(source=Source.SERVICE))
    assert err.value.code == "no_active_warning"


def test_override_during_restart_wait_restarts() -> None:
    """``override_safety`` during the restart wait ends it."""
    sim = _sim()
    sim.warn(SMOKE)
    sim.run()
    sim.clear(SMOKE)
    sim.run()
    sim.command(OverrideSafetyCommand(source=Source.SERVICE))
    decision, effects = sim.run()
    assert decision.mode is Mode.AUTOMATIC
    assert CLEAR_SAFETY in effects


def test_override_always_acts_while_disabled() -> None:
    """``override_safety`` acts while control is disabled."""
    sim = _sim()
    sim.command(ControlSwitchCommand(source=Source.BUTTON, enabled=False))
    sim.run()
    sim.warn(SMOKE)
    assert sim.run()[0].mode is Mode.SAFETY_OFF
    sim.command(OverrideSafetyCommand(source=Source.VOICE))
    decision, effects = sim.run()
    assert decision.mode is Mode.DISABLED
    assert CLEAR_SAFETY in effects
    assert of_type(effects, TurnOn)  # restored to the state before


def test_remove_override_shuts_down_again() -> None:
    """``safety_override`` switch off removes the overrides."""
    sim = _sim()
    sim.warn(SMOKE)
    sim.run()
    sim.command(OverrideSafetyCommand(source=Source.BUTTON))
    sim.run()
    sim.command(RemoveOverrideCommand(source=Source.BUTTON))
    decision, effects = sim.run()
    assert decision.mode is Mode.SAFETY_OFF
    assert _notify(effects, NotifyKind.SAFETY)
    assert _event(effects)["trigger"] == "command"
    state = sim.state
    sim.command(RemoveOverrideCommand(source=Source.BUTTON))
    assert sim.state is state


def test_bhv40_pause_and_boost_during_safety_off() -> None:
    """Commands are accepted during safety_off but safety has priority."""
    sim = _sim()
    sim.warn(SMOKE)
    sim.run()
    sim.command(PauseCommand(source=Source.BUTTON, duration=timedelta(hours=1)))
    decision, _ = sim.run()
    assert decision.mode is Mode.SAFETY_OFF
    assert decision.ends_at is None
    assert ScheduleAt(when=T0 + timedelta(hours=1)) in sim.run()[1]
    sim.clear(SMOKE)
    sim.run()
    sim.command(ResumeCommand(source=Source.BUTTON))
    assert sim.run()[0].mode is Mode.AUTOMATIC


def test_source_unavailable_problem_after_grace() -> None:
    """``warning_source_unavailable``: one per source, not during the grace."""
    sim = _sim()
    sim.set(NINA_1, "unavailable")
    sim.set(NINA_2, "unknown")
    decision, _ = sim.run()
    assert decision.mode is Mode.AUTOMATIC
    problems = [(p.key, p.entity_id) for p in decision.problems]
    assert problems.count((ProblemKey.WARNING_SOURCE_UNAVAILABLE, NINA_1)) == 1
    assert (ProblemKey.WARNING_SOURCE_UNAVAILABLE, NINA_2) not in problems

    options = make_options(warning_sources=(NINA_SOURCE,))
    early = Sim(options, values={NINA_1: "unavailable"}, started_at=T0)
    decision, _ = early.run()
    assert not [p for p in decision.problems if p.key.startswith("warning")]


# --- Persistence and load rules ------------------------------------------------


def _reload(sim: Sim, *, minutes: float = 5, grace: bool = True) -> Sim:
    """Persist, advance and load the state as after a restart of Home Assistant."""
    data = state_to_dict(sim.state)
    sim.advance(minutes=minutes)
    loaded = state_from_dict(data, sim.now)
    restarted = Sim(
        sim.options,
        now=sim.now,
        off=sim.off.state if sim.off is not None else None,
        values=dict(sim.values),
        state=loaded.state,
        started_at=sim.now if grace else sim.now - timedelta(hours=1),
    )
    restarted.details = dict(sim.details)
    return restarted


def test_load_rule_active_is_rebuilt_from_the_sources() -> None:
    """``active`` is not trusted: rebuilt; until the grace ends it keeps the unit off."""
    sim = _sim()
    sim.warn(SMOKE)
    sim.run()
    sim.clear(SMOKE)
    restarted = _reload(sim)
    assert restarted.state.safety_unconfirmed == frozenset(sim.state.safety.active)
    decision, effects = restarted.run()
    assert decision.mode is Mode.SAFETY_OFF  # fail-safe during the grace
    assert not of_type(effects, Notify)
    restarted.advance(minutes=1)
    decision, effects = restarted.run()
    assert decision.mode is Mode.SAFETY_OFF
    assert decision.awaiting_restart
    assert _notify(effects, NotifyKind.RESTART)
    assert restarted.state.safety_unconfirmed == frozenset()


def test_load_rule_confirmed_warning_does_not_notify_again() -> None:
    """A loaded warning that the source reports again: no new notification."""
    sim = _sim()
    sim.warn(SMOKE)
    sim.run()
    restarted = _reload(sim)
    decision, effects = restarted.run()
    assert decision.mode is Mode.SAFETY_OFF
    assert not of_type(effects, Notify)
    assert restarted.state.safety_unconfirmed == frozenset()
    assert list(restarted.state.safety.active) == list(sim.state.safety.active)


def test_load_rule_override_kept_with_matching_incident() -> None:
    """``overridden`` is kept only if a warning with the same incident is active again."""
    sim = _sim()
    sim.warn(NINA_1, FIRE)
    sim.run()
    sim.command(OverrideSafetyCommand(source=Source.NOTIFICATION))
    sim.run()

    kept = _reload(sim)
    kept.warn(NINA_1, FIRE_UPDATE)
    decision, effects = kept.run()
    assert decision.mode is Mode.AUTOMATIC
    assert kept.state.safety.overridden == {"nina:großbrand - musterstadt"}

    dropped = _reload(sim, minutes=1)
    dropped.clear(NINA_1)
    assert dropped.run()[0].mode is Mode.AUTOMATIC
    assert dropped.state.safety.overridden  # still loaded during the grace
    dropped.advance(minutes=1)
    decision, effects = dropped.run()
    assert decision.mode is Mode.AUTOMATIC
    assert dropped.state.safety.overridden == frozenset()
    assert not of_type(effects, Notify)
    assert not of_type(effects, ClearNotification)
    # the same fire again is now a new shutdown
    dropped.warn(NINA_1, FIRE)
    assert dropped.run()[0].mode is Mode.SAFETY_OFF


def test_load_rule_smoke_detector_override_survives_restart() -> None:
    """A warning without key keeps its key (and override) across a restart."""
    sim = _sim()
    sim.warn(SMOKE)
    sim.run()
    sim.command(OverrideSafetyCommand(source=Source.NOTIFICATION))
    sim.run()
    restarted = _reload(sim, minutes=30)
    decision, _ = restarted.run()
    assert decision.mode is Mode.AUTOMATIC
    assert list(restarted.state.safety.active) == list(sim.state.safety.active)


def test_load_rule_restart_wait_ended_while_offline() -> None:
    """A restart wait that ended while offline: restarted, timeout_while_offline."""
    sim = _sim()
    sim.warn(SMOKE)
    sim.run()
    sim.clear(SMOKE)
    sim.run()
    restarted = _reload(sim, minutes=45, grace=False)
    assert restarted.state.safety.restart_pending is None
    decision, effects = restarted.run()
    assert decision.mode is Mode.AUTOMATIC
    assert _event(effects)["trigger"] == "timeout_while_offline"
    assert _notify(effects, NotifyKind.RESTARTED)
    assert of_type(effects, TurnOn)


def test_load_rule_restart_wait_continues() -> None:
    """A restart wait that has not ended continues with its deadline."""
    sim = _sim()
    sim.warn(SMOKE)
    sim.run()
    sim.clear(SMOKE)
    sim.run()
    deadline = sim.state.safety.restart_pending
    restarted = _reload(sim, minutes=10)
    decision, effects = restarted.run()
    assert decision.mode is Mode.SAFETY_OFF
    assert restarted.state.safety.restart_pending == deadline
    assert not of_type(effects, Notify)
