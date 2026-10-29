"""Shared constants and helpers of the component tests."""

from __future__ import annotations

from datetime import timedelta
import inspect
from typing import Any, ClassVar

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import (
    CALLBACK_TYPE,
    HomeAssistant,
    Service,
    ServiceCall,
    callback,
)
from homeassistant.data_entry_flow import section
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_call_later
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.better_home_ventilation.config_flow import (
    BetterHomeVentilationConfigFlow,
)
from custom_components.better_home_ventilation.const import (
    ACTUATOR_UNIT_FLOW,
    CONF_ACTUATOR,
    CONF_ACTUATOR_UNIT,
    CONF_CO2_SENSORS,
    CONF_HUMIDITY_SENSORS,
    CONF_MAX_FLOW,
    CONF_NOTIFY_SERVICES,
    CONF_PRESENCE_ENTITIES,
    DEFAULTS,
    DOMAIN,
    defaults_for_max_flow,
)

# Neutral example entities (no real installation).
NUMBER_FLOW = "number.unit_flow"
NUMBER_PERCENT = "number.unit_percent"
NUMBER_RAW = "number.unit_raw"
FAN = "fan.unit"
SELECT_STAGES = "select.unit_stage"
SELECT_SINGLE = "select.unit_single"
CO2_LIVING = "sensor.living_room_co2"
CO2_BEDROOM = "sensor.bedroom_co2"
HUMIDITY_LIVING = "sensor.living_room_humidity"
HUMIDITY_BEDROOM = "sensor.bedroom_humidity"
PERSON = "person.resident"
STANDBY_SWITCH = "switch.unit_standby"
CONTROL_MODE = "select.unit_control_mode"

STAGE_OPTIONS = ["Off", "Stage 1", "Stage 2", "Stage 3"]


def entry_options(*remove: str, **overrides: Any) -> dict[str, Any]:
    """Return complete options of an entry as the config flow creates them."""
    options = {
        **DEFAULTS,
        **defaults_for_max_flow(400),
        CONF_ACTUATOR: NUMBER_FLOW,
        CONF_MAX_FLOW: 400,
        CONF_ACTUATOR_UNIT: ACTUATOR_UNIT_FLOW,
        CONF_CO2_SENSORS: [CO2_LIVING],
        CONF_HUMIDITY_SENSORS: [HUMIDITY_LIVING],
        CONF_PRESENCE_ENTITIES: [],
        CONF_NOTIFY_SERVICES: [],
        **overrides,
    }
    for key in remove:
        del options[key]
    return options


def mock_entry(*remove: str, **overrides: Any) -> MockConfigEntry:
    """Return a config entry of the current version with complete options."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Ventilation",
        data={},
        options=entry_options(*remove, **overrides),
        version=BetterHomeVentilationConfigFlow.VERSION,
        minor_version=BetterHomeVentilationConfigFlow.MINOR_VERSION,
    )


async def advance(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: float
) -> None:
    """Move the clock forward, fire due timers and wait until HA is idle."""
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


class MockUnit:
    """Service handlers that stand in for a ventilation unit and its helpers.

    Every handled call sets the entity state with the caller's context, like a
    real entity does. Entities in ``ignore`` accept calls without changing
    their state; calls for entities in ``fail`` raise ``HomeAssistantError``.
    Entities in ``delay`` take a call at once but report it only after the
    given number of seconds and with a new context, like a device that is
    polled (for example an ESPHome Modbus device).
    """

    SERVICES: ClassVar[tuple[tuple[str, str], ...]] = (
        ("number", "set_value"),
        ("select", "select_option"),
        ("input_select", "select_option"),
        ("fan", "set_percentage"),
        ("fan", "turn_on"),
        ("fan", "turn_off"),
        ("switch", "turn_on"),
        ("switch", "turn_off"),
        ("input_boolean", "turn_on"),
        ("input_boolean", "turn_off"),
    )

    def __init__(self, hass: HomeAssistant) -> None:
        """Register the service handlers.

        Services that already exist (the entity components of the
        integration's own platforms) are kept for entities with a registry
        entry: only the stand-in states are simulated.
        """
        self.hass = hass
        self.calls: list[ServiceCall] = []
        self.ignore: set[str] = set()
        self.fail: set[str] = set()
        self.delay: dict[str, float] = {}
        self._pending: list[CALLBACK_TYPE] = []
        # State of delayed entities on the device, before HA learns it.
        self._device: dict[str, tuple[str, dict[str, Any]]] = {}
        self._originals: dict[tuple[str, str], Service] = {}
        for domain, service in self.SERVICES:
            if original := hass.services.async_services_for_domain(domain).get(service):
                self._originals[domain, service] = original
            hass.services.async_register(domain, service, self._handle)

    async def _delegate(self, original: Service, call: ServiceCall) -> None:
        """Run the original handler of a service (for real entities)."""
        data = original.schema(dict(call.data)) if original.schema else call.data
        result = original.job.target(
            ServiceCall(self.hass, call.domain, call.service, data, call.context)
        )
        if inspect.isawaitable(result):
            await result

    @callback
    def async_cancel(self) -> None:
        """Cancel the delayed confirmations that are still pending."""
        pending, self._pending = self._pending, []
        for unsub in pending:
            unsub()

    def calls_for(self, domain: str, service: str) -> list[ServiceCall]:
        """Return the calls of one service."""
        return [
            call
            for call in self.calls
            if call.domain == domain and call.service == service
        ]

    async def _handle(self, call: ServiceCall) -> None:
        entity_id = call.data["entity_id"]
        original = self._originals.get((call.domain, call.service))
        registry_entry = er.async_get(self.hass).async_get(
            entity_id if isinstance(entity_id, str) else entity_id[0]
        )
        if (
            original is not None
            and registry_entry is not None
            and registry_entry.platform == DOMAIN
        ):
            await self._delegate(original, call)
            return
        self.calls.append(call)
        if entity_id in self.fail:
            raise HomeAssistantError("unit does not answer")
        if entity_id in self.ignore:
            return
        state = self.hass.states.get(entity_id)
        attributes = dict(state.attributes) if state is not None else {}
        if call.service == "set_value":
            new_state = str(call.data["value"])
        elif call.service == "select_option":
            new_state = call.data["option"]
        elif call.service == "set_percentage":
            attributes["percentage"] = call.data["percentage"]
            new_state = "on" if call.data["percentage"] else "off"
        else:
            new_state = "on" if call.service == "turn_on" else "off"
        if (delay := self.delay.get(entity_id)) is not None:
            self._device[entity_id] = (new_state, attributes)

            @callback
            def confirm(_now: Any) -> None:
                # The poll reports what the device shows now, with a new context.
                self.hass.states.async_set(entity_id, *self._device[entity_id])

            self._pending.append(async_call_later(self.hass, delay, confirm))
            return
        self.hass.states.async_set(
            entity_id, new_state, attributes, context=call.context
        )


def schema_fields(schema: Any) -> dict[str, tuple[str | None, Any, Any]]:
    """Return every field of a form schema: key -> (section, marker, selector)."""
    fields: dict[str, tuple[str | None, Any, Any]] = {}
    for marker, value in schema.schema.items():
        if isinstance(value, section):
            for inner, inner_value in value.schema.schema.items():
                fields[str(inner.schema)] = (str(marker.schema), inner, inner_value)
        else:
            fields[str(marker.schema)] = (None, marker, value)
    return fields


def suggested(schema: Any, key: str) -> Any:
    """Return the suggested value of a field of a form schema."""
    marker = schema_fields(schema)[key][1]
    return (marker.description or {}).get("suggested_value")


def sections(schema: Any) -> dict[str, Any]:
    """Return the sections of a form schema by name."""
    return {
        str(marker.schema): value
        for marker, value in schema.schema.items()
        if isinstance(value, section)
    }
