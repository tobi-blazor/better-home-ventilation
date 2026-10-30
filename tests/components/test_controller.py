"""Controller runtime: startup, triggers, writes, readiness, commands (C4, C8).

Time is controlled with ``freezer`` and ``async_fire_time_changed``; the unit
is simulated by :class:`MockUnit` service handlers.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
from typing import Any

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import SOURCE_USER, ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
)

from custom_components.better_home_ventilation.const import (
    CONF_AUTO_CORRECT,
    CONF_ENTITY_ID,
    CONF_EXPECTED_STATE,
    DOMAIN,
    EVENT_MODE_CHANGED,
    EVENT_READINESS_CORRECTED,
    MANUFACTURER,
    MODEL,
    SUBENTRY_READINESS_CONDITION,
)
from custom_components.better_home_ventilation.controller import (
    VentilationController,
)
from custom_components.better_home_ventilation.engine.models import (
    Boost,
    BoostCommand,
    ControlSwitchCommand,
    Mode,
    Pause,
    PauseCommand,
    ProblemKey,
    ResumeCommand,
    Source,
    WriteBlock,
)

from .common import (
    CO2_LIVING,
    CONTROL_MODE,
    FAN,
    NUMBER_FLOW,
    SELECT_STAGES,
    STAGE_OPTIONS,
    STANDBY_SWITCH,
    MockUnit,
    advance,
    entry_options,
)

MODULE = "custom_components.better_home_ventilation"
CO2 = {"device_class": "carbon_dioxide", "unit_of_measurement": "ppm"}
NUMBER = {"unit_of_measurement": "m³/h", "min": 0, "max": 400, "step": 1}


@pytest.fixture(autouse=True)
def daytime(freezer: FrozenDateTimeFactory) -> None:
    """Run every test at 13:00 local time, outside the night window."""
    freezer.move_to("2026-10-14T20:00:00+00:00")  # US/Pacific in tests


def readiness(
    entity_id: str, expected: str, *, auto_correct: bool = True
) -> dict[str, Any]:
    """Return the data of a readiness condition subentry."""
    return {
        "data": {
            CONF_ENTITY_ID: entity_id,
            CONF_EXPECTED_STATE: expected,
            CONF_AUTO_CORRECT: auto_correct,
        },
        "subentry_type": SUBENTRY_READINESS_CONDITION,
        "title": f"{entity_id} = {expected}",
        "unique_id": entity_id,
    }


async def setup_entry(
    hass: HomeAssistant,
    *,
    subentries: list[dict[str, Any]] | None = None,
    **overrides: Any,
) -> MockConfigEntry:
    """Add and set up an entry with complete options."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Ventilation",
        data={},
        options=entry_options(**overrides),
        version=1,
        minor_version=1,
        subentries_data=subentries or [],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def controller_of(entry: ConfigEntry) -> VentilationController:
    """Return the controller of a loaded entry."""
    controller = entry.runtime_data
    assert isinstance(controller, VentilationController)
    return controller


def problem_keys(controller: VentilationController) -> set[tuple[str, str | None]]:
    """Return the current problems as (key, entity id) pairs."""
    decision = controller.current_decision
    assert decision is not None
    return {(str(problem.key), problem.entity_id) for problem in decision.problems}


# --- Startup, device, triggers -------------------------------------------------


@pytest.mark.usefixtures("entities")
async def test_device_is_created(hass: HomeAssistant, unit: MockUnit) -> None:
    """One service device per entry carries the events' device id (04-interface)."""
    entry = await setup_entry(hass)
    controller = controller_of(entry)

    assert controller.device_id is not None
    device = dr.async_get(hass).async_get(controller.device_id)
    assert isinstance(device, dr.DeviceEntry)
    assert device.identifiers == {(DOMAIN, entry.entry_id)}
    assert device.manufacturer == MANUFACTURER
    assert device.model == MODEL
    assert device.entry_type is dr.DeviceEntryType.SERVICE
    assert device.name == "Ventilation"
    assert device.sw_version == "0.1.0"


@pytest.mark.usefixtures("entities")
async def test_e39_startup_grace_then_first_write(
    hass: HomeAssistant, unit: MockUnit, freezer: FrozenDateTimeFactory
) -> None:
    """Nothing is written during the startup grace; then one write with our context."""
    entry = await setup_entry(hass)
    controller = controller_of(entry)
    decision = controller.current_decision
    assert decision is not None
    assert decision.write_blocked_by is WriteBlock.GRACE
    assert decision.written is None

    await advance(hass, freezer, 59)
    assert unit.calls == []

    await advance(hass, freezer, 1)
    [call] = unit.calls_for("number", "set_value")
    assert call.data == {"entity_id": NUMBER_FLOW, "value": 80.0}
    assert controller._writer.is_own(call.context)
    assert hass.states.get(NUMBER_FLOW).state == "80.0"  # type: ignore[union-attr]
    # The own state change is evaluated but holds (no second write).
    assert len(unit.calls) == 1


async def test_setup_without_entities_does_not_fail(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """Missing source entities are invalid inputs, not a setup error."""
    entry = await setup_entry(hass)
    controller = controller_of(entry)
    await advance(hass, freezer, 61)

    decision = controller.current_decision
    assert decision is not None
    assert decision.mode is Mode.FALLBACK
    assert decision.write_blocked_by is WriteBlock.UNAVAILABLE
    assert ("actuator_unavailable", NUMBER_FLOW) in problem_keys(controller)


@pytest.mark.usefixtures("entities")
async def test_bhv17_input_changes_are_debounced(
    hass: HomeAssistant, unit: MockUnit, freezer: FrozenDateTimeFactory
) -> None:
    """Input changes evaluate after `debounce`; later changes do not restart it."""
    entry = await setup_entry(hass)
    controller = controller_of(entry)
    await advance(hass, freezer, 61)
    count = len(controller.decisions)

    hass.states.async_set(CO2_LIVING, "1400", CO2)
    await hass.async_block_till_done()
    assert len(controller.decisions) == count
    await advance(hass, freezer, 5)
    hass.states.async_set(CO2_LIVING, "1300", CO2)
    await advance(hass, freezer, 4)
    assert len(controller.decisions) == count

    await advance(hass, freezer, 1)
    assert len(controller.decisions) == count + 1
    decision = controller.current_decision
    assert decision is not None
    assert decision.target == 340
    assert decision.write_blocked_by is WriteBlock.INTERVAL


@pytest.mark.usefixtures("entities")
async def test_bhv17_periodic_evaluation(
    hass: HomeAssistant, unit: MockUnit, freezer: FrozenDateTimeFactory
) -> None:
    """An evaluation runs every `evaluation_interval` without any change."""
    entry = await setup_entry(hass)
    controller = controller_of(entry)
    await advance(hass, freezer, 61)
    count = len(controller.decisions)

    await advance(hass, freezer, 60)
    assert len(controller.decisions) == count + 1
    await advance(hass, freezer, 60)
    assert len(controller.decisions) == count + 2


@pytest.mark.usefixtures("entities")
async def test_listeners_are_called_after_each_evaluation(
    hass: HomeAssistant, unit: MockUnit, freezer: FrozenDateTimeFactory
) -> None:
    """Entities register a callback and can unregister it."""
    entry = await setup_entry(hass)
    controller = controller_of(entry)
    calls: list[None] = []
    remove = controller.async_add_listener(lambda: calls.append(None))

    await advance(hass, freezer, 60)
    assert calls
    remove()
    remove()  # removing twice is harmless
    seen = len(calls)
    await advance(hass, freezer, 60)
    assert len(calls) == seen


@pytest.mark.usefixtures("entities")
async def test_evaluations_never_overlap(hass: HomeAssistant, unit: MockUnit) -> None:
    """Requests during a running evaluation collapse into exactly one follow-up."""
    entry = await setup_entry(hass)
    controller = controller_of(entry)
    count = len(controller.decisions)

    async with controller._lock:
        tasks = [
            hass.async_create_task(controller.async_request_evaluation())
            for _ in range(3)
        ]
        await asyncio.sleep(0)
    await asyncio.gather(*tasks)
    await hass.async_block_till_done()

    assert len(controller.decisions) == count + 1


@pytest.mark.usefixtures("entities")
async def test_decision_log_keeps_100(
    hass: HomeAssistant, unit: MockUnit, freezer: FrozenDateTimeFactory
) -> None:
    """The decision log keeps the last 100 decisions."""
    entry = await setup_entry(hass)
    controller = controller_of(entry)
    for _ in range(105):
        await controller.async_request_evaluation()
    assert len(controller.decisions) == 100
    assert controller.decisions[-1] is controller.current_decision


# --- Writing (C8) ----------------------------------------------------------------


@pytest.mark.usefixtures("entities")
async def test_c8_verification_retry_then_write_mismatch(
    hass: HomeAssistant, unit: MockUnit, freezer: FrozenDateTimeFactory
) -> None:
    """A value the unit does not take is retried once, then `write_mismatch`."""
    unit.ignore.add(NUMBER_FLOW)
    entry = await setup_entry(hass)
    controller = controller_of(entry)
    await advance(hass, freezer, 60)
    assert len(unit.calls_for("number", "set_value")) == 1

    await advance(hass, freezer, 30)
    retry = unit.calls_for("number", "set_value")
    assert len(retry) == 2
    assert retry[1].data["value"] == 80.0
    assert controller._writer.is_own(retry[1].context)
    assert ("write_mismatch", None) not in problem_keys(controller)

    await advance(hass, freezer, 30)
    assert ("write_mismatch", None) in problem_keys(controller)
    await advance(hass, freezer, 60)
    assert len(unit.calls_for("number", "set_value")) == 2  # no further retries

    # The unit finally shows the value: the problem clears.
    hass.states.async_set(NUMBER_FLOW, "80", NUMBER)
    await hass.async_block_till_done()
    assert ("write_mismatch", None) not in problem_keys(controller)


@pytest.mark.usefixtures("entities")
async def test_c8_verification_match_and_new_target(
    hass: HomeAssistant, unit: MockUnit, freezer: FrozenDateTimeFactory
) -> None:
    """A verified write raises nothing; a mismatch resets when the target changes."""
    entry = await setup_entry(hass)
    controller = controller_of(entry)
    await advance(hass, freezer, 90)
    assert len(unit.calls_for("number", "set_value")) == 1
    assert not controller._writer.problems

    unit.ignore.add(NUMBER_FLOW)
    await controller.async_command(BoostCommand(source=Source.SERVICE))
    await advance(hass, freezer, 30)
    await advance(hass, freezer, 30)
    assert ("write_mismatch", None) in problem_keys(controller)
    writes = len(unit.calls_for("number", "set_value"))

    # A new target starts a new verification cycle with one retry again.
    await controller.async_command(BoostCommand(source=Source.SERVICE, level=350))
    assert len(unit.calls_for("number", "set_value")) == writes + 1
    assert ProblemKey.WRITE_MISMATCH not in controller._writer.problems
    await advance(hass, freezer, 30)
    assert len(unit.calls_for("number", "set_value")) == writes + 2


@pytest.mark.usefixtures("entities")
async def test_write_failed_logs_once(
    hass: HomeAssistant,
    unit: MockUnit,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failing service call raises `write_failed` and warns once."""
    unit.fail.add(NUMBER_FLOW)
    entry = await setup_entry(hass)
    controller = controller_of(entry)
    caplog.set_level(logging.DEBUG)

    await advance(hass, freezer, 60)
    await advance(hass, freezer, 300)
    assert len(unit.calls_for("number", "set_value")) == 2
    assert ("write_failed", None) in problem_keys(controller)
    warnings = [
        record
        for record in caplog.records
        if record.levelno == logging.WARNING and record.name.startswith(MODULE)
    ]
    assert len(warnings) == 1
    assert "number.set_value" in warnings[0].getMessage()

    unit.fail.clear()
    await advance(hass, freezer, 300)
    await advance(hass, freezer, 60)
    assert ("write_failed", None) not in problem_keys(controller)
    assert "work again" in caplog.text


@pytest.mark.usefixtures("entities")
async def test_log_when_unavailable(
    hass: HomeAssistant,
    unit: MockUnit,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """One info message when the actuator becomes unavailable, one when back."""
    entry = await setup_entry(hass)
    controller = controller_of(entry)
    await advance(hass, freezer, 61)
    caplog.set_level(logging.INFO)

    hass.states.async_set(NUMBER_FLOW, "unavailable", NUMBER)
    await hass.async_block_till_done()
    await advance(hass, freezer, 60)
    await advance(hass, freezer, 60)
    assert caplog.text.count("is unavailable") == 1
    assert ("actuator_unavailable", NUMBER_FLOW) in problem_keys(controller)

    hass.states.async_set(NUMBER_FLOW, "80", NUMBER)
    await hass.async_block_till_done()
    assert caplog.text.count("is available again") == 1


@pytest.mark.usefixtures("entities")
async def test_fan_actuator_writes_percentage(
    hass: HomeAssistant, unit: MockUnit, freezer: FrozenDateTimeFactory
) -> None:
    """A `fan` actuator gets `fan.set_percentage` (BHV-01)."""
    hass.states.async_set(FAN, "on", {"percentage": 50, "percentage_step": 1})
    await setup_entry(hass, actuator=FAN)
    await advance(hass, freezer, 60)

    [call] = unit.calls_for("fan", "set_percentage")
    assert call.data == {"entity_id": FAN, "percentage": 20}
    await advance(hass, freezer, 30)  # verified, no retry
    assert len(unit.calls_for("fan", "set_percentage")) == 1


@pytest.mark.usefixtures("entities")
async def test_select_actuator_selects_stage(
    hass: HomeAssistant, unit: MockUnit, freezer: FrozenDateTimeFactory
) -> None:
    """A `select` actuator gets `select.select_option` with the mapped stage."""
    hass.states.async_set(SELECT_STAGES, "Stage 3", {"options": STAGE_OPTIONS})
    await setup_entry(
        hass,
        actuator=SELECT_STAGES,
        select_mapping={"Stage 1": 50, "Stage 2": 150, "Stage 3": 400},
    )
    await advance(hass, freezer, 60)

    # 80 m³/h decreasing: the stage for 80 + dead band down (20) is Stage 2.
    [call] = unit.calls_for("select", "select_option")
    assert call.data == {"entity_id": SELECT_STAGES, "option": "Stage 2"}
    await advance(hass, freezer, 30)
    assert len(unit.calls_for("select", "select_option")) == 1


# --- Off actuator ---------------------------------------------------------------------


@pytest.mark.usefixtures("entities")
async def test_e52_external_stop_becomes_pause(
    hass: HomeAssistant, unit: MockUnit, freezer: FrozenDateTimeFactory
) -> None:
    """A stop at the unit (foreign context) is an indefinite pause; a start resumes."""
    events = async_capture_events(hass, EVENT_MODE_CHANGED)
    entry = await setup_entry(hass, off_entity=STANDBY_SWITCH, off_state="on")
    controller = controller_of(entry)
    await advance(hass, freezer, 61)

    hass.states.async_set(STANDBY_SWITCH, "on")
    await hass.async_block_till_done()

    decision = controller.current_decision
    assert decision is not None
    assert decision.mode is Mode.OFF
    manual = controller.state.manual
    assert isinstance(manual, Pause)
    assert manual.source is Source.EXTERNAL
    assert manual.until is None
    assert unit.calls_for("switch", "turn_on") == []
    assert events[-1].data == {
        "from_mode": "automatic",
        "to_mode": "off",
        "reason": "off",
        "setpoint": 0.0,
        "trigger": "command",
        "source": "external",
        "warning": None,
        "ends_at": None,
        "entry_id": entry.entry_id,
        "device_id": controller.device_id,
        "name": "Ventilation",
    }

    hass.states.async_set(STANDBY_SWITCH, "off")
    await hass.async_block_till_done()
    assert controller.state.manual is None
    assert controller.current_decision.mode is Mode.AUTOMATIC  # type: ignore[union-attr]


@pytest.mark.usefixtures("entities")
async def test_pause_and_resume_switch_the_off_actuator(
    hass: HomeAssistant, unit: MockUnit, freezer: FrozenDateTimeFactory
) -> None:
    """Own stop/start of the off actuator is not taken as an external command."""
    entry = await setup_entry(hass, off_entity=STANDBY_SWITCH, off_state="on")
    controller = controller_of(entry)
    await advance(hass, freezer, 61)

    await controller.async_command(PauseCommand(source=Source.SERVICE))
    [stop] = unit.calls_for("switch", "turn_on")
    assert stop.data == {"entity_id": STANDBY_SWITCH}
    assert controller._writer.is_own(stop.context)
    manual = controller.state.manual
    assert isinstance(manual, Pause)
    assert manual.source is Source.SERVICE
    assert controller.state.stopped_by_integration

    await controller.async_command(ResumeCommand(source=Source.SERVICE))
    await hass.async_block_till_done()  # the follow-up sees the unit running
    [start] = unit.calls_for("switch", "turn_off")
    assert start.data == {"entity_id": STANDBY_SWITCH}
    assert controller.current_decision.mode is Mode.AUTOMATIC  # type: ignore[union-attr]
    assert not controller.state.stopped_by_integration
    assert unit.calls_for("number", "set_value")[-1].data["value"] == 80.0


async def _create_entry_with_flow(
    hass: HomeAssistant, off: dict[str, str] | None
) -> ConfigEntry:
    """Create and load an entry through the config flow (number in m³/h)."""
    flow = hass.config_entries.flow
    result: Any = await flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    user_input: dict[str, Any] = {"name": "Ventilation", "actuator": NUMBER_FLOW}
    if off is not None:
        user_input["off"] = off
    result = await flow.async_configure(result["flow_id"], user_input)
    result = await flow.async_configure(
        result["flow_id"], {"co2_sensors": [CO2_LIVING]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    entry: ConfigEntry = result["result"]
    assert entry.state is ConfigEntryState.LOADED
    return entry


@pytest.mark.usefixtures("entities")
async def test_bhv02_pause_after_setup_with_off_actuator(
    hass: HomeAssistant, unit: MockUnit, freezer: FrozenDateTimeFactory
) -> None:
    """An off actuator chosen at setup stops the unit; no minimum flow is written.

    C1a, BHV-02, BHV-21: a unit that does not stop at its lowest value is stopped
    with the standby switch (`off_state` = on).
    """
    entry = await _create_entry_with_flow(
        hass, {"off_entity": STANDBY_SWITCH, "off_state": "on"}
    )
    controller = controller_of(entry)
    await advance(hass, freezer, 61)
    writes = len(unit.calls_for("number", "set_value"))

    await hass.services.async_call(DOMAIN, "pause", {}, blocking=True)
    await hass.async_block_till_done()

    [stop] = unit.calls_for("switch", "turn_on")
    assert stop.data == {"entity_id": STANDBY_SWITCH}
    assert hass.states.get(STANDBY_SWITCH).state == "on"  # type: ignore[union-attr]
    assert len(unit.calls_for("number", "set_value")) == writes
    assert hass.states.get(NUMBER_FLOW).state != "0"  # type: ignore[union-attr]
    assert controller.current_decision.mode is Mode.OFF  # type: ignore[union-attr]
    assert controller.state.stopped_by_integration


@pytest.mark.usefixtures("entities")
async def test_bhv02_pause_after_setup_without_off_actuator(
    hass: HomeAssistant, unit: MockUnit, freezer: FrozenDateTimeFactory
) -> None:
    """Without an off actuator a pause writes the lowest actuator value (C1a)."""
    entry = await _create_entry_with_flow(hass, None)
    controller = controller_of(entry)
    await advance(hass, freezer, 61)

    await hass.services.async_call(DOMAIN, "pause", {}, blocking=True)
    await hass.async_block_till_done()

    assert unit.calls_for("switch", "turn_on") == []
    assert unit.calls_for("number", "set_value")[-1].data["value"] == 0
    assert controller.current_decision.mode is Mode.OFF  # type: ignore[union-attr]


@pytest.mark.usefixtures("entities")
async def test_fan_off_actuator_without_off_entity(
    hass: HomeAssistant, unit: MockUnit, freezer: FrozenDateTimeFactory
) -> None:
    """Without off actuator a pause writes the lowest value (0 % for a fan)."""
    hass.states.async_set(FAN, "on", {"percentage": 50, "percentage_step": 1})
    entry = await setup_entry(hass, actuator=FAN)
    controller = controller_of(entry)
    await advance(hass, freezer, 61)

    await controller.async_command(PauseCommand(source=Source.BUTTON))
    assert unit.calls_for("fan", "set_percentage")[-1].data["percentage"] == 0
    assert hass.states.get(FAN).state == "off"  # type: ignore[union-attr]
    assert controller.current_decision.mode is Mode.OFF  # type: ignore[union-attr]
    manual = controller.state.manual
    assert isinstance(manual, Pause)
    assert manual.source is Source.BUTTON  # own `off` is no external stop


@pytest.mark.usefixtures("entities")
async def test_fan_as_off_actuator(
    hass: HomeAssistant, unit: MockUnit, freezer: FrozenDateTimeFactory
) -> None:
    """A `fan` off actuator is switched with `fan.turn_off` / `fan.turn_on`."""
    entry = await setup_entry(hass, off_entity=FAN)
    controller = controller_of(entry)
    await advance(hass, freezer, 61)

    await controller.async_command(PauseCommand(source=Source.SERVICE))
    assert len(unit.calls_for("fan", "turn_off")) == 1
    await controller.async_command(ResumeCommand(source=Source.SERVICE))
    assert len(unit.calls_for("fan", "turn_on")) == 1


# --- Late confirmation (slow pollers, BHV-02, BHV-21) -------------------------------


@pytest.mark.parametrize("delay", [20, 45])
@pytest.mark.usefixtures("entities")
async def test_bhv21_late_confirmed_stop_keeps_the_timed_pause(
    hass: HomeAssistant, unit: MockUnit, freezer: FrozenDateTimeFactory, delay: int
) -> None:
    """A stop confirmed on the next poll (new context) is own: the pause stays timed."""
    events = async_capture_events(hass, EVENT_MODE_CHANGED)
    unit.delay[STANDBY_SWITCH] = delay
    entry = await setup_entry(hass, off_entity=STANDBY_SWITCH, off_state="on")
    controller = controller_of(entry)
    await advance(hass, freezer, 61)

    await controller.async_command(
        PauseCommand(source=Source.BUTTON, duration=timedelta(hours=1))
    )
    manual = controller.state.manual
    assert isinstance(manual, Pause)
    until = manual.until
    assert until is not None
    assert hass.states.get(STANDBY_SWITCH).state == "off"  # type: ignore[union-attr]

    await advance(hass, freezer, delay)
    state = hass.states.get(STANDBY_SWITCH)
    assert state is not None
    assert state.state == "on"
    assert controller._writer.is_own(state.context)
    manual = controller.state.manual
    assert isinstance(manual, Pause)
    assert manual.source is Source.BUTTON
    assert manual.until == until
    decision = controller.current_decision
    assert decision is not None
    assert decision.mode is Mode.OFF
    assert decision.ends_at == until
    assert controller.state.stopped_by_integration
    assert [event.data["source"] for event in events] == ["button"]


@pytest.mark.usefixtures("entities")
async def test_bhv21_late_confirmed_start_then_stop_at_the_unit(
    hass: HomeAssistant, unit: MockUnit, freezer: FrozenDateTimeFactory
) -> None:
    """A late start is own; a stop at the unit inside the window is still external."""
    unit.delay[STANDBY_SWITCH] = 30
    entry = await setup_entry(hass, off_entity=STANDBY_SWITCH, off_state="on")
    controller = controller_of(entry)
    await advance(hass, freezer, 61)
    await controller.async_command(PauseCommand(source=Source.SERVICE))
    await advance(hass, freezer, 30)
    events = async_capture_events(hass, EVENT_MODE_CHANGED)

    await controller.async_command(ResumeCommand(source=Source.SERVICE))
    assert len(unit.calls_for("switch", "turn_off")) == 1
    await advance(hass, freezer, 30)
    state = hass.states.get(STANDBY_SWITCH)
    assert state is not None
    assert state.state == "off"
    assert controller._writer.is_own(state.context)
    assert not controller.state.stopped_by_integration
    assert controller.current_decision.mode is Mode.AUTOMATIC  # type: ignore[union-attr]
    assert [event.data["source"] for event in events] == ["service"]

    # The expectation ended with the confirmation: this stop is the user's.
    await advance(hass, freezer, 10)
    hass.states.async_set(STANDBY_SWITCH, "on")
    await hass.async_block_till_done()
    manual = controller.state.manual
    assert isinstance(manual, Pause)
    assert manual.source is Source.EXTERNAL
    assert manual.until is None
    assert events[-1].data["source"] == "external"


@pytest.mark.usefixtures("entities")
async def test_bhv21_late_confirmed_fan_off_keeps_the_timed_pause(
    hass: HomeAssistant, unit: MockUnit, freezer: FrozenDateTimeFactory
) -> None:
    """A `fan` actuator without off actuator that reports `off` late is own."""
    hass.states.async_set(FAN, "on", {"percentage": 50, "percentage_step": 1})
    unit.delay[FAN] = 20
    entry = await setup_entry(hass, actuator=FAN)
    controller = controller_of(entry)
    await advance(hass, freezer, 61)

    await controller.async_command(
        PauseCommand(source=Source.BUTTON, duration=timedelta(minutes=30))
    )
    await advance(hass, freezer, 20)
    state = hass.states.get(FAN)
    assert state is not None
    assert state.state == "off"
    assert controller._writer.is_own(state.context)
    manual = controller.state.manual
    assert isinstance(manual, Pause)
    assert manual.source is Source.BUTTON
    assert manual.until is not None


@pytest.mark.parametrize(("delay", "writes"), [(20, 1), (45, 2)])
@pytest.mark.usefixtures("entities")
async def test_bhv02_late_confirmed_write_is_own_without_mismatch(
    hass: HomeAssistant,
    unit: MockUnit,
    freezer: FrozenDateTimeFactory,
    delay: int,
    writes: int,
) -> None:
    """A write confirmed late is own; within the window it never ends in a mismatch.

    Confirmed after the 30 s verification, the value is written once more and
    the retry verification finds it.
    """
    unit.delay[NUMBER_FLOW] = delay
    entry = await setup_entry(hass)
    controller = controller_of(entry)
    await advance(hass, freezer, 60)
    assert len(unit.calls_for("number", "set_value")) == 1

    for _ in range(12):  # 60 s in steps of 5 s: verification and retry
        await advance(hass, freezer, 5)

    state = hass.states.get(NUMBER_FLOW)
    assert state is not None
    assert state.state == "80.0"
    assert controller._writer.is_own(state.context)
    assert len(unit.calls_for("number", "set_value")) == writes
    assert not controller._writer.problems
    assert ("write_mismatch", None) not in problem_keys(controller)


# --- Commands -------------------------------------------------------------------------


@pytest.mark.usefixtures("entities")
async def test_bhv03_boost_rejected_while_control_disabled(
    hass: HomeAssistant, unit: MockUnit, freezer: FrozenDateTimeFactory
) -> None:
    """Boost while the control switch is off raises a translated validation error."""
    entry = await setup_entry(hass)
    controller = controller_of(entry)
    await advance(hass, freezer, 61)

    await controller.async_command(
        ControlSwitchCommand(source=Source.SERVICE, enabled=False)
    )
    assert controller.current_decision.mode is Mode.DISABLED  # type: ignore[union-attr]
    with pytest.raises(ServiceValidationError) as err:
        await controller.async_command(BoostCommand(source=Source.BUTTON))
    assert err.value.translation_domain == DOMAIN
    assert err.value.translation_key == "control_disabled"
    assert controller.state.manual is None

    await controller.async_command(
        ControlSwitchCommand(source=Source.SERVICE, enabled=True)
    )
    with pytest.raises(ServiceValidationError) as err:
        await controller.async_command(BoostCommand(source=Source.SERVICE, level=1000))
    assert err.value.translation_key == "level_out_of_range"


@pytest.mark.usefixtures("entities")
async def test_bhv20_boost_writes_immediately_and_ends(
    hass: HomeAssistant, unit: MockUnit, freezer: FrozenDateTimeFactory
) -> None:
    """A boost bypasses shaping and ends at `until` through a scheduled evaluation."""
    events = async_capture_events(hass, EVENT_MODE_CHANGED)
    entry = await setup_entry(hass)
    controller = controller_of(entry)
    await advance(hass, freezer, 61)

    await controller.async_command(
        BoostCommand(source=Source.SERVICE, duration=timedelta(minutes=5))
    )
    assert unit.calls_for("number", "set_value")[-1].data["value"] == 300.0
    assert events[-1].data["to_mode"] == "boost"

    await advance(hass, freezer, 300)
    assert controller.current_decision.mode is Mode.AUTOMATIC  # type: ignore[union-attr]
    assert events[-1].data["trigger"] == "timeout"
    assert unit.calls_for("number", "set_value")[-1].data["value"] == 80.0


# --- Readiness (C4 runtime part) -------------------------------------------------------


@pytest.mark.usefixtures("entities")
async def test_c4_auto_correct_calls_select_option(
    hass: HomeAssistant, unit: MockUnit, freezer: FrozenDateTimeFactory
) -> None:
    """An unmet readiness condition is corrected after the grace, then written."""
    events = async_capture_events(hass, EVENT_READINESS_CORRECTED)
    entry = await setup_entry(hass, subentries=[readiness(CONTROL_MODE, "Remote")])
    controller = controller_of(entry)
    assert ("not_ready", CONTROL_MODE) in problem_keys(controller)  # in the grace
    assert unit.calls == []

    await advance(hass, freezer, 60)
    [call] = unit.calls_for("select", "select_option")
    assert call.data == {"entity_id": CONTROL_MODE, "option": "Remote"}
    assert controller._writer.is_own(call.context)
    assert [event.data for event in events] == [
        {
            "entry_id": entry.entry_id,
            "entity_id": CONTROL_MODE,
            "from_state": "Manual",
            "to_state": "Remote",
        }
    ]
    # The corrected state is evaluated at once and the write follows (bypass).
    assert unit.calls_for("number", "set_value")[-1].data["value"] == 80.0
    assert ("not_ready", CONTROL_MODE) not in problem_keys(controller)


@pytest.mark.usefixtures("entities")
async def test_c4_not_ready_after_three_failed_corrections(
    hass: HomeAssistant,
    unit: MockUnit,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A condition that stays unmet is `not_ready` after 3 attempts (BHV-04)."""
    unit.ignore.add(CONTROL_MODE)
    entry = await setup_entry(hass, subentries=[readiness(CONTROL_MODE, "Remote")])
    controller = controller_of(entry)
    caplog.set_level(logging.INFO)

    await advance(hass, freezer, 60)
    assert ("not_ready", CONTROL_MODE) not in problem_keys(controller)
    for _ in range(3):
        await advance(hass, freezer, 60)

    assert len(unit.calls_for("select", "select_option")) == 3
    assert ("not_ready", CONTROL_MODE) in problem_keys(controller)
    assert unit.calls_for("number", "set_value") == []
    decision = controller.current_decision
    assert decision is not None
    assert decision.write_blocked_by is WriteBlock.NOT_READY
    assert caplog.text.count(f"Readiness condition on {CONTROL_MODE} is not met") == 1

    # Retries continue every 5 minutes.
    await advance(hass, freezer, 300)
    assert len(unit.calls_for("select", "select_option")) == 4


@pytest.mark.usefixtures("entities")
async def test_c4_no_correction_while_control_disabled(
    hass: HomeAssistant, unit: MockUnit, freezer: FrozenDateTimeFactory
) -> None:
    """Control switch off: no write and no readiness correction (BHV-03)."""
    entry = await setup_entry(hass, subentries=[readiness(CONTROL_MODE, "Remote")])
    controller = controller_of(entry)
    await controller.async_command(
        ControlSwitchCommand(source=Source.SERVICE, enabled=False)
    )
    await advance(hass, freezer, 120)

    assert unit.calls == []
    assert ("not_ready", CONTROL_MODE) in problem_keys(controller)


# --- Store --------------------------------------------------------------------------------


@pytest.mark.usefixtures("entities")
async def test_bhv50_store_round_trip(
    hass: HomeAssistant,
    unit: MockUnit,
    freezer: FrozenDateTimeFactory,
    hass_storage: dict[str, Any],
) -> None:
    """State changes are saved with a delay of 1 s and loaded on the next setup."""
    entry = await setup_entry(hass)
    controller = controller_of(entry)
    key = f"{DOMAIN}.{entry.entry_id}"
    await controller.async_command(BoostCommand(source=Source.SERVICE, level=250))
    await advance(hass, freezer, 2)

    stored = hass_storage[key]
    assert stored["version"] == 1
    assert stored["data"]["manual"]["type"] == "boost"
    assert stored["data"]["manual"]["level"] == 250

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    manual = controller_of(entry).state.manual
    assert isinstance(manual, Boost)
    assert manual.level == 250


@pytest.mark.parametrize(
    "stored",
    [
        {"version": 1, "minor_version": 1, "data": {"manual": {"type": "bogus"}}},
        {"version": 1, "minor_version": 1, "data": ["not", "a", "dict"]},
        {"version": 2, "minor_version": 1, "data": {}},
    ],
    ids=["corrupt", "wrong_type", "future_version"],
)
@pytest.mark.usefixtures("entities")
async def test_bhv50_unreadable_store_starts_with_defaults(
    hass: HomeAssistant,
    unit: MockUnit,
    hass_storage: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
    stored: dict[str, Any],
) -> None:
    """Corrupt data or a future version: defaults, a warning and the reset flag."""
    entry = MockConfigEntry(
        domain=DOMAIN, title="Ventilation", data={}, options=entry_options()
    )
    key = f"{DOMAIN}.{entry.entry_id}"
    hass_storage[key] = {"key": key, **stored}
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    controller = controller_of(entry)
    assert controller.state_reset
    assert controller.state.manual is None
    assert "could not be read" in caplog.text


@pytest.mark.usefixtures("entities")
async def test_bhv50_expired_boost_ends_while_offline(
    hass: HomeAssistant,
    unit: MockUnit,
    hass_storage: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A boost that ended while HA was offline ends with `timeout_while_offline`."""
    caplog.set_level(logging.INFO)
    events = async_capture_events(hass, EVENT_MODE_CHANGED)
    entry = MockConfigEntry(
        domain=DOMAIN, title="Ventilation", data={}, options=entry_options()
    )
    key = f"{DOMAIN}.{entry.entry_id}"
    hass_storage[key] = {
        "key": key,
        "version": 1,
        "minor_version": 1,
        "data": {
            "manual": {
                "type": "boost",
                "until": "2020-01-01T00:30:00+00:00",
                "level": 300,
                "started": "2020-01-01T00:00:00+00:00",
                "source": "button",
            },
            "last_mode": "boost",
        },
    }
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    controller = controller_of(entry)
    assert not controller.state_reset
    assert controller.state.manual is None
    assert "ended while Home Assistant was offline" in caplog.text
    assert events[-1].data["trigger"] == "timeout_while_offline"
    assert events[-1].data["from_mode"] == "boost"


@pytest.mark.usefixtures("entities")
async def test_bhv50_store_migration_from_1_1(
    hass: HomeAssistant, unit: MockUnit, hass_storage: dict[str, Any]
) -> None:
    """A 0.1 store (1.1) is migrated: the pause keeps running, `off_since` = start."""
    entry = MockConfigEntry(
        domain=DOMAIN, title="Ventilation", data={}, options=entry_options()
    )
    key = f"{DOMAIN}.{entry.entry_id}"
    started = "2026-10-14T18:00:00+00:00"
    hass_storage[key] = {
        "key": key,
        "version": 1,
        "minor_version": 1,
        "data": {
            "manual": {
                "type": "pause",
                "until": None,
                "started": started,
                "source": "button",
            },
            "last_mode": "off",
        },
    }
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    controller = controller_of(entry)
    assert not controller.state_reset
    assert isinstance(controller.state.manual, Pause)
    assert controller.state.off_since is not None
    assert controller.state.off_since.isoformat() == started
    assert hass_storage[key]["minor_version"] == 3
    assert hass_storage[key]["data"]["off_since"] == started
    assert hass_storage[key]["data"]["auto_boosts"] == {}
    assert hass_storage[key]["data"]["safety"]["active"] == {}
