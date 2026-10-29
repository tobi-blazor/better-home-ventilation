"""Provider ``attributes``: one warning from generic state attributes.

The fallback for every platform without its own provider, e.g. Meteoalarm,
whose ``binary_sensor`` exposes the alert dict of the API as attributes
(``headline``, ``description``, ``instruction``, ``severity``,
``awareness_level``, …; 03-modes.md#details-providers). The first present
name wins per field.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

from homeassistant.core import HomeAssistant, State

from custom_components.better_home_ventilation.engine.models import WarningDetails
from custom_components.better_home_ventilation.engine.safety import (
    PROVIDER_ATTRIBUTES,
    make_details,
)

from .provider import first_present

HEADLINE: Final = ("headline", "title", "event", "name")
TEXT: Final = ("description", "text", "message")
ACTIONS: Final = ("instruction", "recommended_actions")
SEVERITY: Final = ("severity", "level", "awareness_level")
IDENTIFIER: Final = ("id", "identifier")
SENDER: Final = ("sender", "senderName", "sender_name")


def details_from_attributes(attributes: Mapping[str, Any]) -> list[WarningDetails]:
    """Return the single warning of the attributes, or none without text.

    Without a headline and without a text the entity has no details (it
    counts as one warning without details, like provider ``none``).
    """
    headline = first_present(attributes, HEADLINE)
    text = first_present(attributes, TEXT)
    if headline is None and text is None:
        return []
    details = make_details(
        key=first_present(attributes, IDENTIFIER),
        headline=headline,
        text=text,
        actions=first_present(attributes, ACTIONS),
        severity=first_present(attributes, SEVERITY),
        sender=first_present(attributes, SENDER),
    )
    if details.headline is None and details.text is None:
        return []
    return [details]


class AttributesProvider:
    """Generic attributes (0…1 warnings per entity)."""

    name = PROVIDER_ATTRIBUTES

    async def async_get_details(
        self, hass: HomeAssistant, entity_id: str, state: State
    ) -> list[WarningDetails]:
        """Return the warning of the entity's attributes."""
        return details_from_attributes(state.attributes)
