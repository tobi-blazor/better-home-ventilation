"""Actuator adapters: service selection, snapshots, tolerance (BHV-01, BHV-04)."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import logging

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import Context, HomeAssistant, State
import pytest

from custom_components.better_home_ventilation.actuator import (
    CONFIRM_WINDOW,
    MIN_TOLERANCE,
    ActuatorWriter,
    _correction_service,
    _off_service,
    actuator_snapshot,
    off_actuator_snapshot,
    verify_tolerance,
)
from custom_components.better_home_ventilation.engine.models import (
    CorrectReadiness,
    Options,
    WriteFlow,
)

from .common import (
    FAN,
    NUMBER_FLOW,
    NUMBER_PERCENT,
    STANDBY_SWITCH,
    MockUnit,
    entry_options,
)


def options(**overrides: object) -> Options:
    """Return engine options of a 400 m³/h unit."""
    return Options.from_mapping(entry_options(**overrides))


@pytest.mark.parametrize(
    ("off_entity", "off_state", "stop", "start"),
    [
        ("switch.unit_standby", "on", "turn_on", "turn_off"),
        ("input_boolean.unit_power", "off", "turn_off", "turn_on"),
        ("fan.unit", "on", "turn_off", "turn_on"),
    ],
)
def test_off_actuator_services(
    off_entity: str, off_state: str, stop: str, start: str
) -> None:
    """`off_state` decides which service stops the unit; a fan is turned off."""
    opts = options(off_entity=off_entity, off_state=off_state)
    domain = off_entity.partition(".")[0]
    assert _off_service(opts, off_entity, stop=True) == (domain, stop)
    assert _off_service(opts, off_entity, stop=False) == (domain, start)


@pytest.mark.parametrize(
    ("entity_id", "to_state", "expected"),
    [
        (
            "select.unit_mode",
            "Remote",
            ("select", "select_option", {"option": "Remote"}),
        ),
        (
            "input_select.unit_mode",
            "Remote",
            ("input_select", "select_option", {"option": "Remote"}),
        ),
        ("switch.unit_enable", "on", ("switch", "turn_on", {})),
        ("input_boolean.unit_enable", "off", ("input_boolean", "turn_off", {})),
        ("number.unit_register", "2", ("number", "set_value", {"value": 2.0})),
    ],
)
def test_bhv04_correction_services(
    entity_id: str, to_state: str, expected: tuple[str, str, dict[str, object]]
) -> None:
    """Each correctable domain gets its own service call."""
    domain, service, data = expected
    effect = CorrectReadiness(entity_id=entity_id, to_state=to_state, from_state="x")
    assert _correction_service(effect) == (
        domain,
        service,
        {"entity_id": entity_id, **data},
    )


async def test_correction_with_invalid_number_is_skipped(
    hass: HomeAssistant, unit: MockUnit, caplog: pytest.LogCaptureFixture
) -> None:
    """A non-numeric expected state of a `number` entity is not sent."""
    writer = ActuatorWriter(hass, options(), asyncio.Lock(), lambda: None)
    effect = CorrectReadiness(
        entity_id="number.unit_register", to_state="fast", from_state="1"
    )
    caplog.set_level(logging.WARNING)

    assert not await writer.async_correct(effect)
    assert unit.calls == []
    assert "not a number" in caplog.text


def test_snapshots_from_states() -> None:
    """Snapshots read the attributes the mapping needs; odd values are ignored."""
    number = actuator_snapshot(
        options(),
        State(NUMBER_FLOW, "100", {"min": 10, "max": 400, "step": float("nan")}),
        foreign_change=True,
    )
    assert (number.state, number.min_value, number.max_value, number.step) == (
        "100",
        10.0,
        400.0,
        None,
    )
    assert number.foreign_change

    fan = actuator_snapshot(
        options(actuator=FAN),
        State(FAN, "on", {"percentage": 40, "percentage_step": True}),
    )
    assert (fan.percentage, fan.step) == (40.0, None)

    assert actuator_snapshot(options(), None).state is None
    assert off_actuator_snapshot(None).state is None
    assert off_actuator_snapshot(State("switch.unit_standby", "on")).state == "on"


@pytest.mark.parametrize(
    ("overrides", "step", "tolerance"),
    [
        ({}, 1.0, MIN_TOLERANCE),
        ({}, 10.0, 10.0),
        ({"actuator": NUMBER_PERCENT, "actuator_unit": "percent"}, 5.0, 20.0),
        ({"actuator": FAN}, 25.0, 100.0),
    ],
)
def test_verify_tolerance(
    overrides: dict[str, object], step: float, tolerance: float
) -> None:
    """Tolerance: one actuator step in flow, at least 5 m³/h."""
    opts = options(**overrides)
    snapshot = actuator_snapshot(
        opts,
        State(opts.actuator, "50", {"step": step, "percentage_step": step}),
    )
    assert verify_tolerance(opts, snapshot) == tolerance


async def test_own_context(hass: HomeAssistant, unit: MockUnit) -> None:
    """Only contexts created by the writer count as own."""
    hass.states.async_set("switch.unit_standby", "off")
    writer = ActuatorWriter(hass, options(), asyncio.Lock(), lambda: None)

    assert await writer.async_turn_on("switch.unit_standby")
    [call] = unit.calls
    assert writer.is_own(call.context)
    assert not writer.is_own(Context())
    assert not writer.is_own(None)


# --- Late confirmation (BHV-02, BHV-21) ------------------------------------------


def standby_writer(hass: HomeAssistant) -> ActuatorWriter:
    """Return a writer with a standby switch as off actuator (`on` = stopped)."""
    return ActuatorWriter(
        hass,
        options(off_entity=STANDBY_SWITCH, off_state="on"),
        asyncio.Lock(),
        lambda: None,
    )


def report(hass: HomeAssistant, value: str) -> State:
    """Set the standby switch with a new (foreign) context, like a poll."""
    hass.states.async_set(STANDBY_SWITCH, value, context=Context())
    state = hass.states.get(STANDBY_SWITCH)
    assert state is not None
    return state


async def test_late_confirmation_inside_the_window_is_own_once(
    hass: HomeAssistant, unit: MockUnit, freezer: FrozenDateTimeFactory
) -> None:
    """The commanded state with a new context inside the window is own, once."""
    unit.ignore.add(STANDBY_SWITCH)
    hass.states.async_set(STANDBY_SWITCH, "off")
    writer = standby_writer(hass)
    assert await writer.async_turn_off(STANDBY_SWITCH)  # stop: switch on

    freezer.tick(CONFIRM_WINDOW - timedelta(seconds=1))
    unavailable = report(hass, "unavailable")
    writer.observe_change(unavailable)
    assert not writer.is_own(unavailable.context)
    confirmed = report(hass, "on")
    writer.observe_change(confirmed)
    assert writer.is_own(confirmed.context)

    # The expectation is used up: the same state again is someone else's.
    for value in ("off", "on"):
        state = report(hass, value)
        writer.observe_change(state)
        assert not writer.is_own(state.context)


async def test_late_confirmation_after_the_window_is_foreign(
    hass: HomeAssistant, unit: MockUnit, freezer: FrozenDateTimeFactory
) -> None:
    """After the window the commanded state is an external change."""
    unit.ignore.add(STANDBY_SWITCH)
    hass.states.async_set(STANDBY_SWITCH, "on")
    writer = standby_writer(hass)
    assert await writer.async_turn_on(STANDBY_SWITCH)  # start: switch off

    freezer.tick(CONFIRM_WINDOW + timedelta(seconds=1))
    state = report(hass, "off")
    writer.observe_change(state)
    assert not writer.is_own(state.context)


async def test_own_context_ends_the_expectation(
    hass: HomeAssistant, unit: MockUnit
) -> None:
    """A confirmation with the own context ends the expectation as well."""
    hass.states.async_set(STANDBY_SWITCH, "off")
    writer = standby_writer(hass)
    assert await writer.async_turn_off(STANDBY_SWITCH)  # MockUnit echoes the context
    own = hass.states.get(STANDBY_SWITCH)
    assert own is not None
    writer.observe_change(own)
    assert writer.is_own(own.context)

    for value in ("off", "on"):
        state = report(hass, value)
        writer.observe_change(state)
        assert not writer.is_own(state.context)


async def test_no_expectation_for_a_state_already_shown(
    hass: HomeAssistant, unit: MockUnit
) -> None:
    """A command for a state the entity already shows expects nothing."""
    unit.ignore.add(STANDBY_SWITCH)
    hass.states.async_set(STANDBY_SWITCH, "on")
    writer = standby_writer(hass)
    assert await writer.async_turn_off(STANDBY_SWITCH)

    for value in ("off", "on"):
        state = report(hass, value)
        writer.observe_change(state)
        assert not writer.is_own(state.context)


async def test_newer_command_replaces_the_expectation(
    hass: HomeAssistant, unit: MockUnit
) -> None:
    """Only the value of the last command counts as its confirmation."""
    unit.ignore.add(NUMBER_FLOW)
    hass.states.async_set(NUMBER_FLOW, "100", {"step": 1})
    writer = ActuatorWriter(hass, options(), asyncio.Lock(), lambda: None)
    assert await writer.async_write(WriteFlow(flow=80, native=80.0))
    assert await writer.async_write(WriteFlow(flow=200, native=200.0))
    writer.async_cancel()

    for value, own in (("80", False), ("200", True)):
        hass.states.async_set(NUMBER_FLOW, value, {"step": 1}, context=Context())
        state = hass.states.get(NUMBER_FLOW)
        assert state is not None
        writer.observe_change(state)
        assert writer.is_own(state.context) is own


async def test_late_write_confirmation_uses_the_tolerance(
    hass: HomeAssistant, unit: MockUnit
) -> None:
    """A write is confirmed by its value within the verification tolerance."""
    unit.ignore.add(NUMBER_FLOW)
    hass.states.async_set(NUMBER_FLOW, "100", {"step": 1})
    writer = ActuatorWriter(hass, options(), asyncio.Lock(), lambda: None)
    assert await writer.async_write(WriteFlow(flow=80, native=80.0))
    writer.async_cancel()

    for value, own in (("120", False), ("unknown", False), ("83", True)):
        hass.states.async_set(NUMBER_FLOW, value, {"step": 1}, context=Context())
        state = hass.states.get(NUMBER_FLOW)
        assert state is not None
        writer.observe_change(state)
        assert writer.is_own(state.context) is own


async def test_awaiting_confirmation_until_the_actuator_changes(
    hass: HomeAssistant, unit: MockUnit, freezer: FrozenDateTimeFactory
) -> None:
    """A write awaits its confirmation until the actuator changes or the window ends.

    02-control.md#output-shaping: meanwhile the written value counts as
    `current`; an attribute-only update does not end it, any new state does.
    """
    unit.ignore.add(NUMBER_FLOW)
    hass.states.async_set(NUMBER_FLOW, "190", {"step": 1})
    writer = ActuatorWriter(hass, options(), asyncio.Lock(), lambda: None)
    assert not writer.awaiting_confirmation(hass.states.get(NUMBER_FLOW))
    assert await writer.async_write(WriteFlow(flow=240, native=240.0))
    writer.async_cancel()
    assert writer.awaiting_confirmation(hass.states.get(NUMBER_FLOW))

    freezer.tick(timedelta(seconds=1))
    hass.states.async_set(NUMBER_FLOW, "190", {"step": 1, "max": 400})
    assert writer.awaiting_confirmation(hass.states.get(NUMBER_FLOW))

    freezer.tick(CONFIRM_WINDOW)
    assert not writer.awaiting_confirmation(hass.states.get(NUMBER_FLOW))

    assert await writer.async_write(WriteFlow(flow=240, native=240.0))
    writer.async_cancel()
    freezer.tick(timedelta(seconds=1))
    hass.states.async_set(NUMBER_FLOW, "300", {"step": 1}, context=Context())
    assert not writer.awaiting_confirmation(hass.states.get(NUMBER_FLOW))
    assert not writer.awaiting_confirmation(None)
