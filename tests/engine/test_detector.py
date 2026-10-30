"""Automatic boosts: shower and cooking detection (BHV-30, BHV-31, BHV-32).

docs/spec/03-modes.md#automatic-boosts, scenarios E33-E37 of 06-testing.md.
A 400 m³/h unit: shower and cooking level 250, quiet cap 200, cooldown 10 min.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from engine import apply_command
from engine.detector import detector_key, trigger_active, update_detectors
from engine.models import (
    AutoBoost,
    AutoBoostKind,
    BoostCommand,
    ControlSwitchCommand,
    DetectorRuntime,
    FireEvent,
    InputValue,
    Mode,
    PauseCommand,
    Profile,
    ProfileCommand,
    Reason,
    ResumeCommand,
    ScheduleAt,
    Source,
)
from engine.serialize import state_from_dict, state_to_dict

from .helpers import CO2_A, T0, Sim, co2_for, make_options, of_type, running_state

BATH = "sensor.bathroom_humidity"
BATH_2 = "sensor.guest_bathroom_humidity"
KITCHEN = "sensor.kitchen_voc"
KITCHEN_IAQ = "sensor.kitchen_air_quality_index"
HOB = "sensor.hob_power"
HOOD = "binary_sensor.kitchen_hood_running"

SHOWER_KEY = detector_key(AutoBoostKind.SHOWER, BATH)
NIGHT = datetime(2026, 1, 15, 23, 0, tzinfo=UTC)


def _sim(*, now: datetime = T0, **overrides: Any) -> Sim:
    """Return a simulator with the bathroom at 55 % and the kitchen sensor at 100."""
    options = make_options(
        bathroom_humidity_sensors=[BATH],
        cooking_sensors=[KITCHEN],
        **overrides,
    )
    return Sim(options, now=now, values={BATH: 55, KITCHEN: 100})


def _step(sim: Sim, minutes: float, **values: float | str | None) -> Any:
    """Advance, set the given entity values (by attribute name) and evaluate."""
    sim.advance(minutes=minutes)
    names = {"bath": BATH, "bath_2": BATH_2, "kitchen": KITCHEN, "hob": HOB}
    names |= {"iaq": KITCHEN_IAQ, "hood": HOOD}
    for name, value in values.items():
        sim.set(names[name], value)
    return sim.run()


def _shower(sim: Sim) -> None:
    """E33 rise: 55 -> 58 -> 64 % within 6 minutes."""
    sim.run()
    _step(sim, 3, bath=58)
    _step(sim, 3, bath=64)


def _event(effects: list[Any]) -> dict[str, Any]:
    (event,) = of_type(effects, FireEvent)
    return dict(event.data)


# --- E33 shower ---------------------------------------------------------------


def test_e33_shower_starts_at_rise_and_ends_near_baseline() -> None:
    """E33: 55 -> 64 % within 6 min starts the shower boost, baseline 55, ends at ≤ 60 %."""
    sim = _sim()
    sim.run()
    decision, _ = _step(sim, 3, bath=58)
    assert decision.mode is Mode.AUTOMATIC  # rise 3 < 8

    decision, effects = _step(sim, 3, bath=64)
    assert decision.mode is Mode.SHOWER
    assert decision.reason is Reason.SHOWER
    assert decision.written == 250  # bypass: one step
    assert decision.contributions.shower == 250
    assert decision.contributions.cooking is None
    assert decision.auto_boosts == (
        AutoBoost(
            kind=AutoBoostKind.SHOWER,
            entity_id=BATH,
            baseline=55,
            started=sim.now,
            deadline=sim.now + timedelta(hours=1),
        ),
    )
    assert decision.ends_at == sim.now + timedelta(hours=1)
    event = _event(effects)
    assert event["to_mode"] == "shower"
    assert event["trigger"] == "detector_start"
    assert event["ends_at"] == (sim.now + timedelta(hours=1)).isoformat()
    assert ScheduleAt(when=sim.now + timedelta(hours=1)) in effects

    decision, _ = _step(sim, 5, bath=61)
    assert decision.mode is Mode.SHOWER  # 61 > 55 + 5

    decision, effects = _step(sim, 5, bath=60)
    assert decision.mode is Mode.AUTOMATIC
    assert decision.written == 80  # mode change: one step down
    assert decision.auto_boosts == ()
    assert _event(effects)["trigger"] == "detector_end"
    assert sim.state.detector_cooldown == {SHOWER_KEY: sim.now + timedelta(minutes=10)}
    assert sim.state.detectors[SHOWER_KEY].samples == ()


def test_e33_cooldown_blocks_a_new_start_for_10_minutes() -> None:
    """E33: after the end the detector waits for the cooldown."""
    sim = _sim()
    _shower(sim)
    _step(sim, 5, bath=59)  # end
    end = sim.now
    _step(sim, 1, bath=59)
    decision, _ = _step(sim, 3, bath=69)  # rise 10, but cooling down
    assert decision.mode is Mode.AUTOMATIC
    sim.advance(minutes=6)
    decision, effects = sim.run()  # end + 10 min: cooldown over
    assert sim.now == end + timedelta(minutes=10)
    assert decision.mode is Mode.SHOWER
    assert sim.state.auto_boosts[SHOWER_KEY].baseline == 59
    assert sim.state.detector_cooldown == {}
    assert _event(effects)["trigger"] == "detector_start"


def test_rise_outside_the_window_does_not_start() -> None:
    """Samples older than the window are dropped: a slow rise is no shower."""
    sim = _sim()
    sim.run()
    for value in (57, 59, 61, 63, 65):
        decision, _ = _step(sim, 6, bath=value)
        assert decision.mode is Mode.AUTOMATIC
    assert all(
        time >= sim.now - timedelta(minutes=10)
        for time, _ in sim.state.detectors[SHOWER_KEY].samples
    )


def test_shower_ends_at_max_duration_with_timeout() -> None:
    """A shower that never dries ends after `shower_max_duration` (timeout)."""
    sim = _sim()
    _shower(sim)
    started = sim.now
    decision, _ = _step(sim, 59, bath=75)
    assert decision.mode is Mode.SHOWER
    decision, effects = _step(sim, 1)
    assert sim.now == started + timedelta(hours=1)
    assert decision.mode is Mode.AUTOMATIC
    assert _event(effects)["trigger"] == "timeout"
    # Still humid after the cooldown: the cleared samples need a new rise.
    decision, _ = _step(sim, 11)
    decision, _ = _step(sim, 1)
    assert decision.mode is Mode.AUTOMATIC


# --- E34 night cap --------------------------------------------------------------


@pytest.mark.parametrize(
    ("profile", "now", "expected"),
    [
        (Profile.BALANCED, NIGHT, 200),
        (Profile.WINTER, NIGHT, 200),
        (Profile.BALANCED, T0, 250),
        (Profile.QUIET, T0, 200),
        (Profile.AIR_QUALITY, NIGHT, 250),
    ],
)
def test_e34_auto_boost_cap_per_profile(
    profile: Profile, now: datetime, expected: float
) -> None:
    """E34/BHV-32: the cap applies where the profile applies the quiet limit."""
    sim = _sim(now=now)
    sim.state = running_state(profile=profile)
    _shower(sim)
    decision = sim.decision
    assert decision is not None
    assert decision.mode is Mode.SHOWER
    assert decision.setpoint == expected
    assert decision.contributions.shower == expected
    assert decision.reason is Reason.SHOWER


def test_e34_user_boost_is_not_capped_at_night() -> None:
    """User boosts are not capped by the quiet limit, auto boosts are (BHV-32)."""
    sim = _sim(now=NIGHT)
    _shower(sim)
    assert sim.decision is not None
    assert sim.decision.setpoint == 200
    sim.command(BoostCommand(source=Source.BUTTON))
    decision, _ = _step(sim, 0)
    assert decision.mode is Mode.BOOST
    assert decision.setpoint == 300
    assert decision.contributions.shower == 200  # still active behind the boost


def test_quiet_profile_change_during_shower_caps_the_level() -> None:
    """A profile change takes effect in the same evaluation."""
    sim = _sim()
    _shower(sim)
    sim.command(ProfileCommand(source=Source.BUTTON, profile=Profile.QUIET))
    decision, _ = _step(sim, 1)
    assert decision.mode is Mode.SHOWER
    assert decision.written == 200


def test_higher_demand_wins_with_its_reason() -> None:
    """Target = max(level, demand): CO₂ above the shower level keeps its reason."""
    sim = _sim()
    sim.set(CO2_A, co2_for(300))
    _shower(sim)
    decision = sim.decision
    assert decision is not None
    assert decision.mode is Mode.SHOWER
    assert decision.target == 300
    assert decision.reason is Reason.CO2


def test_night_demand_above_the_cap_with_humidity_override() -> None:
    """Above the humidity override the quiet limit is off; the demand still counts."""
    sim = _sim(now=NIGHT)
    sim.set("sensor.living_room_humidity", 68)  # override 65, humidity-high 357
    _shower(sim)
    decision = sim.decision
    assert decision is not None
    assert decision.mode is Mode.SHOWER
    assert decision.reason is Reason.HUMIDITY_HIGH
    assert decision.target > 250


# --- E35 inputs lost --------------------------------------------------------------


def test_e35_unavailable_sensor_ends_the_shower_with_inputs_lost() -> None:
    """E35: the bathroom sensor unavailable for more than 30 min ends the shower."""
    sim = _sim()
    _shower(sim)
    decision, _ = _step(sim, 1, bath="unavailable")
    assert decision.mode is Mode.SHOWER
    assert BATH in decision.invalid_inputs
    lost = sim.now
    decision, _ = _step(sim, 30)
    assert decision.mode is Mode.SHOWER  # exactly stale_timeout: not longer
    decision, effects = _step(sim, 1)
    assert sim.now - lost > timedelta(minutes=30)
    assert decision.mode is Mode.AUTOMATIC
    assert _event(effects)["trigger"] == "inputs_lost"


def test_e35_a_valid_value_resets_the_invalid_time() -> None:
    """The invalid time counts again from the next invalid value."""
    sim = _sim()
    _shower(sim)
    _step(sim, 1, bath="unknown")
    _step(sim, 20, bath=70)
    _step(sim, 1, bath="unavailable")
    decision, _ = _step(sim, 25)
    assert decision.mode is Mode.SHOWER


def test_stale_bathroom_sensor_is_an_invalid_input() -> None:
    """Detector sensors follow the input validity rules (BHV-18)."""
    sim = _sim()
    sim.set_stale(BATH, T0 - timedelta(hours=1))
    decision, _ = sim.run()
    assert decision.invalid_inputs == (BATH,)
    assert sim.state.detectors[SHOWER_KEY].invalid_since == T0


# --- E36 cooking, lower is worse ------------------------------------------------------


def test_e36_cooking_detects_a_falling_value() -> None:
    """E36: `cooking_higher_is_worse` false detects a fall of the value."""
    options = make_options(cooking_sensors=[KITCHEN_IAQ], cooking_higher_is_worse=False)
    sim = Sim(options, values={KITCHEN_IAQ: 100})
    sim.run()
    decision, _ = _step(sim, 2, iaq=70)
    assert decision.mode is Mode.AUTOMATIC  # fall 30 < 50
    decision, effects = _step(sim, 2, iaq=40)
    assert decision.mode is Mode.COOKING
    assert decision.setpoint == 250
    assert sim.state.auto_boosts[detector_key("cooking", KITCHEN_IAQ)].baseline == 100
    assert _event(effects)["trigger"] == "detector_start"
    decision, _ = _step(sim, 5, iaq=84)
    assert decision.mode is Mode.COOKING  # 84 < 100 - 15
    decision, effects = _step(sim, 5, iaq=85)
    assert decision.mode is Mode.AUTOMATIC
    assert _event(effects)["trigger"] == "detector_end"


def test_cooking_rise_with_higher_is_worse() -> None:
    """The default cooking sensor: a VOC rise of 50 starts the cooking boost."""
    sim = _sim()
    sim.run()
    decision, _ = _step(sim, 4, kitchen=160)
    assert decision.mode is Mode.COOKING
    assert decision.contributions.cooking == 250
    decision, _ = _step(sim, 10, kitchen=115)
    assert decision.mode is Mode.AUTOMATIC  # 115 ≤ 100 + 15


# --- E37 cooking trigger ----------------------------------------------------------------


def test_e37_trigger_power_starts_cooking_until_the_run_on_ends() -> None:
    """E37: hob power 1500 W > 100 W; the boost runs on 15 min after it drops."""
    options = make_options(cooking_trigger=HOB)
    sim = Sim(options, values={HOB: 0})
    sim.run()
    decision, effects = _step(sim, 1, hob=1500)
    assert decision.mode is Mode.COOKING
    assert decision.setpoint == 250
    started = sim.now
    assert decision.ends_at == started + timedelta(minutes=90)
    assert decision.auto_boosts[0].baseline is None
    assert _event(effects)["trigger"] == "detector_start"

    decision, _ = _step(sim, 20, hob=40)
    dropped = sim.now
    assert decision.mode is Mode.COOKING
    assert decision.ends_at == dropped + timedelta(minutes=15)
    decision, _ = _step(sim, 14)
    assert decision.mode is Mode.COOKING
    decision, effects = _step(sim, 1)
    assert sim.now == dropped + timedelta(minutes=15)
    assert decision.mode is Mode.AUTOMATIC
    assert _event(effects)["trigger"] == "detector_end"


def test_trigger_active_again_during_the_run_on_continues() -> None:
    """The trigger back on within the run-on keeps the boost up to its maximum."""
    options = make_options(cooking_trigger=HOOD)
    sim = Sim(options, values={HOOD: "on"})
    decision, _ = sim.run()
    assert decision.mode is Mode.COOKING  # active at start: a start
    started = sim.now
    decision, _ = _step(sim, 10, hood="off")
    assert decision.ends_at == sim.now + timedelta(minutes=15)
    decision, _ = _step(sim, 5, hood="on")
    assert decision.ends_at == started + timedelta(minutes=90)
    decision, effects = _step(sim, 75)
    assert decision.mode is Mode.AUTOMATIC
    assert _event(effects)["trigger"] == "timeout"
    # Still on after the cooldown: it must turn off before it starts again.
    decision, _ = _step(sim, 30)
    assert decision.mode is Mode.AUTOMATIC
    _step(sim, 1, hood="off")
    decision, _ = _step(sim, 1, hood="on")
    assert decision.mode is Mode.COOKING


def test_trigger_run_on_zero_ends_at_once() -> None:
    """Without run-on the boost ends with the trigger."""
    options = make_options(cooking_trigger=HOOD, cooking_trigger_run_on=0)
    sim = Sim(options, values={HOOD: "on"})
    sim.run()
    decision, _ = _step(sim, 1, hood="unavailable")
    assert decision.mode is Mode.AUTOMATIC


@pytest.mark.parametrize(
    ("entity_id", "state", "expected"),
    [
        (HOOD, "on", True),
        (HOOD, "off", False),
        ("switch.hob", "on", True),
        ("light.hood_light", "ON", True),
        ("fan.kitchen_hood", "on", True),
        (HOB, "100", False),
        (HOB, "100.5", True),
        (HOB, "on", False),
        (HOB, "unavailable", False),
        (HOOD, None, False),
    ],
)
def test_trigger_active(entity_id: str, state: str | None, expected: bool) -> None:
    """Active: `on` for on/off domains, numeric above the threshold otherwise."""
    value = InputValue(state=state)
    assert trigger_active(entity_id, value, 100) is expected
    assert trigger_active(entity_id, None, 100) is False


# --- Both kinds, priority, commands -----------------------------------------------------


@pytest.mark.parametrize(
    ("shower_level", "cooking_level", "mode", "setpoint"),
    [
        (250, 300, Mode.COOKING, 300),
        (300, 250, Mode.SHOWER, 300),
        (250, 250, Mode.COOKING, 250),
    ],
)
def test_both_kinds_the_higher_level_wins(
    shower_level: float, cooking_level: float, mode: Mode, setpoint: float
) -> None:
    """Shower and cooking at once: the higher level wins, tie cooking."""
    sim = _sim(shower_level=shower_level, cooking_level=cooking_level)
    sim.run()
    _step(sim, 3, bath=58, kitchen=130)
    decision, _ = _step(sim, 3, bath=64, kitchen=170)
    assert decision.mode is mode
    assert decision.setpoint == setpoint
    assert decision.contributions.shower == shower_level
    assert decision.contributions.cooking == cooking_level
    assert {boost.kind for boost in decision.auto_boosts} == {"shower", "cooking"}


def test_end_of_the_winning_kind_falls_back_to_the_other() -> None:
    """Cooking ends while the shower is active: mode shower, trigger detector_end."""
    sim = _sim(cooking_level=300)
    sim.run()
    _step(sim, 3, bath=58, kitchen=130)
    decision, _ = _step(sim, 3, bath=64, kitchen=170)
    assert decision.mode is Mode.COOKING
    decision, effects = _step(sim, 2, kitchen=110)
    assert decision.mode is Mode.SHOWER
    assert _event(effects)["trigger"] == "detector_end"
    decision, effects = _step(sim, 2, kitchen=200)  # cooling down
    assert decision.mode is Mode.SHOWER
    assert of_type(effects, FireEvent) == []


def test_a_second_sensor_keeps_the_kind_active() -> None:
    """The shower boost is active while at least one of its detectors is."""
    options = make_options(bathroom_humidity_sensors=[BATH, BATH_2])
    sim = Sim(options, values={BATH: 55, BATH_2: 50})
    sim.run()
    _step(sim, 3, bath=64)
    decision, _ = _step(sim, 3, bath_2=60)
    assert len(decision.auto_boosts) == 2
    decision, effects = _step(sim, 3, bath=58)
    assert decision.mode is Mode.SHOWER
    assert of_type(effects, FireEvent) == []
    decision, effects = _step(sim, 3, bath_2=54)
    assert decision.mode is Mode.AUTOMATIC
    assert _event(effects)["trigger"] == "detector_end"


def test_resume_ends_auto_boosts_with_cooldown() -> None:
    """`resume()` clears the auto boosts; the detectors enter the cooldown."""
    sim = _sim()
    _shower(sim)
    sim.command(ResumeCommand(source=Source.BUTTON))
    assert sim.state.auto_boosts == {}
    assert sim.state.detector_cooldown == {SHOWER_KEY: sim.now + timedelta(minutes=10)}
    decision, effects = _step(sim, 0)
    assert decision.mode is Mode.AUTOMATIC
    event = _event(effects)
    assert event["trigger"] == "command"
    assert event["source"] == "button"
    decision, _ = _step(sim, 1, bath=75)
    assert decision.mode is Mode.AUTOMATIC  # cooldown


def test_resume_without_anything_to_end_does_nothing() -> None:
    """`resume()` while control is disabled keeps the shadow auto boost."""
    sim = _sim()
    _shower(sim)
    state = sim.state
    disabled = apply_command(
        state,
        ControlSwitchCommand(source=Source.BUTTON, enabled=False),
        sim.options,
        sim.now,
    )
    assert disabled.auto_boosts == {}
    assert disabled.detector_cooldown == {SHOWER_KEY: sim.now + timedelta(minutes=10)}
    again = apply_command(
        disabled, ResumeCommand(source=Source.BUTTON), sim.options, sim.now
    )
    assert again is disabled


def test_control_switch_off_ends_auto_boosts() -> None:
    """Switching control off ends the auto boosts (cooldown); detection continues."""
    sim = _sim()
    _shower(sim)
    sim.command(ControlSwitchCommand(source=Source.BUTTON, enabled=False))
    decision, effects = _step(sim, 0)
    assert decision.mode is Mode.DISABLED
    assert _event(effects)["trigger"] == "control_switch"
    _step(sim, 10, bath=60)
    decision, _ = _step(sim, 3, bath=70)
    assert decision.mode is Mode.DISABLED
    assert len(decision.auto_boosts) == 1  # shown, nothing written
    assert decision.written is None


def test_pause_wins_and_the_shower_continues_after_it() -> None:
    """A pause has priority; the auto boost stays active behind it."""
    sim = _sim()
    _shower(sim)
    sim.command(PauseCommand(source=Source.BUTTON, duration=timedelta(minutes=5)))
    decision, _ = _step(sim, 0)
    assert decision.mode is Mode.OFF
    assert len(decision.auto_boosts) == 1
    decision, effects = _step(sim, 5)
    assert decision.mode is Mode.SHOWER
    assert _event(effects)["trigger"] == "timeout"


def test_unconfigured_detector_drops_its_auto_boost() -> None:
    """Options changed: an auto boost of a sensor no longer configured is dropped."""
    sim = _sim()
    _shower(sim)
    sim.options = make_options()
    decision, _ = _step(sim, 1)
    assert decision.mode is Mode.AUTOMATIC
    assert sim.state.auto_boosts == {}
    assert sim.state.detectors == {}


def test_no_detectors_configured_keeps_the_state_empty() -> None:
    """Without bathroom or cooking sensors nothing is tracked."""
    sim = Sim()
    decision, _ = sim.run()
    assert decision.auto_boosts == ()
    assert sim.state.detectors == {}
    assert decision.contributions.shower is None


def test_update_detectors_keeps_unexpired_cooldowns_only() -> None:
    """Expired cooldowns are dropped."""
    options = make_options(bathroom_humidity_sensors=[BATH])
    state = running_state(
        detector_cooldown={
            SHOWER_KEY: T0 + timedelta(minutes=1),
            "shower:sensor.gone": T0 + timedelta(minutes=1),
        },
        detectors={SHOWER_KEY: DetectorRuntime()},
    )
    update = update_detectors(state, {}, {}, options, T0)
    assert update.state.detector_cooldown == {SHOWER_KEY: T0 + timedelta(minutes=1)}
    later = update_detectors(state, {}, {}, options, T0 + timedelta(minutes=1))
    assert later.state.detector_cooldown == {}


# --- Restart ---------------------------------------------------------------------------


def test_restart_with_an_active_shower_keeps_its_deadline() -> None:
    """The auto boost survives a restart; the samples refill (BHV-50)."""
    sim = _sim()
    _shower(sim)
    deadline = sim.state.auto_boosts[SHOWER_KEY].deadline
    restarted_at = sim.now + timedelta(minutes=5)
    loaded = state_from_dict(state_to_dict(sim.state), restarted_at)
    assert loaded.expired is None
    assert loaded.state.auto_boosts == sim.state.auto_boosts
    assert loaded.state.detector_cooldown == {}
    assert loaded.state.detectors == {}

    restarted = _sim(now=restarted_at)
    restarted.started_at = restarted_at
    restarted.state = loaded.state
    restarted.set(BATH, 70)
    decision, effects = restarted.run()
    assert decision.mode is Mode.SHOWER
    assert decision.ends_at == deadline
    assert of_type(effects, FireEvent) == []  # same mode as before the restart
    assert ScheduleAt(when=restarted_at + timedelta(minutes=1)) in effects  # grace


def test_restart_after_the_deadline_ends_with_timeout_while_offline() -> None:
    """An auto boost that expired while offline ends with `timeout_while_offline`."""
    sim = _sim()
    _shower(sim)
    later = sim.now + timedelta(hours=2)
    loaded = state_from_dict(state_to_dict(sim.state), later)
    assert loaded.expired == "auto boost"
    assert loaded.state.auto_boosts == {}
    restarted = _sim(now=later)
    restarted.state = loaded.state
    decision, effects = restarted.run()
    assert decision.mode is Mode.AUTOMATIC
    assert _event(effects)["trigger"] == "timeout_while_offline"
