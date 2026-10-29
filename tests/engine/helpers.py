"""Builders and a small simulator for engine tests (no Home Assistant).

Numbers follow the spec tables: max flow 400 m³/h and the defaults
(min 80, fallback 150, steps 50, quiet 250, boost 300).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

from engine import apply_command, evaluate
from engine.models import (
    ActuatorSnapshot,
    Command,
    ControllerState,
    Decision,
    Effect,
    InputValue,
    Mode,
    OffActuatorSnapshot,
    Options,
    ProblemKey,
    Snapshot,
    TurnOff,
    TurnOn,
    WarningDetails,
    WriteFlow,
)

T0 = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)

CO2_A = "sensor.living_room_co2"
CO2_B = "sensor.bedroom_co2"
HUM_A = "sensor.living_room_humidity"
HUM_B = "sensor.bedroom_humidity"
PRESENCE = "person.resident"
ACTUATOR = "number.ventilation_flow"
FAN = "fan.ventilation"
SELECT = "select.ventilation_stage"
OFF_SWITCH = "switch.ventilation_standby"
READY = "select.ventilation_control_mode"


def make_options(**overrides: Any) -> Options:
    """Return options of a 400 m³/h unit with two CO₂ and two humidity sensors."""
    readiness = overrides.pop("readiness_conditions", ())
    sources = overrides.pop("warning_sources", ())
    data: dict[str, Any] = {
        "actuator": ACTUATOR,
        "max_flow": 400,
        "co2_sensors": [CO2_A, CO2_B],
        "humidity_sensors": [HUM_A, HUM_B],
    }
    data.update(overrides)
    return Options.from_mapping(data, readiness, sources)


def co2_for(flow: float) -> float:
    """CO₂ value in ppm for which the default CO₂ ramp asks for ``flow``."""
    return 850 + (flow - 80) * 550 / 320


def running_state(**overrides: Any) -> ControllerState:
    """Return the state of an engine that has been running for a while."""
    values: dict[str, Any] = {
        "started": True,
        "written_since_start": True,
        "last_mode": Mode.AUTOMATIC,
        "last_write": T0 - timedelta(hours=1),
    }
    values.update(overrides)
    return ControllerState(**values)


class Sim:
    """Steps the engine through time and applies its effects to fake entities."""

    def __init__(
        self,
        options: Options | None = None,
        *,
        now: datetime = T0,
        values: dict[str, str | float | None] | None = None,
        actuator: str | None = "80",
        actuator_attrs: dict[str, Any] | None = None,
        off: str | None = None,
        state: ControllerState | None = None,
        started_at: datetime | None = None,
    ) -> None:
        """Set up the fake world; defaults give a demand of 80 m³/h."""
        self.options = options or make_options()
        self.now = now
        self.started_at = started_at or now - timedelta(hours=1)
        self.values: dict[str, str | None] = {
            CO2_A: "700",
            CO2_B: "700",
            HUM_A: "45",
            HUM_B: "45",
        }
        self.reported: dict[str, datetime] = {}
        for entity_id, value in (values or {}).items():
            self.set(entity_id, value)
        self.actuator = ActuatorSnapshot(state=actuator, **(actuator_attrs or {}))
        self.off = OffActuatorSnapshot(state=off) if off is not None else None
        self.reported_problems: frozenset[ProblemKey] = frozenset()
        self.missing_entities: frozenset[str] = frozenset()
        self.details: dict[str, tuple[WarningDetails, ...] | None] = {}
        self.details_failed: set[str] = set()
        self.state = state or running_state()
        self.decision: Decision | None = None

    def set(self, entity_id: str, value: str | float | None) -> None:
        """Set an entity state (numbers are converted to strings); reported now."""
        self.values[entity_id] = None if value is None else str(value)
        self.reported.pop(entity_id, None)

    def set_stale(self, entity_id: str, last_reported: datetime) -> None:
        """Pin ``last_reported`` of an entity."""
        self.reported[entity_id] = last_reported

    def set_actuator(
        self, state: str | None, *, foreign: bool = False, **attrs: Any
    ) -> None:
        """Change the actuator state (an external change if ``foreign``)."""
        self.actuator = replace(
            self.actuator, state=state, foreign_change=foreign, **attrs
        )

    def set_off(self, state: str | None, *, foreign: bool = False) -> None:
        """Change the off actuator state (an external change if ``foreign``)."""
        self.off = OffActuatorSnapshot(state=state, foreign_change=foreign)

    def advance(self, **delta: float) -> None:
        """Move the clock forward."""
        self.now += timedelta(**delta)

    def snapshot(self) -> Snapshot:
        """Build the snapshot of the current fake world."""
        return Snapshot(
            now=self.now,
            started_at=self.started_at,
            states={
                entity_id: InputValue(
                    state=value, last_reported=self.reported.get(entity_id, self.now)
                )
                for entity_id, value in self.values.items()
            },
            actuator=self.actuator,
            off_actuator=self.off,
            reported_problems=self.reported_problems,
            missing_entities=self.missing_entities,
            warning_details=dict(self.details),
            details_failed=frozenset(self.details_failed),
        )

    def warn(self, entity_id: str, *details: WarningDetails, state: str = "on") -> None:
        """Set a warning source entity to ``state`` with the given details."""
        self.set(entity_id, state)
        self.details[entity_id] = details or None

    def clear(self, entity_id: str, state: str = "off") -> None:
        """Set a warning source entity inactive and drop its details."""
        self.set(entity_id, state)
        self.details.pop(entity_id, None)

    def command(self, command: Command) -> None:
        """Apply a command at the current time."""
        self.state = apply_command(self.state, command, self.options, self.now)

    def run(self) -> tuple[Decision, list[Effect]]:
        """Evaluate once and apply the effects to the fake entities."""
        decision, self.state, effects = evaluate(
            self.snapshot(), self.state, self.options
        )
        self.decision = decision
        stopped = "off" if self.options.off_domain == "fan" else self.options.off_state
        running = "on" if stopped == "off" else "off"
        for effect in effects:
            if isinstance(effect, WriteFlow):
                self._write(effect)
            elif isinstance(effect, TurnOff):
                self.off = OffActuatorSnapshot(state=stopped)
            elif isinstance(effect, TurnOn):
                self.off = OffActuatorSnapshot(state=running)
        self.actuator = replace(self.actuator, foreign_change=False)
        if self.off is not None:
            self.off = replace(self.off, foreign_change=False)
        return decision, effects

    def _write(self, effect: WriteFlow) -> None:
        domain = self.options.actuator_domain
        if domain == "fan":
            assert isinstance(effect.native, float)
            self.actuator = replace(
                self.actuator,
                state="on" if effect.native > 0 else "off",
                percentage=effect.native,
            )
        else:
            self.actuator = replace(self.actuator, state=str(effect.native))


def writes(effects: Iterable[Effect]) -> list[WriteFlow]:
    """Return the WriteFlow effects of an evaluation."""
    return [effect for effect in effects if isinstance(effect, WriteFlow)]


def of_type[T](effects: Iterable[Effect], kind: type[T]) -> list[T]:
    """Return the effects of one type."""
    return [effect for effect in effects if isinstance(effect, kind)]
