"""Modes, targets per mode and commands (docs/spec/03-modes.md, 0.1 part)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from engine import CommandRejected
from engine.models import (
    EVENT_MODE_CHANGED,
    Boost,
    BoostCommand,
    ControlSwitchCommand,
    Effect,
    FireEvent,
    Mode,
    Pause,
    PauseCommand,
    Profile,
    ProfileCommand,
    Reason,
    ResumeCommand,
    ScheduleAt,
    Source,
    WriteBlock,
)

from .helpers import (
    CO2_A,
    CO2_B,
    T0,
    Sim,
    co2_for,
    of_type,
    running_state,
    writes,
)

BUTTON = Source.BUTTON


def _sim(flow: float = 80, **kwargs: Any) -> Sim:
    co2 = co2_for(flow)
    return Sim(values={CO2_A: co2, CO2_B: co2}, **kwargs)


def _event(effects: list[Effect]) -> dict[str, object]:
    events = of_type(effects, FireEvent)
    assert len(events) == 1
    assert events[0].event_type == EVENT_MODE_CHANGED
    return dict(events[0].data)


def test_e16_bhv20_boost_immediately_without_step_limit() -> None:
    """E16: boost from automatic 120 -> 300 at once, no step limit."""
    sim = _sim(120, actuator="120")
    decision, effects = sim.run()
    assert not writes(effects)
    sim.command(BoostCommand(source=BUTTON))
    decision, effects = sim.run()
    assert decision.mode is Mode.BOOST
    assert decision.reason is Reason.BOOST
    assert decision.contributions.boost == 300
    assert decision.ends_at == T0 + timedelta(minutes=30)
    assert [(w.flow, w.bypass) for w in writes(effects)] == [(300, True)]
    event = _event(effects)
    assert event == {
        "from_mode": "automatic",
        "to_mode": "boost",
        "reason": "boost",
        "setpoint": 300,
        "trigger": "command",
        "source": "button",
        "warning": None,
    }


def test_e17_bhv20_boost_below_co2_demand() -> None:
    """E17: boost while CO₂ demands 400 -> 400, reason co2."""
    sim = _sim(400, actuator="400")
    sim.command(BoostCommand(source=BUTTON))
    decision, _ = sim.run()
    assert decision.mode is Mode.BOOST
    assert decision.target == 400
    assert decision.reason is Reason.CO2


def test_e18_bhv20_boost_twice_extends() -> None:
    """E18: boost twice (30 + 30 min) -> until = start + 60 min."""
    sim = _sim()
    sim.command(BoostCommand(source=BUTTON))
    sim.command(BoostCommand(source=BUTTON))
    assert isinstance(sim.state.manual, Boost)
    assert sim.state.manual.until == T0 + timedelta(minutes=60)
    assert sim.state.manual.started == T0


def test_e19_bhv20_boost_extension_capped() -> None:
    """E19: extended beyond ``boost_max_duration`` (4 h) -> capped."""
    sim = _sim()
    sim.command(BoostCommand(source=BUTTON, duration=timedelta(hours=3)))
    sim.advance(minutes=10)
    sim.command(BoostCommand(source=BUTTON, duration=timedelta(hours=2)))
    assert isinstance(sim.state.manual, Boost)
    assert sim.state.manual.until == sim.now + timedelta(hours=4)
    new = _sim()
    new.command(BoostCommand(source=BUTTON, duration=timedelta(hours=12)))
    assert isinstance(new.state.manual, Boost)
    assert new.state.manual.until == T0 + timedelta(hours=4)


def test_e20_bhv21_last_command_wins() -> None:
    """E20: pause during boost, then boost during pause: last command wins."""
    sim = _sim()
    sim.command(BoostCommand(source=BUTTON))
    assert sim.run()[0].mode is Mode.BOOST
    sim.command(PauseCommand(source=BUTTON, duration=timedelta(hours=1)))
    decision, _ = sim.run()
    assert decision.mode is Mode.OFF
    assert decision.reason is Reason.OFF
    assert decision.ends_at == T0 + timedelta(hours=1)
    sim.command(BoostCommand(source=BUTTON))
    decision, _ = sim.run()
    assert decision.mode is Mode.BOOST
    assert decision.ends_at == T0 + timedelta(minutes=30)


def test_e21_bhv20_resume_during_boost() -> None:
    """E21: resume during boost -> immediate automatic value."""
    sim = _sim()
    sim.command(BoostCommand(source=BUTTON))
    sim.run()
    sim.advance(minutes=1)
    sim.command(ResumeCommand(source=Source.SERVICE))
    decision, effects = sim.run()
    assert decision.mode is Mode.AUTOMATIC
    assert [(w.flow, w.bypass) for w in writes(effects)] == [(80, True)]
    assert _event(effects)["source"] == "service"


def test_e22_bhv20_boost_expires() -> None:
    """E22: boost expires -> automatic, trigger timeout, no shaping on the way down."""
    sim = _sim()
    sim.command(BoostCommand(source=BUTTON))
    _, effects = sim.run()
    assert of_type(effects, ScheduleAt) == [ScheduleAt(when=T0 + timedelta(minutes=30))]
    sim.advance(minutes=30)
    decision, effects = sim.run()
    assert decision.mode is Mode.AUTOMATIC
    assert decision.ends_at is None
    assert [(w.flow, w.bypass) for w in writes(effects)] == [(80, True)]
    event = _event(effects)
    assert event["trigger"] == "timeout"
    assert event["source"] is None


def test_timed_pause_expires() -> None:
    """A timed pause ends at ``until`` with trigger timeout."""
    sim = _sim()
    sim.command(PauseCommand(source=BUTTON, duration=timedelta(hours=1)))
    sim.run()
    sim.advance(hours=1)
    decision, effects = sim.run()
    assert decision.mode is Mode.AUTOMATIC
    assert _event(effects)["trigger"] == "timeout"


def test_e32_bhv03_disabled_shows_demand_without_writing() -> None:
    """E32: control disabled, CO₂ 1400 -> mode disabled, setpoint 400, no write."""
    sim = _sim(400, state=running_state(control_enabled=False, last_mode=Mode.DISABLED))
    decision, effects = sim.run()
    assert decision.mode is Mode.DISABLED
    assert decision.setpoint == 400
    assert decision.reason is Reason.CO2
    assert decision.write_blocked_by is WriteBlock.DISABLED
    assert not writes(effects)


def test_e44_bhv26_quiet_profile_all_day_and_boost() -> None:
    """E44: profile quiet, 14:00, demand 350 -> 250; a user boost is not capped.

    SPEC-GAP: the table expects 300 for the boost, but the rule
    ``max(level, demand_unquiet)`` (02-control, 03-modes, E54) gives 350.
    """
    sim = _sim(350, now=T0.replace(hour=14), state=running_state(profile=Profile.QUIET))
    decision, _ = sim.run()
    assert decision.target == 250
    assert decision.reason is Reason.QUIET_LIMIT
    sim.command(BoostCommand(source=BUTTON))
    decision, _ = sim.run()
    assert decision.mode is Mode.BOOST
    assert decision.target == 350
    assert decision.target > 250
    assert decision.reason is Reason.CO2


def test_quiet_profile_boost_level_above_demand() -> None:
    """Quiet profile, demand below the level: the boost level is not capped."""
    sim = _sim(80, state=running_state(profile=Profile.QUIET))
    sim.command(BoostCommand(source=BUTTON))
    decision, _ = sim.run()
    assert decision.target == 300
    assert decision.reason is Reason.BOOST


def test_e47_bhv26_profile_change_bypasses_shaping() -> None:
    """E47: balanced -> quiet while 400 is written -> 250 in the same evaluation."""
    sim = _sim(400, actuator="400")
    _, effects = sim.run()
    assert not writes(effects)
    sim.advance(minutes=1)
    sim.command(ProfileCommand(source=Source.BUTTON, profile=Profile.QUIET))
    decision, effects = sim.run()
    assert decision.profile is Profile.QUIET
    assert [(w.flow, w.bypass) for w in writes(effects)] == [(250, True)]
    assert not of_type(effects, FireEvent)


def test_e50_bhv20_boost_level_change_is_written_at_once() -> None:
    """E50: boost 300 running, boost again with level 400 -> 400 at once, extended."""
    sim = _sim()
    sim.command(BoostCommand(source=BUTTON))
    sim.run()
    sim.advance(minutes=1)
    sim.command(BoostCommand(source=Source.SERVICE, level=400))
    decision, effects = sim.run()
    assert [(w.flow, w.bypass) for w in writes(effects)] == [(400, True)]
    assert decision.ends_at == T0 + timedelta(minutes=60)
    assert not of_type(effects, FireEvent)


def test_e51_bhv03_control_switch_off_ends_boost_and_rejects_commands() -> None:
    """E51: control off during boost ends it; boost and pause are rejected."""
    sim = _sim()
    sim.command(BoostCommand(source=BUTTON))
    sim.run()
    sim.command(ControlSwitchCommand(source=Source.SERVICE, enabled=False))
    assert sim.state.manual is None
    decision, effects = sim.run()
    assert decision.mode is Mode.DISABLED
    assert _event(effects)["trigger"] == "control_switch"
    with pytest.raises(CommandRejected) as err:
        sim.command(BoostCommand(source=BUTTON))
    assert err.value.code == "control_disabled"
    with pytest.raises(CommandRejected):
        sim.command(PauseCommand(source=BUTTON))
    before = sim.state
    sim.command(ResumeCommand(source=BUTTON))
    assert sim.state is before
    sim.command(ProfileCommand(source=BUTTON, profile=Profile.WINTER))
    assert sim.state.profile is Profile.WINTER
    sim.command(ControlSwitchCommand(source=BUTTON, enabled=False))
    assert sim.state.control_enabled is False
    sim.command(ControlSwitchCommand(source=BUTTON, enabled=True))
    decision, effects = sim.run()
    assert decision.mode is Mode.AUTOMATIC
    assert _event(effects)["trigger"] == "control_switch"


def test_e54_bhv14_boost_uses_demand_before_quiet_limit() -> None:
    """E54: profile quiet, CO₂ 1400, user boost 300 -> 400, reason co2."""
    sim = Sim(
        values={CO2_A: 1400, CO2_B: 1400},
        state=running_state(profile=Profile.QUIET),
    )
    assert sim.run()[0].target == 250
    sim.command(BoostCommand(source=BUTTON))
    decision, _ = sim.run()
    assert decision.target == 400
    assert decision.reason is Reason.CO2


def test_boost_defaults_and_level_validation() -> None:
    """Defaults come from the parameter entities; levels outside min…max are rejected."""
    sim = _sim()
    with pytest.raises(CommandRejected) as err:
        sim.command(BoostCommand(source=BUTTON, level=500))
    assert err.value.code == "level_out_of_range"
    sim.command(
        BoostCommand(
            source=Source.VOICE,
            default_duration=timedelta(minutes=15),
            default_level=200,
        )
    )
    assert sim.state.manual == Boost(
        until=T0 + timedelta(minutes=15), level=200, started=T0, source=Source.VOICE
    )


def test_pause_indefinite_and_resume_without_manual() -> None:
    """Pause without duration is indefinite; resume without manual does nothing."""
    sim = _sim()
    before = sim.state
    sim.command(ResumeCommand(source=BUTTON))
    assert sim.state is before
    sim.command(PauseCommand(source=BUTTON))
    assert sim.state.manual == Pause(until=None, started=T0, source=BUTTON)
    decision, _ = sim.run()
    assert decision.ends_at is None
    assert decision.mode is Mode.OFF
    sim.command(ProfileCommand(source=BUTTON, profile=Profile.BALANCED))
    assert sim.state.bypass_pending is False
