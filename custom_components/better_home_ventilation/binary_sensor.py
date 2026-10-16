"""Problem sensor (docs/spec/04-interface.md#problem-attributes)."""

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
from .entity import PushedEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BetterVentilationConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the problem sensor of an entry."""
    async_add_entities([ProblemSensor(entry.runtime_data)])


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
