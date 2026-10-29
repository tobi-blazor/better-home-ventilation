"""Problem and safety warning sensors (docs/spec/04-interface.md#entities)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .controller import BetterVentilationConfigEntry, VentilationController
from .engine.models import Decision
from .entity import PushedEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BetterVentilationConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the problem sensor and, with warning sources, the safety warning."""
    controller = entry.runtime_data
    entities: list[BinarySensorEntity] = [ProblemSensor(controller)]
    if controller.has_warning_sources:
        entities.append(SafetyWarningSensor(controller))
    async_add_entities(entities)


def warnings_attribute(decision: Decision) -> list[dict[str, Any]]:
    """Return the ``warnings`` attribute: every active warning (04-interface.md)."""
    return [
        {
            "source": warning.source_name,
            "headline": warning.headline,
            "severity": str(warning.severity) if warning.severity else None,
            "overridden": warning.incident_key in decision.overridden_incidents,
            "since": warning.since,
        }
        for warning in decision.warnings
    ]


class ProblemSensor(PushedEntity, BinarySensorEntity):
    """On while any problem persists longer than ``problem_delay``."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, controller: VentilationController) -> None:
        """Initialize the sensor."""
        super().__init__(controller, "problem")

    @property
    def is_on(self) -> bool | None:
        """Return True if a problem persists longer than ``problem_delay``."""
        return self.decision.problem if self.decision else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the problems that count (persisting longer than the delay)."""
        decision = self.decision
        if decision is None:
            return {"problems": []}
        delay = self.controller.options.problem_delay
        problems: list[dict[str, Any]] = []
        for problem in decision.problems:
            if decision.time - problem.since < delay:
                continue
            item: dict[str, Any] = {"key": str(problem.key)}
            if problem.entity_id is not None:
                item["entity_id"] = problem.entity_id
            item["since"] = problem.since
            problems.append(item)
        return {"problems": problems}


class SafetyWarningSensor(PushedEntity, BinarySensorEntity):
    """On while any warning is active, overridden or not (BHV-40)."""

    _attr_device_class = BinarySensorDeviceClass.SAFETY

    def __init__(self, controller: VentilationController) -> None:
        """Initialize the sensor."""
        super().__init__(controller, "safety_warning")

    @property
    def is_on(self) -> bool | None:
        """Return True while a warning is active."""
        return bool(self.decision.warnings) if self.decision else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the active warnings."""
        decision = self.decision
        return {"warnings": warnings_attribute(decision) if decision else []}
