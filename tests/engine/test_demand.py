"""Automatic demand, dryness protection, limits and profiles.

docs/spec/02-control.md#automatic-demand, #limits, #profiles.
"""

from __future__ import annotations

from datetime import time, timedelta

import pytest

from engine.demand import in_window, quiet_limit_applies, ramp, update_dryness
from engine.models import Limit, Mode, Profile, Reason

from .helpers import (
    CO2_A,
    CO2_B,
    HUM_A,
    HUM_B,
    PRESENCE,
    T0,
    Sim,
    co2_for,
    make_options,
    running_state,
)

NIGHT = T0.replace(hour=23)


def _co2(value: float | str) -> dict[str, str | float | None]:
    return {CO2_A: value, CO2_B: value}


@pytest.mark.parametrize(
    ("co2", "expected", "reason"),
    [
        (700, 80, Reason.MINIMUM),
        (850, 80, Reason.MINIMUM),
        (1125, 240, Reason.CO2),
        (1400, 400, Reason.CO2),
        (1600, 400, Reason.CO2),
    ],
)
def test_e01_bhv10_co2_ramp(co2: float, expected: float, reason: Reason) -> None:
    """E1: CO₂ ramp from 80 m³/h at 850 ppm to 400 m³/h at 1400 ppm."""
    decision, _ = Sim(values=_co2(co2)).run()
    assert decision.target == expected
    assert decision.reason is reason


def test_e02_bhv10_highest_co2_sensor_counts() -> None:
    """E2: 900 / 1200 ppm -> the ramp uses 1200 from the second sensor."""
    decision, _ = Sim(values={CO2_A: 900, CO2_B: 1200}).run()
    assert decision.inputs.co2_max == 1200
    assert decision.inputs.co2_max_entity == CO2_B
    assert decision.contributions.co2 == pytest.approx(283.64, abs=0.01)
    assert decision.target == 285


def test_e04_bhv19_all_co2_invalid_is_fallback() -> None:
    """E4: no valid CO₂ value -> mode fallback, setpoint 150, reason fallback."""
    decision, effects = Sim(values=_co2("unavailable")).run()
    assert decision.mode is Mode.FALLBACK
    assert decision.setpoint == 150
    assert decision.reason is Reason.FALLBACK
    assert decision.contributions.fallback == 150
    assert effects


def test_e06_bhv13_humidity_high_ramp() -> None:
    """E6: humidity max 62 % -> ≈ 229 m³/h, quantised 230, reason humidity_high."""
    decision, _ = Sim(values={HUM_A: 62, HUM_B: 50}).run()
    assert decision.contributions.humidity_high == pytest.approx(229.33, abs=0.01)
    assert decision.target == 230
    assert decision.reason is Reason.HUMIDITY_HIGH


def _humidity_only(**overrides: object) -> Sim:
    return Sim(make_options(co2_sensors=[], **overrides), values={HUM_A: 58, HUM_B: 18})


def test_e07_bhv12_humidity_high_limited_while_dry() -> None:
    """E7: mean 38 %, max 58 % -> dry; humidity-high for 60 min, then suppressed."""
    sim = _humidity_only()
    decision, _ = sim.run()
    assert Limit.DRY in decision.limits
    assert decision.target == 145  # ramp(58) = 144
    assert decision.reason is Reason.HUMIDITY_HIGH
    assert sim.state.dry_humidity_high_since == T0

    sim.advance(minutes=59)
    decision, _ = sim.run()
    assert decision.target == 145

    sim.advance(minutes=1)
    decision, _ = sim.run()
    assert decision.target == 80
    assert decision.reason is Reason.DRYNESS_PROTECTION


def test_bhv12_limit_zero_suppresses_at_once() -> None:
    """``dryness_humidity_high_limit`` 0 = humidity-high suppressed while dry."""
    decision, _ = _humidity_only(dryness_humidity_high_limit=0).run()
    assert decision.target == 80
    assert decision.reason is Reason.DRYNESS_PROTECTION


def test_bhv12_counter_resets_when_humidity_drops_below_start() -> None:
    """The counter resets when h_max drops below humidity_high_start."""
    sim = _humidity_only()
    sim.run()
    sim.advance(minutes=30)
    sim.set(HUM_A, 54)
    sim.set(HUM_B, 22)
    sim.run()
    assert sim.state.dry_humidity_high_since is None
    sim.advance(minutes=1)
    sim.set(HUM_A, 58)
    sim.set(HUM_B, 18)
    decision, _ = sim.run()
    assert sim.state.dry_humidity_high_since == sim.now
    assert decision.reason is Reason.HUMIDITY_HIGH


def test_e08_bhv12_dryness_hysteresis() -> None:
    """E8: after dry, a mean of 40.5 % stays dry (hysteresis up to 42 %)."""
    options = make_options()
    dry = update_dryness(
        active=False,
        humidity_high_since=None,
        h_mean=38,
        h_max=40,
        options=options,
        now=T0,
    )
    assert dry.active
    stays = update_dryness(
        active=True,
        humidity_high_since=None,
        h_mean=40.5,
        h_max=41,
        options=options,
        now=T0,
    )
    assert stays.active
    ends = update_dryness(
        active=True,
        humidity_high_since=None,
        h_mean=42,
        h_max=43,
        options=options,
        now=T0,
    )
    assert not ends.active
    not_yet = update_dryness(
        active=False,
        humidity_high_since=None,
        h_mean=40.5,
        h_max=41,
        options=options,
        now=T0,
    )
    assert not not_yet.active
    no_mean = update_dryness(
        active=True,
        humidity_high_since=T0,
        h_mean=None,
        h_max=None,
        options=options,
        now=T0,
    )
    assert not no_mean.active
    assert no_mean.humidity_high_since is None


def test_e49_bhv12_humidity_high_allowed_again_when_dry_ends() -> None:
    """E49: suppressed by dryness, then dry ends while h_max > 55 -> allowed, reset."""
    sim = _humidity_only()
    sim.run()
    sim.advance(minutes=60)
    decision, _ = sim.run()
    assert decision.reason is Reason.DRYNESS_PROTECTION

    sim.advance(minutes=1)
    sim.set(HUM_B, 30)  # mean 44 ≥ 42: dry ends
    decision, _ = sim.run()
    assert Limit.DRY not in decision.limits
    assert sim.state.dry_humidity_high_since is None
    assert decision.target == 145
    assert decision.reason is Reason.HUMIDITY_HIGH


def test_e09_bhv14_quiet_limit_at_night() -> None:
    """E9: night 23:00, demand 350 -> 250, reason quiet_limit."""
    decision, _ = Sim(now=NIGHT, values=_co2(co2_for(350))).run()
    assert decision.target == 250
    assert decision.reason is Reason.QUIET_LIMIT
    assert Limit.QUIET in decision.limits


def test_e10_bhv14_quiet_limit_humidity_override() -> None:
    """E10: night, humidity max 66 % -> not limited."""
    decision, _ = Sim(now=NIGHT, values={HUM_A: 66, HUM_B: 50}).run()
    assert decision.target == 315  # ramp(66) = 314.7
    assert decision.reason is Reason.HUMIDITY_HIGH
    assert Limit.QUIET not in decision.limits


@pytest.mark.parametrize(
    ("moment", "inside"),
    [
        (time(6, 59), True),
        (time(7, 0), False),
        (time(22, 0), True),
        (time(21, 59), False),
        (time(0, 0), True),
        (time(12, 0), False),
    ],
)
def test_e11_bhv14_night_window_across_midnight(moment: time, inside: bool) -> None:
    """E11: window 22:00 to 07:00, start inclusive, end exclusive."""
    assert in_window(moment, time(22), time(7)) is inside
    options = make_options()
    now = T0.replace(hour=moment.hour, minute=moment.minute)
    assert quiet_limit_applies(Profile.BALANCED, options, now) is inside


def test_night_window_within_one_day_and_empty() -> None:
    """A window that does not cross midnight; start == end is empty."""
    assert in_window(time(13), time(12), time(14))
    assert not in_window(time(14), time(12), time(14))
    assert not in_window(time(12), time(12), time(12))


def test_bhv14_night_disabled() -> None:
    """Without ``night_enabled`` balanced has no quiet limit, quiet still has."""
    options = make_options(night_enabled=False)
    assert not quiet_limit_applies(Profile.BALANCED, options, NIGHT)
    assert quiet_limit_applies(Profile.QUIET, options, T0)


def _away(**overrides: object) -> Sim:
    options = make_options(presence_entities=[PRESENCE], **overrides)
    return Sim(options, values={**_co2(1300), PRESENCE: "not_home"})


def test_e12_bhv15_away_ignores_co2() -> None:
    """E12: away, CO₂ 1300 -> 80, reason away."""
    decision, _ = _away().run()
    assert decision.target == 80
    assert decision.reason is Reason.AWAY
    assert decision.limits == (Limit.AWAY,)
    assert decision.inputs.presence_home is False


def test_bhv15_away_keeps_humidity_high() -> None:
    """Away: humidity-high still applies and names the reason."""
    sim = _away()
    sim.set(HUM_A, 62)
    decision, _ = sim.run()
    assert decision.target == 230
    assert decision.reason is Reason.HUMIDITY_HIGH


def test_bhv15_away_disabled_or_home() -> None:
    """``away_enabled`` false or someone home: no away limit."""
    decision, _ = _away(away_enabled=False).run()
    assert decision.target == 340
    sim = _away()
    sim.set(PRESENCE, "home")
    decision, _ = sim.run()
    assert decision.target == 340


def test_e48_bhv19_fallback_wins_over_away() -> None:
    """E48: away and all CO₂ invalid -> 150, reason fallback."""
    sim = _away()
    sim.set(CO2_A, "unavailable")
    sim.set(CO2_B, "unknown")
    decision, _ = sim.run()
    assert decision.target == 150
    assert decision.reason is Reason.FALLBACK
    assert Limit.AWAY in decision.limits


def test_e53_bhv14_quiet_limit_caps_fallback() -> None:
    """E53: night, all CO₂ invalid, quiet_max_flow 120 < fallback 150 -> 120."""
    sim = Sim(make_options(quiet_max_flow=120), now=NIGHT, values=_co2("unavailable"))
    decision, _ = sim.run()
    assert decision.mode is Mode.FALLBACK
    assert decision.target == 120
    assert decision.reason is Reason.QUIET_LIMIT


def test_e43_bhv26_air_quality_has_no_quiet_limit() -> None:
    """E43: profile air_quality, night 23:00, demand 350 -> 350."""
    sim = Sim(
        now=NIGHT,
        values=_co2(co2_for(350)),
        state=running_state(profile=Profile.AIR_QUALITY),
    )
    decision, _ = sim.run()
    assert decision.target == 350
    assert decision.reason is Reason.CO2
    assert decision.limits == ()


@pytest.mark.parametrize(
    ("profile", "co2", "expected", "reason"),
    [
        (Profile.WINTER, 1300, 285, Reason.CO2),
        (Profile.WINTER, 1000, 80, Reason.DRYNESS_PROTECTION),
        (Profile.BALANCED, 1300, 340, Reason.CO2),
        (Profile.BALANCED, 1000, 165, Reason.CO2),
    ],
)
def test_e45_bhv26_winter_co2_ramp_from_midpoint_while_dry(
    profile: Profile, co2: float, expected: float, reason: Reason
) -> None:
    """E45: winter and dry: CO₂ ramp starts at its midpoint (1125 ppm)."""
    sim = Sim(
        values={**_co2(co2), HUM_A: 35, HUM_B: 35},
        state=running_state(profile=profile),
    )
    decision, _ = sim.run()
    assert Limit.DRY in decision.limits
    assert decision.target == expected
    assert decision.reason is reason


def test_e46_bhv26_air_quality_has_no_away_limit() -> None:
    """E46: profile air_quality, away, CO₂ 1300 -> 340."""
    sim = _away()
    sim.state = running_state(profile=Profile.AIR_QUALITY)
    decision, _ = sim.run()
    assert decision.target == 340
    assert decision.reason is Reason.CO2
    assert Limit.AWAY not in decision.limits


def test_bhv11_no_inputs_is_minimum() -> None:
    """No valid inputs and no CO₂ sensors configured -> minimum flow."""
    sim = Sim(make_options(co2_sensors=[]), values={HUM_A: None, HUM_B: "unknown"})
    decision, _ = sim.run()
    assert decision.target == 80
    assert decision.reason is Reason.MINIMUM
    assert decision.mode is Mode.AUTOMATIC


def test_quiet_limit_listed_without_changing_the_value() -> None:
    """``limits`` lists the quiet limit also when the demand is below it."""
    decision, _ = Sim(now=NIGHT).run()
    assert decision.limits == (Limit.QUIET,)
    assert decision.target == 80


def test_ramp_edges() -> None:
    """``ramp`` is flat outside its range and linear inside."""
    assert ramp(0, 10, 20, 1, 2) == 1
    assert ramp(30, 10, 20, 1, 2) == 2
    assert ramp(15, 10, 20, 1, 2) == 1.5


def test_dryness_counter_holds_at_exact_start() -> None:
    """At exactly humidity_high_start the counter neither starts nor resets."""
    options = make_options()
    since = T0 - timedelta(minutes=5)
    held = update_dryness(
        active=True,
        humidity_high_since=since,
        h_mean=38,
        h_max=55,
        options=options,
        now=T0,
    )
    assert held.humidity_high_since == since
    not_started = update_dryness(
        active=True,
        humidity_high_since=None,
        h_mean=38,
        h_max=55,
        options=options,
        now=T0,
    )
    assert not_started.humidity_high_since is None
