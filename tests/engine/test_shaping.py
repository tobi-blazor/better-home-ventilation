"""Output shaping (BHV-16, docs/spec/02-control.md#output-shaping)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from engine.models import Limit, Profile, Reason, WriteBlock
from engine.shaping import clamp, quantise, shape

from .helpers import (
    CO2_A,
    CO2_B,
    HUM_A,
    T0,
    Sim,
    make_options,
    running_state,
    writes,
)


def test_e13_bhv16_step_limit_one_step_per_interval() -> None:
    """E13: current 80, target 400 -> 130, after 5 min 180, ... (steps of 50)."""
    sim = Sim(values={CO2_A: 1600, CO2_B: 1600})
    written: list[float] = []
    for _ in range(7):
        decision, effects = sim.run()
        assert decision.target == 400
        written.extend(effect.flow for effect in writes(effects))
        if written[-1] < 400:
            sim.advance(minutes=1)
            decision, effects = sim.run()
            assert not writes(effects)
            assert decision.write_blocked_by is WriteBlock.INTERVAL
            sim.advance(minutes=4)
    assert written == [130, 180, 230, 280, 330, 380, 400]
    sim.advance(minutes=5)
    decision, effects = sim.run()
    assert not writes(effects)
    assert decision.write_blocked_by is None


@pytest.mark.parametrize("target", [185, 195])
def test_e14_bhv16_no_oscillation_inside_deadband_down(target: float) -> None:
    """E14: current 200, target alternating 185/195 -> no write (dead band 20)."""
    result = shape(
        target, 200, bypass=False, now=T0, last_write=None, options=make_options()
    )
    assert result.value is None
    assert result.blocked_by is WriteBlock.DEADBAND


def test_e14_alternating_target_through_the_engine() -> None:
    """E14 end to end: the unit stays at 200 while the target alternates."""
    sim = Sim(actuator="200")
    for flow in (185, 195, 185, 195):
        co2 = 850 + (flow - 80) * 550 / 320
        sim.set(CO2_A, co2)
        sim.set(CO2_B, co2)
        decision, effects = sim.run()
        assert not writes(effects)
        assert decision.setpoint == 200
        sim.advance(minutes=10)


def test_e15_bhv16_min_write_interval() -> None:
    """E15: 4 min after the last write in automatic -> held, ``interval``."""
    sim = Sim(
        values={CO2_A: 1600, CO2_B: 1600},
        state=running_state(last_write=T0 - timedelta(minutes=4)),
    )
    decision, effects = sim.run()
    assert not writes(effects)
    assert decision.write_blocked_by is WriteBlock.INTERVAL
    assert decision.setpoint == 80


def test_bhv16_deadband_up() -> None:
    """Up: below ``deadband_up`` (10) hold, at it write."""
    options = make_options()
    held = shape(205, 200, bypass=False, now=T0, last_write=None, options=options)
    assert held.blocked_by is WriteBlock.DEADBAND
    moved = shape(210, 200, bypass=False, now=T0, last_write=None, options=options)
    assert moved.value == 210


def test_bhv16_bypass_and_equal_target() -> None:
    """Bypass writes the target at once; target == current holds silently."""
    options = make_options()
    bypass = shape(400, 80, bypass=True, now=T0, last_write=T0, options=options)
    assert bypass.value == 400
    unknown = shape(400, None, bypass=False, now=T0, last_write=T0, options=options)
    assert unknown.value == 400
    equal = shape(200, 200, bypass=False, now=T0, last_write=None, options=options)
    assert equal.value is None
    assert equal.blocked_by is None


def test_step_down_and_quantised_step() -> None:
    """Steps down are limited too; the stepped value is quantised again."""
    options = make_options()
    down = shape(80, 400, bypass=False, now=T0, last_write=None, options=options)
    assert down.value == 350
    odd = shape(400, 152, bypass=False, now=T0, last_write=None, options=options)
    assert odd.value == 200


def test_quantise_rounds_half_up_and_clamp() -> None:
    """Quantisation to ``flow_quantum``; halves round up; quantum 0 = off."""
    assert quantise(229.33, 5) == 230
    assert quantise(227.5, 5) == 230
    assert quantise(232.4, 5) == 230
    assert quantise(123.4, 0) == 123.4
    assert clamp(500, 0, 400) == 400
    assert clamp(-1, 0, 400) == 0


def test_bhv14_strict_down_ignores_the_deadband_only() -> None:
    """`strict_down` (quiet limit): no dead band down; interval and step still apply."""
    options = make_options()
    held = shape(250, 260, bypass=False, now=T0, last_write=None, options=options)
    assert held.blocked_by is WriteBlock.DEADBAND
    strict = shape(
        250,
        260,
        bypass=False,
        now=T0,
        last_write=None,
        options=options,
        strict_down=True,
    )
    assert strict.value == 250
    stepped = shape(
        250,
        400,
        bypass=False,
        now=T0,
        last_write=None,
        options=options,
        strict_down=True,
    )
    assert stepped.value == 350
    interval = shape(
        250,
        260,
        bypass=False,
        now=T0,
        last_write=T0 - timedelta(minutes=4),
        options=options,
        strict_down=True,
    )
    assert interval.blocked_by is WriteBlock.INTERVAL
    up = shape(
        255,
        250,
        bypass=False,
        now=T0,
        last_write=None,
        options=options,
        strict_down=True,
    )
    assert up.blocked_by is WriteBlock.DEADBAND  # the dead band up is unchanged


NIGHT = T0.replace(hour=23)


def _night(current: str, flow: float, **kwargs: object) -> tuple[float, object]:
    """Run one evaluation at 23:00 with CO₂ asking for ``flow``; (written, reason)."""
    co2 = 850 + (flow - 80) * 550 / 320
    sim = Sim(
        make_options(night_enabled=True, **kwargs),
        now=NIGHT,
        values={CO2_A: co2, CO2_B: co2},
        actuator=current,
        state=running_state(last_write=NIGHT - timedelta(minutes=10)),
    )
    decision, effects = sim.run()
    flows = [effect.flow for effect in writes(effects)]
    return (flows[0] if flows else decision.setpoint), decision.reason


@pytest.mark.parametrize(
    ("current", "flow", "expected"),
    [
        ("260", 350, 250),  # capped target, 10 above: written despite dead band 20
        ("255", 350, 250),
        ("400", 350, 350),  # step down limited to 50
        ("260", 245, 245),  # target below the limit, current above it
        ("245", 230, 245),  # both below the limit: dead band holds
        ("250", 240, 250),  # at the limit, not above it: dead band holds
    ],
)
def test_e09a_bhv14_quiet_limit_is_strict(
    current: str, flow: float, expected: float
) -> None:
    """E9a: above an active quiet limit the dead band down does not hold the flow."""
    written, _ = _night(current, flow)
    assert written == expected


def test_e09a_quiet_limit_quantised_below_its_value() -> None:
    """Quiet max 252 quantises to a target of 250: 252 is above the target."""
    written, reason = _night("252", 350, quiet_max_flow=252)
    assert (written, reason) == (250, Reason.QUIET_LIMIT)


def test_e09a_quiet_strict_not_without_the_quiet_limit() -> None:
    """Day, profile air quality or humidity override: the normal dead band applies."""
    day_sim = Sim(
        make_options(night_enabled=True),
        values={CO2_A: 850 + 170 * 550 / 320, CO2_B: 700},
        actuator="260",
    )
    decision, effects = day_sim.run()
    assert decision.target == 250
    assert not writes(effects)
    assert decision.write_blocked_by is WriteBlock.DEADBAND

    co2 = 850 + 170 * 550 / 320  # demand 250
    air_quality = Sim(
        make_options(night_enabled=True),
        now=NIGHT,
        values={CO2_A: co2, CO2_B: co2},
        actuator="260",
        state=running_state(
            profile=Profile.AIR_QUALITY, last_write=NIGHT - timedelta(minutes=10)
        ),
    )
    decision, effects = air_quality.run()
    assert Limit.QUIET not in decision.limits
    assert not writes(effects)

    co2 = 850 + 305 * 550 / 320  # demand 385
    humid = Sim(
        make_options(night_enabled=True),
        now=NIGHT,
        values={CO2_A: co2, CO2_B: co2, HUM_A: 66},
        actuator="400",
        state=running_state(last_write=NIGHT - timedelta(minutes=10)),
    )
    decision, effects = humid.run()
    assert Limit.QUIET not in decision.limits  # humidity override
    assert decision.target == 385
    assert not writes(effects)  # 15 below: the dead band holds


def test_e09a_night_window_starts_while_above_the_limit() -> None:
    """Observed: 260 in the evening; from 22:00 the quiet limit 250 is written.

    Before, the dead band down (20) held 260 for hours above the limit.
    """
    co2 = 850 + 180 * 550 / 320  # demand 260
    sim = Sim(
        make_options(night_enabled=True),
        now=T0.replace(hour=21, minute=50),
        values={CO2_A: co2, CO2_B: co2},
        actuator="260",
        state=running_state(last_write=T0.replace(hour=21)),
    )
    decision, effects = sim.run()
    assert (decision.target, decision.setpoint) == (260, 260)
    assert not writes(effects)
    sim.advance(minutes=10)
    decision, effects = sim.run()
    assert (decision.target, decision.reason) == (250, Reason.QUIET_LIMIT)
    assert [effect.flow for effect in writes(effects)] == [250]
    assert decision.setpoint == 250


def test_zero_step_cannot_move() -> None:
    """A step that quantises back to the current value is a hold."""
    options = make_options(max_step_up=1)
    result = shape(400, 200, bypass=False, now=T0, last_write=None, options=options)
    assert result.value is None
    assert result.blocked_by is WriteBlock.DEADBAND
