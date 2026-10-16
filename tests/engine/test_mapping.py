"""Flow <-> native mapping (BHV-01, docs/spec/02-control.md#actuator-mapping)."""

from __future__ import annotations

import pytest

from engine.mapping import (
    adjacent_stage,
    current_native,
    flow_range,
    flow_to_native,
    native_to_flow,
    off_native,
    same_native,
    select_stage,
)
from engine.models import ActuatorSnapshot, Mode, OffMethod, PauseCommand, Source

from .helpers import CO2_A, CO2_B, FAN, SELECT, Sim, co2_for, make_options, writes

STAGES = {
    "Stage 1": 50,
    "Stage 2": 100,
    "Stage 3": 200,
    "Stage 4": 300,
    "Stage 5": 400,
}


def test_e41_bhv01_select_stage_hysteresis() -> None:
    """E41: target 230 -> stage 300; decreasing to 190 stays until ≤ 180."""
    options = make_options(actuator=SELECT, select_mapping=STAGES)
    assert select_stage(230, options, None) == "Stage 4"
    assert select_stage(190, options, 300) == "Stage 4"
    assert select_stage(181, options, 300) == "Stage 4"
    assert select_stage(180, options, 300) == "Stage 3"
    assert select_stage(500, options, None) == "Stage 5"
    assert native_to_flow(options, "Stage 4") == 300
    assert native_to_flow(options, "Unmapped") is None


def test_e41a_bhv01_select_reaches_lowest_stage() -> None:
    """E41a: decreasing to the lowest stage flow (or below) selects the lowest stage."""
    options = make_options(actuator=SELECT, select_mapping=STAGES)
    assert select_stage(50, options, 100) == "Stage 1"
    assert select_stage(40, options, 200) == "Stage 1"
    assert select_stage(51, options, 100) == "Stage 2"


def test_e41_select_hysteresis_through_shaping() -> None:
    """E41 end to end: with step limits the unit leaves stage 300 at target 180."""
    options = make_options(actuator=SELECT, select_mapping=STAGES)
    sim = Sim(options, actuator="Stage 4")
    for flow in (190, 185):  # quantised targets (181 would become 180)
        sim.set(CO2_A, co2_for(flow))
        sim.set(CO2_B, co2_for(flow))
        decision, effects = sim.run()
        assert not writes(effects)
        assert decision.setpoint == 300
        sim.advance(minutes=10)
    sim.set(CO2_A, co2_for(180))
    sim.set(CO2_B, co2_for(180))
    decision, effects = sim.run()
    assert [effect.native for effect in writes(effects)] == ["Stage 3"]
    assert decision.setpoint == 200


def test_select_increase_and_adjacent_stage() -> None:
    """Going up picks the lowest stage ≥ flow; adjacent stage moves one step."""
    options = make_options(actuator=SELECT, select_mapping=STAGES)
    sim = Sim(options, actuator="Stage 3")
    sim.set(CO2_A, co2_for(230))
    sim.set(CO2_B, co2_for(230))
    _, effects = sim.run()
    assert [effect.native for effect in writes(effects)] == ["Stage 4"]
    assert adjacent_stage(options, "Stage 2", "Stage 5") == "Stage 3"
    assert adjacent_stage(options, "Stage 2", "Stage 2") == "Stage 2"
    assert adjacent_stage(options, "Unknown", "Stage 2") == "Stage 2"
    assert flow_range(options, ActuatorSnapshot(state="Stage 1")) == (50, 400)
    assert off_native(options, ActuatorSnapshot(state="Stage 1")) == "Stage 1"


@pytest.mark.parametrize(("step", "expected"), [(1.0, 38.0), (0.5, 37.5), (None, 38.0)])
def test_e42_bhv01_percent_number(step: float | None, expected: float) -> None:
    """E42: percent actuator, max 400, flow 150 -> 37.5 % rounded to the step."""
    options = make_options(actuator_unit="percent")
    actuator = ActuatorSnapshot(state="20", step=step, min_value=0, max_value=100)
    assert flow_to_native(150, options, actuator) == expected
    assert native_to_flow(options, 38.0) == 152
    assert flow_range(options, actuator) == (0, 400)


def test_number_flow_rounding_and_clamp() -> None:
    """Flow numbers round to ``step`` and clamp; options win over attributes."""
    options = make_options(actuator_min=60, actuator_max=350)
    actuator = ActuatorSnapshot(state="80", step=10, min_value=0, max_value=400)
    assert flow_to_native(84, options, actuator) == 80
    assert flow_to_native(85, options, actuator) == 90
    assert flow_to_native(20, options, actuator) == 60
    assert flow_to_native(400, options, actuator) == 350
    assert flow_range(options, actuator) == (60, 350)
    assert off_native(options, actuator) == 60
    plain = make_options()
    assert flow_range(plain, ActuatorSnapshot(state="80")) == (0, 400)
    assert off_native(plain, ActuatorSnapshot(state="80")) == 0


def test_fan_mapping() -> None:
    """Fan: percentage step, at least one step above 0; off -> 0."""
    options = make_options(actuator=FAN)
    actuator = ActuatorSnapshot(state="on", percentage=50, step=10)
    assert flow_to_native(150, options, actuator) == 40
    assert flow_to_native(0, options, actuator) == 10
    assert flow_to_native(500, options, actuator) == 100
    assert current_native(options, actuator) == 50
    assert native_to_flow(options, 50.0) == 200
    assert current_native(options, ActuatorSnapshot(state="off")) == 0
    assert current_native(options, ActuatorSnapshot(state="weird")) is None
    assert current_native(options, ActuatorSnapshot(state="unavailable")) is None
    assert off_native(options, actuator) == 0
    assert flow_range(options, actuator) == (0, 400)


def test_same_native() -> None:
    """Floats compare with a tolerance, strings exactly, None never matches."""
    assert same_native(80.0, 80.0000001)
    assert not same_native(80.0, 81.0)
    assert same_native("Stage 1", "Stage 1")
    assert not same_native("Stage 1", 50.0)
    assert not same_native(None, 80.0)


def test_off_without_off_actuator_writes_lowest_value() -> None:
    """Off mode without off actuator: lowest value, ``off_method`` minimum."""
    sim = Sim(make_options(actuator_min=40), actuator="120")
    sim.command(PauseCommand(source=Source.BUTTON))
    decision, effects = sim.run()
    assert decision.mode is Mode.OFF
    assert decision.setpoint == 0
    assert decision.off_method is OffMethod.MINIMUM
    assert [(e.native, e.bypass) for e in writes(effects)] == [(40, True)]
    decision, effects = sim.run()
    assert not writes(effects)


def test_select_unmapped_current_uses_last_written() -> None:
    """An unmapped current option has no flow; the last written value is used."""
    options = make_options(actuator=SELECT, select_mapping=STAGES)
    sim = Sim(options, actuator="Manual", values={CO2_B: 700})
    decision, effects = sim.run()
    assert decision.actuator_value == "Manual"
    assert [e.native for e in writes(effects)] == ["Stage 2"]
