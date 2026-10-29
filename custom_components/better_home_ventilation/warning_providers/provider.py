"""Shared helpers of the details providers."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import timedelta
from typing import Any, Final

from homeassistant.exceptions import HomeAssistantError

#: A details call that takes longer fails (03-modes.md#details-providers).
DETAILS_TIMEOUT: Final = timedelta(seconds=10)


class DetailsError(HomeAssistantError):
    """The details of an active entity could not be fetched (fail-safe).

    The entity then counts as one warning without details and the problem
    ``warning_details_failed`` is raised.
    """


def first_present(attributes: Mapping[str, Any], names: Iterable[str]) -> Any:
    """Return the value of the first attribute that is present and not empty."""
    for name in names:
        value = attributes.get(name)
        if value is not None and value != "":
            return value
    return None
