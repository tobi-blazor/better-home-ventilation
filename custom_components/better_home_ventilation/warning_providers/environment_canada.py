"""Provider ``environment_canada``: indexed alert titles of the alert sensors.

HA core ``environment_canada/sensor.py`` (``ECAlertSensorEntity``, checked on
2026.4.0 and 2026.10.1): state = number of alerts, attributes ``alert_<i>``
(title) and ``alert_time_<i>`` (date), numbered from 1. Only the title is
available, so a warning has a headline and no text. The key is
``title:time``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.core import HomeAssistant, State

from custom_components.better_home_ventilation.engine.models import WarningDetails
from custom_components.better_home_ventilation.engine.safety import (
    PROVIDER_ENVIRONMENT_CANADA,
    make_details,
)


def details_from_environment_canada(
    attributes: Mapping[str, Any],
) -> list[WarningDetails]:
    """Return one warning per ``alert_<i>`` title."""
    warnings: list[WarningDetails] = []
    index = 1
    while f"alert_{index}" in attributes:
        title = attributes[f"alert_{index}"]
        if title:
            time = attributes.get(f"alert_time_{index}")
            warnings.append(
                make_details(
                    key=f"{title}:{time}" if time else title,
                    headline=title,
                    sender="Environment Canada",
                )
            )
        index += 1
    return warnings


class EnvironmentCanadaProvider:
    """Environment Canada alerts (0…n warnings per sensor)."""

    name = PROVIDER_ENVIRONMENT_CANADA

    async def async_get_details(
        self, hass: HomeAssistant, entity_id: str, state: State
    ) -> list[WarningDetails]:
        """Return the alerts of the sensor's indexed attributes."""
        return details_from_environment_canada(state.attributes)
