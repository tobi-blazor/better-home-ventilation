"""Provider ``dwd_weather_warnings``: indexed attributes of the DWD sensors.

HA core ``dwd_weather_warnings/sensor.py`` (checked on 2026.4.0 and 2026.10.1):
``warning_count`` and per warning ``warning_<i>_headline``, ``_description``,
``_instruction``, ``_level`` (0 to 4), ``_name``, ``_type``, ``_start``,
``_end``, numbered from 1. Level 0 is no warning and is skipped. The warning
key is ``headline:start`` (03-modes.md#warning-key-and-incident).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

from homeassistant.core import HomeAssistant, State

from custom_components.better_home_ventilation.engine.models import WarningDetails
from custom_components.better_home_ventilation.engine.safety import (
    PROVIDER_DWD,
    make_details,
)

ATTR_WARNING_COUNT: Final = "warning_count"


def _count(attributes: Mapping[str, Any]) -> int:
    try:
        return max(int(attributes.get(ATTR_WARNING_COUNT) or 0), 0)
    except TypeError, ValueError:
        return 0


def _level(value: Any) -> int | None:
    try:
        return int(value)
    except TypeError, ValueError:
        return None


def details_from_dwd(attributes: Mapping[str, Any]) -> list[WarningDetails]:
    """Return every non-zero warning of a DWD sensor."""
    warnings: list[WarningDetails] = []
    for index in range(1, _count(attributes) + 1):
        prefix = f"warning_{index}_"
        level = attributes.get(f"{prefix}level")
        if _level(level) == 0:
            continue
        headline = attributes.get(f"{prefix}headline") or attributes.get(
            f"{prefix}name"
        )
        start = attributes.get(f"{prefix}start")
        warnings.append(
            make_details(
                key=f"{headline}:{start}" if headline else None,
                headline=headline,
                text=attributes.get(f"{prefix}description"),
                actions=attributes.get(f"{prefix}instruction"),
                severity=level,
                sender="DWD",
            )
        )
    return warnings


class DwdProvider:
    """DWD Weather Warnings (0…n warnings per sensor)."""

    name = PROVIDER_DWD

    async def async_get_details(
        self, hass: HomeAssistant, entity_id: str, state: State
    ) -> list[WarningDetails]:
        """Return the warnings of the sensor's indexed attributes."""
        return details_from_dwd(state.attributes)
