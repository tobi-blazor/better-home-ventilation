"""Provider ``nina``: the NINA service ``nina.get_details`` (03-modes.md).

HA core ``nina`` (checked on 2026.10.1): one ``binary_sensor`` per message
slot; the attributes except ``id`` are deprecated and removed in HA 2026.11.
The details come from the entity service ``nina.get_details``
(``SupportsResponse.ONLY``); with ``return_response`` the response is keyed by
entity id and holds ``headline``, ``description``, ``sender``, ``severity``,
``recommended_actions``, ``id``, ``sent``, ``start``, ``expires``, … .

HA 2026.4 (the minimum version) has no such service; there the same fields
are state attributes, which are read instead.

Details are cached per NINA ``id``: the service is called when an entity
turns on with an id not seen before, or when its id changes (a warning moved
to another slot keeps its id and its cached details). A failed call is not
repeated for the same id for :data:`RETRY_AFTER` and raises
:class:`DetailsError` meanwhile.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Mapping
from datetime import timedelta
import time
from typing import Any, Final

from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError

from custom_components.better_home_ventilation.engine.models import WarningDetails
from custom_components.better_home_ventilation.engine.safety import (
    PROVIDER_NINA,
    make_details,
)

from .provider import DETAILS_TIMEOUT, DetailsError

NINA_DOMAIN: Final = "nina"
SERVICE_GET_DETAILS: Final = "get_details"
ATTR_ID: Final = "id"
#: Number of warnings whose details are kept.
CACHE_SIZE: Final = 64
#: A failed call is retried for the same warning after this time (NINA
#: itself polls every 5 minutes).
RETRY_AFTER: Final = timedelta(minutes=5)


def details_from_nina(
    data: Mapping[str, Any], warning_id: str | None = None
) -> list[WarningDetails]:
    """Return the warning of a ``get_details`` response or of the attributes."""
    if not data:
        return []
    details = make_details(
        key=data.get(ATTR_ID) or warning_id,
        headline=data.get("headline"),
        text=data.get("description"),
        actions=data.get("recommended_actions"),
        severity=data.get("severity"),
        sender=data.get("sender"),
    )
    if details.headline is None and details.text is None:
        return []
    return [details]


class NinaProvider:
    """NINA warnings (1 per slot entity)."""

    name = PROVIDER_NINA

    def __init__(self) -> None:
        """Initialize the cache."""
        self._cache: OrderedDict[str, list[WarningDetails]] = OrderedDict()
        self._failed: dict[str | None, float] = {}

    async def async_get_details(
        self, hass: HomeAssistant, entity_id: str, state: State
    ) -> list[WarningDetails]:
        """Return the warning behind an active NINA slot."""
        raw_id = state.attributes.get(ATTR_ID)
        warning_id = str(raw_id) if raw_id else None
        if warning_id is not None and warning_id in self._cache:
            self._cache.move_to_end(warning_id)
            return self._cache[warning_id]
        if not hass.services.has_service(NINA_DOMAIN, SERVICE_GET_DETAILS):
            # HA before the service (2026.4): the details are attributes.
            return details_from_nina(state.attributes, warning_id)
        failed_at = self._failed.get(warning_id)
        if failed_at is not None and time.monotonic() - failed_at < (
            RETRY_AFTER.total_seconds()
        ):
            raise DetailsError(f"details of {entity_id} failed recently")
        try:
            async with asyncio.timeout(DETAILS_TIMEOUT.total_seconds()):
                response = await hass.services.async_call(
                    NINA_DOMAIN,
                    SERVICE_GET_DETAILS,
                    target={"entity_id": entity_id},
                    blocking=True,
                    return_response=True,
                )
        except (HomeAssistantError, TimeoutError) as err:
            self._failed[warning_id] = time.monotonic()
            raise DetailsError(str(err) or type(err).__name__) from err
        self._failed.pop(warning_id, None)
        data = (response or {}).get(entity_id)
        details = details_from_nina(
            data if isinstance(data, Mapping) else {}, warning_id
        )
        if warning_id is not None:
            self._cache[warning_id] = details
            while len(self._cache) > CACHE_SIZE:
                self._cache.popitem(last=False)
        return details
