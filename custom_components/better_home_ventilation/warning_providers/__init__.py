"""Details providers of the warning sources (BHV-45).

docs/spec/03-modes.md#details-providers: a provider turns an active source
entity into a list of zero or more :class:`~..engine.models.WarningDetails`,
built with :func:`~..engine.safety.make_details`. The provider is chosen from
the entity registry ``platform`` of the source entity (:data:`REGISTRY`); the
subentry option ``details`` can force ``attributes`` or ``none``.

Contract for contributors (also in the README): a provider is one class with

- ``name``: the provider key (usually the platform it is registered for);
- ``async_get_details(hass, entity_id, state) -> list[WarningDetails]``:
  called only for an active entity. Return an empty list if the entity has
  no details (it then counts as one warning without details). Raise
  :class:`DetailsError` if the details could not be fetched (problem
  ``warning_details_failed``, the entity counts without details). The call
  must not take longer than :data:`DETAILS_TIMEOUT` (it is cancelled
  shortly after).

Support for another integration is a small PR: a module here, one entry in
:data:`REGISTRY` and a test based on a recorded state.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import timedelta
import logging
from typing import Any, Final, Protocol

from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er

from custom_components.better_home_ventilation.engine.models import (
    InputValue,
    WarningDetails,
    WarningSource,
)
from custom_components.better_home_ventilation.engine.options import (
    CONF_SOURCE_DEVICE,
    CONF_SOURCE_ENTITY,
    DETAILS_ATTRIBUTES,
    DETAILS_NONE,
)
from custom_components.better_home_ventilation.engine.safety import (
    PROVIDER_ATTRIBUTES,
    PROVIDER_DWD,
    PROVIDER_ENVIRONMENT_CANADA,
    PROVIDER_NINA,
    PROVIDER_NONE,
    entity_active,
)

from .attributes import AttributesProvider
from .dwd import DwdProvider
from .environment_canada import EnvironmentCanadaProvider
from .nina import NinaProvider
from .provider import DETAILS_TIMEOUT, DetailsError

_LOGGER = logging.getLogger(__name__)

#: Guard for providers that do not enforce :data:`DETAILS_TIMEOUT` themselves.
_GUARD: Final = DETAILS_TIMEOUT + timedelta(seconds=1)


class WarningProvider(Protocol):
    """The provider contract (see the module docstring)."""

    name: str

    async def async_get_details(
        self, hass: HomeAssistant, entity_id: str, state: State
    ) -> list[WarningDetails]:
        """Return the warnings behind an active entity."""
        ...


class NoDetailsProvider:
    """Provider ``none``: the source counts as one warning without details."""

    name = PROVIDER_NONE

    async def async_get_details(
        self, hass: HomeAssistant, entity_id: str, state: State
    ) -> list[WarningDetails]:
        """Return no details."""
        return []


#: Providers by entity registry platform; every other platform uses
#: ``attributes`` (or ``none``, see :func:`detect_provider`).
REGISTRY: Final[Mapping[str, Callable[[], WarningProvider]]] = {
    PROVIDER_NINA: NinaProvider,
    PROVIDER_DWD: DwdProvider,
    PROVIDER_ENVIRONMENT_CANADA: EnvironmentCanadaProvider,
}

#: Every provider by name (registry providers plus the fallbacks).
PROVIDERS: Final[Mapping[str, Callable[[], WarningProvider]]] = {
    **REGISTRY,
    PROVIDER_ATTRIBUTES: AttributesProvider,
    PROVIDER_NONE: NoDetailsProvider,
}

#: Platforms and domains of helpers and detectors that never carry warning
#: details: the flow detects provider ``none`` for them and hides the filters
#: (01-configuration.md#warning-sources-config-subentries).
NO_DETAILS_PLATFORMS: Final = frozenset(
    {
        "template",
        "group",
        "threshold",
        "input_boolean",
        "switch_as_x",
        "trend",
        "bayesian",
        "min_max",
    }
)
NO_DETAILS_DOMAINS: Final = frozenset({"input_boolean", "switch"})
#: Device classes of indoor detectors: on/off only, no details.
NO_DETAILS_DEVICE_CLASSES: Final = frozenset({"smoke", "carbon_monoxide", "gas"})


def runtime_provider(hass: HomeAssistant, entity_id: str, details: str) -> str:
    """Return the provider used at runtime for an entity of a source.

    ``details`` is the subentry option: ``none`` and ``attributes`` force the
    provider; ``auto`` uses the registry platform, else ``attributes`` (which
    yields no details when the entity has neither headline nor text).
    """
    if details == DETAILS_NONE:
        return PROVIDER_NONE
    if details == DETAILS_ATTRIBUTES:
        return PROVIDER_ATTRIBUTES
    entry = er.async_get(hass).async_get(entity_id)
    if entry is not None and entry.platform in REGISTRY:
        return entry.platform
    return PROVIDER_ATTRIBUTES


def detect_provider(hass: HomeAssistant, entity_ids: Iterable[str]) -> str:
    """Return the provider ``auto`` picks for a new source, for the flow.

    The registry platform of the first entity with a registered provider wins.
    Helpers (template, group, threshold, ``input_boolean``, ``switch``) and
    indoor detectors (device class smoke, CO, gas) are ``none``; every other
    entity is ``attributes``.
    """
    registry = er.async_get(hass)
    entity_ids = list(entity_ids)
    for entity_id in entity_ids:
        entry = registry.async_get(entity_id)
        if entry is not None and entry.platform in REGISTRY:
            return entry.platform
    for entity_id in entity_ids:
        entry = registry.async_get(entity_id)
        state = hass.states.get(entity_id)
        device_class = (
            state.attributes.get("device_class") if state is not None else None
        ) or (
            (entry.device_class or entry.original_device_class)
            if entry is not None
            else None
        )
        if (
            entity_id.partition(".")[0] in NO_DETAILS_DOMAINS
            or (entry is not None and entry.platform in NO_DETAILS_PLATFORMS)
            or device_class in NO_DETAILS_DEVICE_CLASSES
        ):
            continue
        return PROVIDER_ATTRIBUTES
    return PROVIDER_NONE


def device_entities(hass: HomeAssistant, device_id: str) -> tuple[str, ...]:
    """Return the enabled ``binary_sensor`` entities of a source device, sorted."""
    registry = er.async_get(hass)
    return tuple(
        sorted(
            entry.entity_id
            for entry in er.async_entries_for_device(registry, device_id)
            if entry.domain == "binary_sensor" and entry.disabled_by is None
        )
    )


def source_entities(hass: HomeAssistant, data: Mapping[str, Any]) -> tuple[str, ...]:
    """Return the entities of a warning source subentry (device expanded)."""
    if entity_id := data.get(CONF_SOURCE_ENTITY):
        return (str(entity_id),)
    if device_id := data.get(CONF_SOURCE_DEVICE):
        return device_entities(hass, str(device_id))
    return ()


@dataclass(frozen=True, kw_only=True)
class CollectedDetails:
    """Details of all active source entities for one snapshot."""

    details: Mapping[str, tuple[WarningDetails, ...]] = field(default_factory=dict)
    failed: frozenset[str] = frozenset()


class DetailsCollector:
    """Calls the providers of all active source entities (controller glue).

    One provider instance per name lives as long as the controller, so the
    NINA cache survives evaluations. All calls run concurrently; each is
    cancelled shortly after :data:`DETAILS_TIMEOUT`, so an evaluation never
    waits much longer than that.
    """

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the collector."""
        self.hass = hass
        self._providers: dict[str, WarningProvider] = {}
        self._failing: set[str] = set()

    def provider(self, name: str) -> WarningProvider:
        """Return the provider instance for ``name``."""
        if (provider := self._providers.get(name)) is None:
            provider = self._providers[name] = PROVIDERS[name]()
        return provider

    async def async_collect(self, sources: Iterable[WarningSource]) -> CollectedDetails:
        """Return the details of every active entity whose provider has any."""
        jobs: dict[str, tuple[WarningProvider, State]] = {}
        for source in sources:
            if source.details == DETAILS_NONE:
                continue
            for entity_id in source.entities:
                state = self.hass.states.get(entity_id)
                if state is None or not entity_active(
                    source, InputValue(state=state.state)
                ):
                    continue
                name = runtime_provider(self.hass, entity_id, source.details)
                if name != PROVIDER_NONE:
                    jobs[entity_id] = (self.provider(name), state)
        if not jobs:
            return CollectedDetails()
        results = await asyncio.gather(
            *(
                self._async_call(provider, entity_id, state)
                for entity_id, (provider, state) in jobs.items()
            )
        )
        details: dict[str, tuple[WarningDetails, ...]] = {}
        failed: set[str] = set()
        for entity_id, result in zip(jobs, results, strict=True):
            if result is None:
                failed.add(entity_id)
            elif result:
                details[entity_id] = tuple(result)
        self._log_failures(failed)
        return CollectedDetails(details=details, failed=frozenset(failed))

    async def _async_call(
        self, provider: WarningProvider, entity_id: str, state: State
    ) -> list[WarningDetails] | None:
        """Call one provider; None if it failed or timed out (fail-safe)."""
        try:
            async with asyncio.timeout(_GUARD.total_seconds()):
                return await provider.async_get_details(self.hass, entity_id, state)
        except DetailsError as err:
            _LOGGER.debug("Details of %s failed: %s", entity_id, err)
        except TimeoutError:
            _LOGGER.debug("Details of %s timed out", entity_id)
        except Exception:  # a broken provider must not stop the safety shutdown
            _LOGGER.exception("Details provider %s failed", provider.name)
        return None

    def _log_failures(self, failed: set[str]) -> None:
        """Log once when details of an entity start and stop failing."""
        for entity_id in sorted(failed - self._failing):
            _LOGGER.info(
                "Warning details of %s are not available; it counts as a "
                "warning without details",
                entity_id,
            )
        for entity_id in sorted(self._failing - failed):
            _LOGGER.info("Warning details of %s are available again", entity_id)
        self._failing = failed


__all__ = [
    "DETAILS_TIMEOUT",
    "PROVIDERS",
    "REGISTRY",
    "CollectedDetails",
    "DetailsCollector",
    "DetailsError",
    "NoDetailsProvider",
    "WarningProvider",
    "detect_provider",
    "device_entities",
    "runtime_provider",
    "source_entities",
]
