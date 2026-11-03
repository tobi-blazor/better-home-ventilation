"""Reminders for long off states (BHV-24, docs/spec/03-modes.md#reminders-bhv-24).

Scenarios E40 and E60 (owner decision on Q15).
"""

from __future__ import annotations

from datetime import timedelta

from engine import options as opt
from engine.models import (
    ClearNotification,
    ControllerState,
    ControlSwitchCommand,
    Effect,
    Mode,
    NotificationTag,
    Notify,
    NotifyKind,
    Pause,
    PauseCommand,
    ResumeCommand,
    ScheduleAt,
    Source,
    TurnOn,
    WarningSource,
)

from .helpers import OFF_SWITCH, T0, Sim, make_options, of_type

SMOKE = "binary_sensor.hallway_smoke"
CLEAR_REMINDER = ClearNotification(tag=NotificationTag.REMINDER)


REMINDER_KINDS = (NotifyKind.REMINDER, NotifyKind.REMINDER_CONTROL_OFF)


def _reminders(effects: list[Effect]) -> list[Notify]:
    return [n for n in of_type(effects, Notify) if n.kind in REMINDER_KINDS]


def _sim() -> Sim:
    source = WarningSource(
        subentry_id="smoke",
        name="Smoke",
        entities=(SMOKE,),
        details=opt.DETAILS_NONE,
    )
    sim = Sim(
        make_options(off_entity=OFF_SWITCH, warning_sources=(source,)),
        off="off",
        values={SMOKE: "off"},
    )
    sim.run()
    return sim


def test_e40_bhv24_indefinite_pause_reminder_after_8h_then_daily() -> None:
    """E40: indefinite pause -> reminder after 8 h, then every 24 h."""
    sim = _sim()
    sim.command(PauseCommand(source=Source.BUTTON))
    _, effects = sim.run()
    assert sim.state.off_since == T0
    assert ScheduleAt(when=T0 + timedelta(hours=8)) in effects

    sim.advance(hours=7, minutes=59)
    assert not _reminders(sim.run()[1])
    sim.advance(minutes=1)
    _, effects = sim.run()
    reminder = _reminders(effects)
    assert reminder == [Notify(kind=NotifyKind.REMINDER, off_since=T0)]
    assert reminder[0].tag is NotificationTag.REMINDER
    assert sim.state.last_reminder == sim.now
    assert ScheduleAt(when=sim.now + timedelta(hours=24)) in effects

    sim.advance(hours=12)
    assert not _reminders(sim.run()[1])
    sim.advance(hours=12)
    assert len(_reminders(sim.run()[1])) == 1
    assert sim.state.last_reminder == T0 + timedelta(hours=32)

    sim.command(ResumeCommand(source=Source.NOTIFICATION))
    decision, effects = sim.run()
    assert decision.mode is Mode.AUTOMATIC
    assert CLEAR_REMINDER in effects
    assert sim.state.off_since is None
    assert sim.state.last_reminder is None


def test_bhv24_timed_pause_ending_before_threshold_sends_nothing() -> None:
    """A timed pause that ends before the threshold sends nothing."""
    sim = _sim()
    sim.command(PauseCommand(source=Source.BUTTON, duration=timedelta(hours=8)))
    sim.run()
    sim.advance(hours=8)
    decision, effects = sim.run()
    assert decision.mode is Mode.AUTOMATIC
    assert not of_type(effects, Notify)
    assert not of_type(effects, ClearNotification)


def test_bhv24_long_timed_pause_after_24h() -> None:
    """A timed pause longer than 24 h is reminded after ``off_notice_after``."""
    sim = _sim()
    sim.command(PauseCommand(source=Source.BUTTON, duration=timedelta(hours=48)))
    sim.run()
    sim.advance(hours=8)
    assert not _reminders(sim.run()[1])
    sim.advance(hours=16)
    assert len(_reminders(sim.run()[1])) == 1


def test_bhv24_external_stop_is_an_indefinite_pause() -> None:
    """A stop at the unit (external, indefinite pause) is reminded after 8 h."""
    sim = _sim()
    sim.set_off("on", foreign=True)
    decision, _ = sim.run()
    assert decision.mode is Mode.OFF
    sim.advance(hours=8)
    assert len(_reminders(sim.run()[1])) == 1


def test_bhv24_safety_off_reminder_after_24h() -> None:
    """A long safety_off is reminded after ``off_notice_after`` (24 h)."""
    sim = _sim()
    sim.warn(SMOKE)
    sim.run()
    sim.advance(hours=8)
    assert not _reminders(sim.run()[1])
    sim.advance(hours=16)
    assert len(_reminders(sim.run()[1])) == 1


def test_bhv24_off_since_kept_from_pause_to_safety_off() -> None:
    """``off_since`` spans both off states; the next reminder 24 h after the last."""
    sim = _sim()
    sim.command(PauseCommand(source=Source.BUTTON))
    sim.run()
    sim.advance(hours=8)
    assert _reminders(sim.run()[1])
    sim.advance(hours=1)
    sim.warn(SMOKE)
    decision, _ = sim.run()
    assert decision.mode is Mode.SAFETY_OFF
    assert sim.state.off_since == T0
    sim.advance(hours=23)
    assert len(_reminders(sim.run()[1])) == 1


def test_bhv24_no_reminder_during_startup_grace() -> None:
    """After a restart the due reminder waits for the end of the grace."""
    pause = Pause(until=None, started=T0, source=Source.BUTTON)
    now = T0 + timedelta(hours=10)
    sim = Sim(
        make_options(off_entity=OFF_SWITCH),
        now=now,
        off="on",
        started_at=now,
        state=ControllerState(manual=pause, off_since=T0, last_mode=Mode.OFF),
    )
    _, effects = sim.run()
    assert not _reminders(effects)
    sim.advance(minutes=1)
    assert len(_reminders(sim.run()[1])) == 1


def test_bhv24_off_since_starts_with_the_off_mode() -> None:
    """Without a stored ``off_since`` it is set when an off mode is first seen."""
    sim = _sim()
    sim.command(PauseCommand(source=Source.BUTTON))
    sim.advance(minutes=5)
    sim.run()
    assert sim.state.off_since == T0 + timedelta(minutes=5)


# --- Q15: a stopped unit while control is disabled ---------------------------


def test_e60_q15_pause_then_control_off_keeps_reminding() -> None:
    """E60: a pause stopped the unit, then control off: reminders go on.

    Owner decision on Q15 (2026-10-30): while control is disabled a stopped
    unit is reminded like an off state; the threshold is ``off_notice_after``
    (the pause ended), counted from the start of the pause.
    """
    sim = _sim()
    sim.command(PauseCommand(source=Source.BUTTON))
    sim.run()
    assert sim.off is not None
    assert sim.off.state == "on"  # stopped by the integration
    sim.advance(hours=1)
    sim.command(ControlSwitchCommand(source=Source.BUTTON, enabled=False))
    decision, effects = sim.run()
    assert decision.mode is Mode.DISABLED
    assert sim.state.off_since == T0
    assert ScheduleAt(when=T0 + timedelta(hours=24)) in effects

    sim.advance(hours=7)  # 8 h: the pause threshold no longer applies
    assert not _reminders(sim.run()[1])
    sim.advance(hours=16)
    _, effects = sim.run()
    # While control is off the action is "Turn control on" (2026-11-03).
    reminder = Notify(kind=NotifyKind.REMINDER_CONTROL_OFF, off_since=T0)
    assert _reminders(effects) == [reminder]
    assert reminder.tag is NotificationTag.REMINDER
    sim.advance(hours=24)
    assert _reminders(sim.run()[1]) == [reminder]

    # "Turn control on": the unit is switched on, the reminder cleared.
    sim.command(ControlSwitchCommand(source=Source.NOTIFICATION, enabled=True))
    decision, effects = sim.run()
    assert decision.mode is Mode.AUTOMATIC
    assert of_type(effects, TurnOn) == [TurnOn(entity_id=OFF_SWITCH)]
    assert CLEAR_REMINDER in effects
    assert sim.state.off_since is None


def test_e60_q15_unit_started_while_disabled_ends_the_reminders() -> None:
    """E60: started at the unit while disabled (no command): reminder cleared."""
    sim = _sim()
    sim.command(ControlSwitchCommand(source=Source.BUTTON, enabled=False))
    sim.run()
    sim.set_off("on", foreign=True)  # stopped at the unit: ignored as command
    decision, _ = sim.run()
    assert decision.mode is Mode.DISABLED
    assert sim.state.off_since == T0
    sim.advance(hours=24)
    assert len(_reminders(sim.run()[1])) == 1

    sim.advance(hours=1)
    sim.set_off("unavailable")  # unknown: the count goes on
    assert not of_type(sim.run()[1], ClearNotification)
    assert sim.state.off_since == T0
    sim.set_off("off", foreign=True)
    decision, effects = sim.run()
    assert decision.mode is Mode.DISABLED
    assert CLEAR_REMINDER in effects
    assert sim.state.off_since is None


def test_e60_q15_lowest_value_without_off_actuator() -> None:
    """E60 without off actuator: the lowest actuator value counts as stopped."""
    sim = Sim(make_options(actuator_min=0), actuator="0")
    sim.command(ControlSwitchCommand(source=Source.BUTTON, enabled=False))
    decision, _ = sim.run()
    assert decision.mode is Mode.DISABLED
    assert sim.state.off_since == T0
    sim.advance(hours=12)
    sim.set_actuator("unavailable")
    assert not _reminders(sim.run()[1])
    assert sim.state.off_since == T0
    sim.advance(hours=12)
    sim.set_actuator("0")
    assert len(_reminders(sim.run()[1])) == 1
    sim.set_actuator("120", foreign=True)
    _, effects = sim.run()
    assert CLEAR_REMINDER in effects
    assert sim.state.off_since is None
    assert not of_type(effects, ScheduleAt)


def test_e60_q15_running_unit_while_disabled_gets_no_reminder() -> None:
    """E60: a running unit while disabled is no off state; unknown starts none."""
    sim = _sim()
    sim.command(ControlSwitchCommand(source=Source.BUTTON, enabled=False))
    sim.run()
    assert sim.state.off_since is None
    sim.set_off("unavailable")
    sim.run()
    assert sim.state.off_since is None
    sim.advance(hours=48)
    assert not _reminders(sim.run()[1])


def test_e60_reminder_shown_when_control_is_switched_off_is_replaced() -> None:
    """E60: a pause reminder (Resume) is replaced by the control-off one at once."""
    sim = _sim()
    sim.command(PauseCommand(source=Source.BUTTON))
    sim.run()
    sim.advance(hours=8)
    assert _reminders(sim.run()[1]) == [Notify(kind=NotifyKind.REMINDER, off_since=T0)]
    sim.advance(hours=1)
    sim.command(ControlSwitchCommand(source=Source.BUTTON, enabled=False))
    _, effects = sim.run()
    assert _reminders(effects) == [
        Notify(kind=NotifyKind.REMINDER_CONTROL_OFF, off_since=T0)
    ]
    assert sim.state.last_reminder == T0 + timedelta(hours=8)  # same schedule
    sim.advance(hours=1)
    assert not _reminders(sim.run()[1])
    sim.advance(hours=22)
    assert len(_reminders(sim.run()[1])) == 1


def test_e60_control_on_during_safety_off_replaces_the_reminder() -> None:
    """E60: control on while the unit stays off (safety_off): back to Resume."""
    sim = _sim()
    sim.command(ControlSwitchCommand(source=Source.BUTTON, enabled=False))
    sim.run()
    sim.warn(SMOKE)  # safety_when_disabled: the unit is switched off
    decision, _ = sim.run()
    assert decision.mode is Mode.SAFETY_OFF
    sim.advance(hours=24)
    assert _reminders(sim.run()[1]) == [
        Notify(kind=NotifyKind.REMINDER_CONTROL_OFF, off_since=T0)
    ]
    sim.advance(hours=1)
    sim.command(ControlSwitchCommand(source=Source.BUTTON, enabled=True))
    decision, effects = sim.run()
    assert decision.mode is Mode.SAFETY_OFF
    assert _reminders(effects) == [Notify(kind=NotifyKind.REMINDER, off_since=T0)]
