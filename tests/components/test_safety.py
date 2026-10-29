"""Safety shutdown end to end: sources, entities, override (BHV-40-45, C11a).

docs/spec/03-modes.md#safety-shutdown, 04-interface.md#entities.
"""

from __future__ import annotations

from typing import Any

from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.better_home_ventilation.const import (
    CONF_DETAILS,
    CONF_EXCLUDE_KEYWORDS,
    CONF_HEADLINE_KEYWORDS,
    CONF_MIN_SEVERITY,
    CONF_NAME,
    CONF_SOURCE_DEVICE,
    CONF_SOURCE_ENTITY,
    CONF_TEXT_KEYWORDS,
    CONF_THRESHOLD,
    CONF_TRIGGER,
    DOMAIN,
    SUBENTRY_WARNING_SOURCE,
)
from custom_components.better_home_ventilation.engine.models import (
    Mode,
    ProblemKey,
)

from .common import STANDBY_SWITCH, MockUnit, advance
from .test_controller import controller_of, problem_keys, setup_entry

SMOKE = "binary_sensor.example_outdoor_smoke"
LEVEL = "sensor.example_warning_level"
NINA_1 = "binary_sensor.example_city_warning_1"
NINA_2 = "binary_sensor.example_city_warning_2"
NINA_3 = "binary_sensor.example_city_warning_3"
SAFETY_WARNING = "binary_sensor.ventilation_safety_warning"
SAFETY_OVERRIDE = "switch.ventilation_safety_override"
MODE = "sensor.ventilation_mode"


@pytest.fixture(autouse=True)
def daytime(freezer: FrozenDateTimeFactory) -> None:
    """Run every test at 13:00 local time, outside the night window."""
    freezer.move_to("2026-10-14T20:00:00+00:00")


def source(
    *,
    entity_id: str | None = None,
    device_id: str | None = None,
    name: str = "Example source",
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the data of a warning source subentry."""
    key = CONF_SOURCE_ENTITY if entity_id else CONF_SOURCE_DEVICE
    value = entity_id or device_id
    return {
        "data": {
            key: value,
            CONF_NAME: name,
            CONF_TRIGGER: "on_off",
            CONF_DETAILS: "auto",
            CONF_HEADLINE_KEYWORDS: [],
            CONF_TEXT_KEYWORDS: [],
            CONF_EXCLUDE_KEYWORDS: [],
            CONF_MIN_SEVERITY: "none",
            **(data or {}),
        },
        "subentry_type": SUBENTRY_WARNING_SOURCE,
        "title": name,
        "unique_id": value,
    }


def mode_of(hass: HomeAssistant) -> str:
    """Return the state of the mode sensor."""
    state = hass.states.get(MODE)
    assert state is not None
    return state.state


async def set_state(
    hass: HomeAssistant,
    entity_id: str,
    state: str,
    attributes: dict[str, Any] | None = None,
) -> None:
    """Set a state and let the controller evaluate."""
    hass.states.async_set(entity_id, state, attributes or {})
    await hass.async_block_till_done()


async def turn(hass: HomeAssistant, service: str) -> None:
    """Turn the safety override switch on or off."""
    await hass.services.async_call(
        "switch", service, {ATTR_ENTITY_ID: SAFETY_OVERRIDE}, blocking=True
    )
    await hass.async_block_till_done()


# --- Entities -------------------------------------------------------------------------


@pytest.mark.usefixtures("entities")
async def test_safety_entities_only_with_warning_sources(
    hass: HomeAssistant, unit: MockUnit
) -> None:
    """Without warning sources there is no safety warning or override entity."""
    await setup_entry(hass)
    assert hass.states.get(SAFETY_WARNING) is None
    assert hass.states.get(SAFETY_OVERRIDE) is None


@pytest.mark.usefixtures("entities")
@pytest.mark.parametrize(
    ("language", "warning", "override"),
    [
        ("en", "Safety warning", "Safety override"),
        ("de", "Sicherheitswarnung", "Warnung übersteuern"),
    ],
)
async def test_safety_entities_are_translated(
    hass: HomeAssistant, unit: MockUnit, language: str, warning: str, override: str
) -> None:
    """Both entities exist with a warning source, named in the server language."""
    hass.config.language = language
    hass.states.async_set(SMOKE, "off")
    entry = await setup_entry(hass, subentries=[source(entity_id=SMOKE)])
    registry = er.async_get(hass)
    for platform, key, name in (
        ("binary_sensor", "safety_warning", warning),
        ("switch", "safety_override", override),
    ):
        entity_id = registry.async_get_entity_id(
            platform, DOMAIN, f"{entry.entry_id}_{key}"
        )
        assert entity_id is not None
        state = hass.states.get(entity_id)
        assert state is not None
        assert state.name == f"Ventilation {name}"
        assert state.state == "off"
    state = hass.states.get(
        registry.async_get_entity_id(
            "binary_sensor", DOMAIN, f"{entry.entry_id}_safety_warning"
        )
        or ""
    )
    assert state is not None
    assert state.attributes["device_class"] == "safety"
    assert state.attributes["warnings"] == []


@pytest.mark.usefixtures("entities")
async def test_safety_entities_removed_with_the_last_source(
    hass: HomeAssistant, unit: MockUnit
) -> None:
    """Removing the last warning source removes the safety entities (reload)."""
    hass.states.async_set(SMOKE, "off")
    entry = await setup_entry(hass, subentries=[source(entity_id=SMOKE)])
    registry = er.async_get(hass)
    assert registry.async_get(SAFETY_WARNING) is not None
    subentry_id = next(iter(entry.subentries))

    assert hass.config_entries.async_remove_subentry(entry, subentry_id)
    await hass.async_block_till_done()

    assert registry.async_get(SAFETY_WARNING) is None
    assert registry.async_get(SAFETY_OVERRIDE) is None
    assert hass.states.get(SAFETY_WARNING) is None


# --- End to end with a template binary sensor ----------------------------------------------


@pytest.mark.usefixtures("entities")
async def test_bhv40_template_source_shuts_down_and_override(
    hass: HomeAssistant, unit: MockUnit, freezer: FrozenDateTimeFactory
) -> None:
    """Warning on → safety_off with the off actuator; override via switch and service."""
    hass.states.async_set(SMOKE, "off")
    entry = await setup_entry(
        hass,
        subentries=[source(entity_id=SMOKE, name="Outdoor smoke")],
        off_entity=STANDBY_SWITCH,
        off_state="on",
    )
    controller = controller_of(entry)
    await advance(hass, freezer, 61)
    assert mode_of(hass) == "automatic"

    await set_state(hass, SMOKE, "on")

    assert mode_of(hass) == "safety_off"
    assert unit.calls_for("switch", "turn_on")[-1].data == {"entity_id": STANDBY_SWITCH}
    warning = hass.states.get(SAFETY_WARNING)
    assert warning is not None
    assert warning.state == "on"
    [item] = warning.attributes["warnings"]
    assert item["source"] == "Outdoor smoke"
    assert item["headline"] is None
    assert item["severity"] is None
    assert item["overridden"] is False
    assert item["since"] is not None
    override = hass.states.get(SAFETY_OVERRIDE)
    assert override is not None
    assert override.state == "off"

    # Override with the switch ("Keep ventilating").
    await turn(hass, "turn_on")
    assert mode_of(hass) == "automatic"
    assert hass.states.get(SAFETY_OVERRIDE).state == "on"  # type: ignore[union-attr]
    assert unit.calls_for("switch", "turn_off")[-1].data == {
        "entity_id": STANDBY_SWITCH
    }
    warning = hass.states.get(SAFETY_WARNING)
    assert warning is not None
    assert warning.state == "on"
    assert warning.attributes["warnings"][0]["overridden"] is True

    # Removing the override shuts down again.
    await turn(hass, "turn_off")
    assert mode_of(hass) == "safety_off"
    assert hass.states.get(SAFETY_OVERRIDE).state == "off"  # type: ignore[union-attr]

    # Override with the service.
    await hass.services.async_call(DOMAIN, "override_safety", {}, blocking=True)
    await hass.async_block_till_done()
    assert mode_of(hass) == "automatic"
    assert controller.state.safety.overridden

    # The overridden warning ends: no restart wait (the unit was running).
    await set_state(hass, SMOKE, "off")
    assert mode_of(hass) == "automatic"
    assert hass.states.get(SAFETY_WARNING).state == "off"  # type: ignore[union-attr]
    assert hass.states.get(SAFETY_OVERRIDE).state == "off"  # type: ignore[union-attr]


@pytest.mark.usefixtures("entities")
async def test_bhv43_warning_ends_restart_wait(
    hass: HomeAssistant, unit: MockUnit, freezer: FrozenDateTimeFactory
) -> None:
    """When the warning ends the unit waits for the restart confirmation."""
    hass.states.async_set(SMOKE, "off")
    entry = await setup_entry(hass, subentries=[source(entity_id=SMOKE)])
    controller = controller_of(entry)
    await advance(hass, freezer, 61)
    await set_state(hass, SMOKE, "on")
    assert mode_of(hass) == "safety_off"

    await set_state(hass, SMOKE, "off")

    decision = controller.current_decision
    assert decision is not None
    assert decision.mode is Mode.SAFETY_OFF
    assert decision.awaiting_restart
    assert hass.states.get(SAFETY_WARNING).state == "off"  # type: ignore[union-attr]
    await advance(hass, freezer, 30 * 60 + 1)
    # Restarted (the room sensors are stale by now, hence fallback).
    assert mode_of(hass) == "fallback"


@pytest.mark.usefixtures("entities")
async def test_override_without_warning(hass: HomeAssistant, unit: MockUnit) -> None:
    """The service raises `no_active_warning`; the switch just stays off."""
    hass.states.async_set(SMOKE, "off")
    await setup_entry(hass, subentries=[source(entity_id=SMOKE)])

    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(DOMAIN, "override_safety", {}, blocking=True)
    assert err.value.translation_key == "no_active_warning"

    await turn(hass, "turn_on")
    assert hass.states.get(SAFETY_OVERRIDE).state == "off"  # type: ignore[union-attr]
    await turn(hass, "turn_off")
    assert hass.states.get(SAFETY_OVERRIDE).state == "off"  # type: ignore[union-attr]


@pytest.mark.usefixtures("entities")
async def test_numeric_source_and_unavailable_problem(
    hass: HomeAssistant, unit: MockUnit, freezer: FrozenDateTimeFactory
) -> None:
    """A numeric sensor triggers at its threshold; unavailable raises a problem."""
    hass.states.async_set(LEVEL, "2")
    entry = await setup_entry(
        hass,
        subentries=[
            source(entity_id=LEVEL, data={CONF_TRIGGER: "numeric", CONF_THRESHOLD: 3})
        ],
    )
    controller = controller_of(entry)
    await advance(hass, freezer, 61)
    assert mode_of(hass) == "automatic"
    await set_state(hass, LEVEL, "3")
    assert mode_of(hass) == "safety_off"
    await set_state(hass, LEVEL, "unavailable")
    assert (str(ProblemKey.WARNING_SOURCE_UNAVAILABLE), LEVEL) in problem_keys(
        controller
    )


@pytest.mark.usefixtures("entities")
async def test_source_entity_rename_is_followed(
    hass: HomeAssistant, unit: MockUnit
) -> None:
    """A renamed source entity is written to the subentry (unique id too)."""
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "binary_sensor",
        "template",
        "outdoor_smoke",
        suggested_object_id="example_outdoor_smoke",
    )
    hass.states.async_set(SMOKE, "off")
    entry = await setup_entry(hass, subentries=[source(entity_id=SMOKE)])

    registry.async_update_entity(
        SMOKE, new_entity_id="binary_sensor.example_smoke_renamed"
    )
    await hass.async_block_till_done()

    subentry = next(iter(entry.subentries.values()))
    assert subentry.data[CONF_SOURCE_ENTITY] == "binary_sensor.example_smoke_renamed"
    assert subentry.unique_id == "binary_sensor.example_smoke_renamed"


# --- NINA device source (C11a) ------------------------------------------------------------


def nina_details(headline: str, warning_id: str) -> dict[str, Any]:
    """Return a `nina.get_details` response of one slot."""
    return {
        "headline": headline,
        "description": "Es kommt zu starker Rauchentwicklung.",
        "sender": "Example Fire Brigade",
        "severity": "Severe",
        "recommended_actions": "Schalten Sie Lüftungs- und Klimaanlagen ab.",
        "id": warning_id,
    }


@pytest.fixture
def nina(
    hass: HomeAssistant,
) -> tuple[str, MockConfigEntry, dict[str, dict[str, Any]], list[ServiceCall]]:
    """Create a NINA device with two slots and a stand-in for `nina.get_details`."""
    nina_entry = MockConfigEntry(domain="nina", title="Example City")
    nina_entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=nina_entry.entry_id,
        identifiers={("nina", "example_region")},
        name="Example City",
    )
    registry = er.async_get(hass)
    for slot, entity_id in ((1, NINA_1), (2, NINA_2)):
        registry.async_get_or_create(
            "binary_sensor",
            "nina",
            f"example_region-{slot}",
            config_entry=nina_entry,
            device_id=device.id,
            suggested_object_id=entity_id.partition(".")[2],
        )
        hass.states.async_set(entity_id, "off", {"device_class": "safety"})
    responses: dict[str, dict[str, Any]] = {}
    calls: list[ServiceCall] = []

    async def handler(call: ServiceCall) -> ServiceResponse:
        calls.append(call)
        entity_id = call.data["entity_id"]
        return {entity_id: responses.get(entity_id)}

    hass.services.async_register(
        "nina", "get_details", handler, supports_response=SupportsResponse.ONLY
    )
    return device.id, nina_entry, responses, calls


@pytest.mark.usefixtures("entities")
async def test_c11a_nina_device_source(
    hass: HomeAssistant,
    unit: MockUnit,
    freezer: FrozenDateTimeFactory,
    nina: tuple[str, MockConfigEntry, dict[str, dict[str, Any]], list[ServiceCall]],
) -> None:
    """All slots count; details come from `nina.get_details`; filters apply."""
    device_id, nina_entry, responses, calls = nina
    entry = await setup_entry(
        hass,
        subentries=[
            source(
                device_id=device_id,
                name="Example City",
                data={
                    CONF_HEADLINE_KEYWORDS: ["/brand(?!enburg)/"],
                    CONF_EXCLUDE_KEYWORDS: ["Entwarnung"],
                },
            )
        ],
    )
    controller = controller_of(entry)
    [nina_source] = controller.options.warning_sources
    assert nina_source.entities == (NINA_1, NINA_2)
    await advance(hass, freezer, 61)

    # An all-clear message in slot 1 does not count.
    responses[NINA_1] = nina_details("Entwarnung: Großbrand - Example City", "w.0")
    await set_state(hass, NINA_1, "on", {"id": "w.0"})
    assert len(calls) == 1
    assert mode_of(hass) == "automatic"
    assert hass.states.get(SAFETY_WARNING).state == "off"  # type: ignore[union-attr]

    # A fire warning in slot 2 shuts down.
    responses[NINA_2] = nina_details("Großbrand - Example City", "w.1")
    await set_state(hass, NINA_2, "on", {"id": "w.1"})
    assert mode_of(hass) == "safety_off"
    warning = hass.states.get(SAFETY_WARNING)
    assert warning is not None
    [item] = warning.attributes["warnings"]
    assert item["headline"] == "Großbrand - Example City"
    assert item["severity"] == "severe"
    assert item["source"] == "Example City"

    # A new slot is added to the device: picked up without reload.
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "binary_sensor",
        "nina",
        "example_region-3",
        config_entry=nina_entry,
        device_id=device_id,
        suggested_object_id="example_city_warning_3",
    )
    await hass.async_block_till_done()
    [nina_source] = controller.options.warning_sources
    assert nina_source.entities == (NINA_1, NINA_2, NINA_3)
    responses[NINA_3] = nina_details("Gefahrstoffaustritt - Example City", "w.2")
    await set_state(hass, NINA_3, "on", {"id": "w.2"})
    warning = hass.states.get(SAFETY_WARNING)
    assert warning is not None
    assert len(warning.attributes["warnings"]) == 1  # no keyword for Gefahrstoff
    calls_before = len(calls)

    # The fire warning moves from slot 2 to slot 1: cached, no new call.
    await set_state(hass, NINA_2, "off")
    await set_state(hass, NINA_1, "on", {"id": "w.1"})
    assert len(calls) == calls_before
    assert mode_of(hass) == "safety_off"

    # A disabled slot is no longer part of the source.
    registry.async_update_entity(NINA_3, disabled_by=er.RegistryEntryDisabler.USER)
    await hass.async_block_till_done()
    [nina_source] = controller.options.warning_sources
    assert nina_source.entities == (NINA_1, NINA_2)


@pytest.mark.usefixtures("entities")
async def test_e30c_details_failure_counts_and_raises_problem(
    hass: HomeAssistant,
    unit: MockUnit,
    freezer: FrozenDateTimeFactory,
    nina: tuple[str, MockConfigEntry, dict[str, dict[str, Any]], list[ServiceCall]],
) -> None:
    """A failing `nina.get_details` shuts down without details (fail-safe)."""
    device_id, _, _, _ = nina

    async def failing(call: ServiceCall) -> ServiceResponse:
        raise ServiceValidationError("not reachable")

    hass.services.async_register(
        "nina", "get_details", failing, supports_response=SupportsResponse.ONLY
    )
    entry = await setup_entry(
        hass,
        subentries=[
            source(device_id=device_id, data={CONF_EXCLUDE_KEYWORDS: ["Entwarnung"]})
        ],
    )
    controller = controller_of(entry)
    await advance(hass, freezer, 61)

    await set_state(hass, NINA_1, "on", {"id": "w.9"})

    assert mode_of(hass) == "safety_off"
    assert (str(ProblemKey.WARNING_DETAILS_FAILED), NINA_1) in problem_keys(controller)
