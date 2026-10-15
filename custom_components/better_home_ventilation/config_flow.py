"""Config flow for Better Home Ventilation.

Skeleton only: the manifest declares ``config_flow: true``, which hassfest
requires to be backed by this module. The steps (``user``, ``actuator``,
``inputs``) and the options flow follow in milestone M3
(docs/spec/01-configuration.md).
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigFlow

from .const import DOMAIN


class BetterHomeVentilationConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the config flow for Better Home Ventilation."""

    VERSION = 1
    MINOR_VERSION = 1
