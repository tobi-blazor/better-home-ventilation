"""Sensors: setpoint, reason, mode and end time (BHV-51, BHV-23).

docs/spec/04-interface.md#entities and #setpoint-attributes.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import UnitOfVolumeFlowRate
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .controller import BetterVentilationConfigEntry, VentilationController
from .engine.models import Decision, Mode, Reason
from .entity import PushedEntity

PARALLEL_UPDATES = 0

#: Modes in which the setpoint sensor shows `off_method`.
_OFF_MODES = frozenset({Mode.OFF, Mode.SAFETY_OFF})


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BetterVentilationConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensors of an entry."""
    controller = entry.runtime_data
    async_add_entities(
        [
            SetpointSensor(controller),
            ReasonSensor(controller),
            ModeSensor(controller),
            EndsAtSensor(controller),
        ]
    )


def setpoint_attributes(decision: Decision) -> dict[str, Any]:
    """Return the attributes of the setpoint sensor (04-interface.md)."""
    contributions = decision.contributions
    inputs = decision.inputs
    attributes: dict[str, Any] = {
        "mode": str(decision.mode),
        "reason": str(decision.reason),
        "reasons": [str(reason) for reason in decision.reasons],
        "profile": str(decision.profile),
        "target": decision.target,
        "written": decision.last_written_flow,
        "last_write": decision.last_write,
        "actuator_value": decision.actuator_value,
        "contributions": {
            "minimum": contributions.minimum,
            "co2": contributions.co2,
            "humidity_high": contributions.humidity_high,
            "fallback": contributions.fallback,
            "boost": contributions.boost,
            "shower": contributions.shower,
            "cooking": contributions.cooking,
        },
        "co2_max": inputs.co2_max,
        "co2_max_entity": inputs.co2_max_entity,
        "humidity_max": inputs.h_max,
        "humidity_max_entity": inputs.h_max_entity,
        "humidity_mean": inputs.h_mean,
        "limits": [str(limit) for limit in decision.limits],
        "invalid_inputs": list(decision.invalid_inputs),
        "write_blocked_by": (
            str(decision.write_blocked_by) if decision.write_blocked_by else None
        ),
    }
    if decision.mode in _OFF_MODES:
        attributes["off_method"] = (
            str(decision.off_method) if decision.off_method else None
        )
    # TODO(0.2, BHV-41): `awaiting_restart` in `safety_off`.
    return attributes


class SetpointSensor(PushedEntity, SensorEntity):
    """Flow the unit should run at, after output shaping; 0 in off modes."""

    _attr_device_class = SensorDeviceClass.VOLUME_FLOW_RATE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfVolumeFlowRate.CUBIC_METERS_PER_HOUR
    _attr_suggested_display_precision = 0
    _unrecorded_attributes = frozenset({"actuator_value", "write_blocked_by"})

    def __init__(self, controller: VentilationController) -> None:
        """Initialize the sensor."""
        super().__init__(controller, "setpoint")

    @property
    def native_value(self) -> float | None:
        """Return the setpoint."""
        return self.decision.setpoint if self.decision else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the decision record (04-interface.md#setpoint-attributes)."""
        return setpoint_attributes(self.decision) if self.decision else None


class ReasonSensor(PushedEntity, SensorEntity):
    """Rule or mode that determined the setpoint."""

    _attr_device_class = SensorDeviceClass.ENUM

    def __init__(self, controller: VentilationController) -> None:
        """Initialize the sensor."""
        super().__init__(controller, "reason")
        self._attr_options = [str(reason) for reason in Reason]

    @property
    def native_value(self) -> str | None:
        """Return the reason."""
        return str(self.decision.reason) if self.decision else None


class ModeSensor(PushedEntity, SensorEntity):
    """Resolved operating mode."""

    _attr_device_class = SensorDeviceClass.ENUM

    def __init__(self, controller: VentilationController) -> None:
        """Initialize the sensor."""
        super().__init__(controller, "mode")
        self._attr_options = [str(mode) for mode in Mode]

    @property
    def native_value(self) -> str | None:
        """Return the mode."""
        return str(self.decision.mode) if self.decision else None


class EndsAtSensor(PushedEntity, SensorEntity):
    """End of the current boost or pause, otherwise None."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, controller: VentilationController) -> None:
        """Initialize the sensor."""
        super().__init__(controller, "ends_at")

    @property
    def native_value(self) -> datetime | None:
        """Return the end time."""
        return self.decision.ends_at if self.decision else None
