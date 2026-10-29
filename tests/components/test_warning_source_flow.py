"""Warning source subentries (C11b): BHV-40, BHV-45, BHV-53.

docs/spec/01-configuration.md#warning-sources-config-subentries.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock, patch

from homeassistant.config_entries import (
    SOURCE_USER,
    ConfigEntryState,
    ConfigSubentry,
    SubentryFlowResult,
)
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import (
    device_registry as dr,
    entity_registry as er,
    selector,
)
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
    CONF_STATES,
    CONF_TEXT_KEYWORDS,
    CONF_THRESHOLD,
    CONF_TRIGGER,
    SUBENTRY_WARNING_SOURCE,
)
from custom_components.better_home_ventilation.engine.safety import (
    NINA_DE,
    NINA_OTHER,
)

from .common import schema_fields, sections, suggested

SMOKE = "binary_sensor.example_smoke_detector"
DANGER = "input_boolean.example_danger"
NINA_1 = "binary_sensor.example_city_warning_1"
NINA_2 = "binary_sensor.example_city_warning_2"
DWD = "sensor.example_district_current_warning_level"
COLOUR = "sensor.example_region_weather_alert"
LEVEL_SELECT = "select.example_alert_level"
METEOALARM = "binary_sensor.example_region_meteoalarm"


@pytest.fixture
def reload_spy(hass: HomeAssistant) -> Iterator[MagicMock]:
    """Spy on reloads scheduled by the update listener (the reload still runs)."""
    with patch.object(
        hass.config_entries,
        "async_schedule_reload",
        wraps=hass.config_entries.async_schedule_reload,
    ) as spy:
        yield spy


@pytest.fixture
def nina_device(hass: HomeAssistant) -> str:
    """Create a NINA region device with two message slots; return its id."""
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
    return device.id


@pytest.fixture(autouse=True)
def source_entities(hass: HomeAssistant, entities: None) -> None:
    """Create entities usable as warning sources."""
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "binary_sensor",
        "template",
        "example_smoke",
        suggested_object_id="example_smoke_detector",
    )
    hass.states.async_set(
        SMOKE, "off", {"device_class": "smoke", "friendly_name": "Example smoke"}
    )
    hass.states.async_set(DANGER, "off", {"friendly_name": "Example danger"})
    registry.async_get_or_create(
        "sensor",
        "dwd_weather_warnings",
        "example_district_current",
        suggested_object_id="example_district_current_warning_level",
    )
    hass.states.async_set(DWD, "0", {"friendly_name": "Example warning level"})
    hass.states.async_set(COLOUR, "Vert", {"friendly_name": "Example weather alert"})
    hass.states.async_set(
        LEVEL_SELECT, "green", {"options": ["green", "yellow", "orange", "red"]}
    )
    registry.async_get_or_create(
        "binary_sensor",
        "meteoalarm",
        "example_region",
        suggested_object_id="example_region_meteoalarm",
    )
    hass.states.async_set(METEOALARM, "off", {"device_class": "safety"})


async def _start(hass: HomeAssistant, entry: MockConfigEntry) -> SubentryFlowResult:
    """Start adding a warning source."""
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_WARNING_SOURCE),
        context={"source": SOURCE_USER},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    return result


async def _configure(
    hass: HomeAssistant, result: SubentryFlowResult, user_input: dict[str, Any]
) -> SubentryFlowResult:
    """Submit a subentry flow step."""
    return await hass.config_entries.subentries.async_configure(
        result["flow_id"], user_input
    )


def _only_subentry(entry: MockConfigEntry) -> ConfigSubentry:
    """Return the single warning source of ``entry``."""
    sources = [
        subentry
        for subentry in entry.subentries.values()
        if subentry.subentry_type == SUBENTRY_WARNING_SOURCE
    ]
    assert len(sources) == 1
    return sources[0]


async def test_bhv40_add_smoke_detector_without_filters(
    hass: HomeAssistant, entry: MockConfigEntry, reload_spy: MagicMock
) -> None:
    """A smoke detector skips the trigger step and shows no keyword filters."""
    result = await _configure(
        hass, await _start(hass, entry), {CONF_SOURCE_ENTITY: SMOKE}
    )

    assert result["step_id"] == "filters"
    fields = schema_fields(result["data_schema"])
    assert set(fields) == {CONF_DETAILS}
    placeholders = result["description_placeholders"] or {}
    assert placeholders["name"] == "Example smoke"
    assert placeholders["provider"].startswith("none")

    result = await _configure(hass, result, {CONF_DETAILS: "auto"})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    subentry = _only_subentry(entry)
    assert subentry.title == "Example smoke"
    assert subentry.unique_id == SMOKE
    assert dict(subentry.data) == {
        CONF_SOURCE_ENTITY: SMOKE,
        CONF_NAME: "Example smoke",
        CONF_TRIGGER: "on_off",
        CONF_DETAILS: "auto",
        CONF_HEADLINE_KEYWORDS: [],
        CONF_TEXT_KEYWORDS: [],
        CONF_EXCLUDE_KEYWORDS: [],
        CONF_MIN_SEVERITY: "none",
    }
    reload_spy.assert_called_once_with(entry.entry_id)
    assert entry.state is ConfigEntryState.LOADED


async def test_input_boolean_with_name(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """An own name wins over the friendly name; input_boolean has no trigger step."""
    result = await _configure(
        hass,
        await _start(hass, entry),
        {CONF_SOURCE_ENTITY: DANGER, CONF_NAME: "  Manual danger  "},
    )
    assert result["step_id"] == "filters"
    result = await _configure(hass, result, {CONF_DETAILS: "none"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert _only_subentry(entry).title == "Manual danger"


@pytest.mark.parametrize(
    ("language", "expected"), [("de", NINA_DE), ("en", NINA_OTHER), ("fr", NINA_OTHER)]
)
async def test_bhv45_nina_device_with_suggested_filters(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    nina_device: str,
    language: str,
    expected: Any,
) -> None:
    """A NINA device is detected and gets the keywords of the server language."""
    hass.config.language = language
    result = await _configure(
        hass, await _start(hass, entry), {CONF_SOURCE_DEVICE: nina_device}
    )

    assert result["step_id"] == "filters"
    schema = result["data_schema"]
    assert "keywords" in sections(schema)
    assert not sections(schema)["keywords"].options["collapsed"]
    for key in (CONF_HEADLINE_KEYWORDS, CONF_TEXT_KEYWORDS, CONF_EXCLUDE_KEYWORDS):
        assert suggested(schema, key) == list(expected[key])
    assert suggested(schema, CONF_DETAILS) == "auto"
    assert suggested(schema, CONF_MIN_SEVERITY) == "none"
    placeholders = result["description_placeholders"] or {}
    assert placeholders["provider"].startswith("NINA")

    result = await _configure(
        hass,
        result,
        {
            CONF_DETAILS: "auto",
            "keywords": {key: list(expected[key]) for key in expected},
            CONF_MIN_SEVERITY: "none",
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    subentry = _only_subentry(entry)
    assert subentry.unique_id == nina_device
    assert subentry.title == "Example City"
    assert subentry.data[CONF_SOURCE_DEVICE] == nina_device
    assert CONF_SOURCE_ENTITY not in subentry.data
    assert subentry.data[CONF_TRIGGER] == "on_off"
    assert subentry.data[CONF_HEADLINE_KEYWORDS] == list(
        expected[CONF_HEADLINE_KEYWORDS]
    )


async def test_dwd_sensor_numeric_trigger(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """A numeric sensor gets the trigger step, pre-selected as numeric."""
    result = await _configure(
        hass, await _start(hass, entry), {CONF_SOURCE_ENTITY: DWD}
    )

    assert result["step_id"] == "trigger"
    assert suggested(result["data_schema"], CONF_TRIGGER) == "numeric"
    assert result["description_placeholders"] == {"entity": "Example warning level"}

    result = await _configure(
        hass, result, {CONF_TRIGGER: "numeric", CONF_THRESHOLD: 3.0}
    )
    assert result["step_id"] == "filters"
    schema = result["data_schema"]
    assert "keywords" in sections(schema)
    assert (result["description_placeholders"] or {})["provider"].startswith("DWD")
    for key in (CONF_HEADLINE_KEYWORDS, CONF_TEXT_KEYWORDS, CONF_EXCLUDE_KEYWORDS):
        assert suggested(schema, key) is None

    result = await _configure(
        hass,
        result,
        {CONF_DETAILS: "auto", "keywords": {}, CONF_MIN_SEVERITY: "severe"},
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    data = _only_subentry(entry).data
    assert data[CONF_TRIGGER] == "numeric"
    assert data[CONF_THRESHOLD] == 3
    assert isinstance(data[CONF_THRESHOLD], int)
    assert CONF_STATES not in data
    assert data[CONF_MIN_SEVERITY] == "severe"


async def test_state_list_trigger_for_a_select(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """A select offers its options as states (custom values allowed)."""
    result = await _configure(
        hass, await _start(hass, entry), {CONF_SOURCE_ENTITY: LEVEL_SELECT}
    )

    assert result["step_id"] == "trigger"
    schema = result["data_schema"]
    assert suggested(schema, CONF_TRIGGER) == "state_list"
    states_selector = schema_fields(schema)[CONF_STATES][2]
    assert isinstance(states_selector, selector.SelectSelector)
    assert states_selector.config["options"] == ["green", "yellow", "orange", "red"]
    assert states_selector.config["multiple"]
    assert states_selector.config["custom_value"]

    result = await _configure(
        hass, result, {CONF_TRIGGER: "state_list", CONF_STATES: ["orange", " red "]}
    )
    result = await _configure(
        hass, result, {CONF_DETAILS: "none", CONF_MIN_SEVERITY: "none"}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    data = _only_subentry(entry).data
    assert data[CONF_TRIGGER] == "state_list"
    assert data[CONF_STATES] == ["orange", "red"]
    assert CONF_THRESHOLD not in data


async def test_trigger_errors(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """`numeric_state`, `threshold_required` and `states_required`."""
    result = await _configure(
        hass, await _start(hass, entry), {CONF_SOURCE_ENTITY: COLOUR}
    )
    assert suggested(result["data_schema"], CONF_TRIGGER) == "state_list"

    result = await _configure(
        hass, result, {CONF_TRIGGER: "numeric", CONF_THRESHOLD: 2}
    )
    assert result["errors"] == {CONF_TRIGGER: "numeric_state"}
    result = await _configure(hass, result, {CONF_TRIGGER: "numeric"})
    assert result["errors"] == {CONF_THRESHOLD: "threshold_required"}
    result = await _configure(hass, result, {CONF_TRIGGER: "state_list"})
    assert result["errors"] == {CONF_STATES: "states_required"}
    result = await _configure(
        hass, result, {CONF_TRIGGER: "state_list", CONF_STATES: ["Orange", "Rouge"]}
    )
    assert result["step_id"] == "filters"


async def test_numeric_trigger_accepted_while_unavailable(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """`numeric_state` is not raised while the state is unavailable."""
    hass.states.async_set(COLOUR, "unavailable")
    result = await _configure(
        hass, await _start(hass, entry), {CONF_SOURCE_ENTITY: COLOUR}
    )
    result = await _configure(
        hass, result, {CONF_TRIGGER: "numeric", CONF_THRESHOLD: 1}
    )
    assert result["step_id"] == "filters"


@pytest.mark.parametrize(
    "user_input",
    [
        {},
        {CONF_NAME: "Only a name"},
        {CONF_SOURCE_ENTITY: SMOKE, CONF_SOURCE_DEVICE: "x"},
    ],
)
async def test_source_required(
    hass: HomeAssistant, entry: MockConfigEntry, user_input: dict[str, Any]
) -> None:
    """Exactly one of entity and device (`source_required`)."""
    result = await _configure(hass, await _start(hass, entry), user_input)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "source_required"}


async def test_duplicate_source_already_configured(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """The same entity cannot be added twice."""
    result = await _configure(
        hass, await _start(hass, entry), {CONF_SOURCE_ENTITY: SMOKE}
    )
    await _configure(hass, result, {CONF_DETAILS: "auto"})
    await hass.async_block_till_done()

    result = await _configure(
        hass, await _start(hass, entry), {CONF_SOURCE_ENTITY: SMOKE}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


@pytest.mark.parametrize(
    ("keywords", "error"),
    [
        ({CONF_HEADLINE_KEYWORDS: ["/brand(/"]}, "invalid_regex"),
        ({CONF_EXCLUDE_KEYWORDS: ["/[/"]}, "invalid_regex"),
        (
            {CONF_TEXT_KEYWORDS: ["Lüftung"], CONF_EXCLUDE_KEYWORDS: ["lüftung"]},
            "keyword_conflict",
        ),
        (
            {CONF_HEADLINE_KEYWORDS: ["Brand"], CONF_EXCLUDE_KEYWORDS: ["Brand"]},
            "keyword_conflict",
        ),
    ],
)
async def test_keyword_errors(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    keywords: dict[str, list[str]],
    error: str,
) -> None:
    """`invalid_regex` and `keyword_conflict` are reported on the section."""
    result = await _configure(
        hass, await _start(hass, entry), {CONF_SOURCE_ENTITY: METEOALARM}
    )
    assert result["step_id"] == "filters"
    assert (result["description_placeholders"] or {})["provider"].startswith("generic")
    user_input = {CONF_DETAILS: "auto", "keywords": keywords, CONF_MIN_SEVERITY: "none"}

    result = await _configure(hass, result, user_input)

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"keywords": error}
    result = await _configure(
        hass,
        result,
        {CONF_DETAILS: "auto", "keywords": {}, CONF_MIN_SEVERITY: "none"},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_choosing_attributes_shows_the_filters_first(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """Forcing `attributes` on a source without details shows the keywords."""
    result = await _configure(
        hass, await _start(hass, entry), {CONF_SOURCE_ENTITY: SMOKE}
    )
    assert "keywords" not in sections(result["data_schema"])

    result = await _configure(hass, result, {CONF_DETAILS: "attributes"})

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "filters"
    assert "keywords" in sections(result["data_schema"])
    assert suggested(result["data_schema"], CONF_DETAILS) == "attributes"
    result = await _configure(
        hass,
        result,
        {
            CONF_DETAILS: "attributes",
            "keywords": {CONF_HEADLINE_KEYWORDS: ["smoke"]},
            CONF_MIN_SEVERITY: "minor",
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    data = _only_subentry(entry).data
    assert data[CONF_DETAILS] == "attributes"
    assert data[CONF_HEADLINE_KEYWORDS] == ["smoke"]
    assert data[CONF_MIN_SEVERITY] == "minor"


async def test_choosing_none_drops_the_filters(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """Forcing `none` stores no keywords, even if they were entered."""
    result = await _configure(
        hass, await _start(hass, entry), {CONF_SOURCE_ENTITY: METEOALARM}
    )
    result = await _configure(
        hass,
        result,
        {
            CONF_DETAILS: "none",
            "keywords": {CONF_HEADLINE_KEYWORDS: ["storm"]},
            CONF_MIN_SEVERITY: "severe",
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    data = _only_subentry(entry).data
    assert data[CONF_HEADLINE_KEYWORDS] == []
    assert data[CONF_MIN_SEVERITY] == "none"


async def test_reconfigure_is_prefilled_and_updates(
    hass: HomeAssistant, entry: MockConfigEntry, reload_spy: MagicMock
) -> None:
    """Reconfigure shows the stored values and updates the subentry."""
    result = await _configure(
        hass, await _start(hass, entry), {CONF_SOURCE_ENTITY: DWD}
    )
    result = await _configure(
        hass, result, {CONF_TRIGGER: "numeric", CONF_THRESHOLD: 3}
    )
    result = await _configure(
        hass,
        result,
        {
            CONF_DETAILS: "auto",
            "keywords": {CONF_HEADLINE_KEYWORDS: ["Gewitter"]},
            CONF_MIN_SEVERITY: "moderate",
        },
    )
    await hass.async_block_till_done()
    subentry = _only_subentry(entry)
    reload_spy.reset_mock()

    result = await entry.start_subentry_reconfigure_flow(hass, subentry.subentry_id)
    assert result["step_id"] == "reconfigure"
    assert suggested(result["data_schema"], CONF_SOURCE_ENTITY) == DWD
    assert suggested(result["data_schema"], CONF_NAME) == "Example warning level"
    result = await _configure(
        hass, result, {CONF_SOURCE_ENTITY: DWD, CONF_NAME: "Weather"}
    )
    assert result["step_id"] == "trigger"
    assert suggested(result["data_schema"], CONF_THRESHOLD) == 3
    result = await _configure(
        hass, result, {CONF_TRIGGER: "numeric", CONF_THRESHOLD: 4}
    )
    assert result["step_id"] == "filters"
    schema = result["data_schema"]
    assert suggested(schema, CONF_MIN_SEVERITY) == "moderate"
    assert suggested(schema, CONF_HEADLINE_KEYWORDS) == ["Gewitter"]
    result = await _configure(
        hass,
        result,
        {
            CONF_DETAILS: "auto",
            "keywords": {CONF_HEADLINE_KEYWORDS: ["Unwetter"]},
            CONF_MIN_SEVERITY: "severe",
        },
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    subentry = _only_subentry(entry)
    assert subentry.title == "Weather"
    assert subentry.data[CONF_THRESHOLD] == 4
    assert subentry.data[CONF_HEADLINE_KEYWORDS] == ["Unwetter"]
    assert subentry.data[CONF_MIN_SEVERITY] == "severe"
    reload_spy.assert_called_once_with(entry.entry_id)


async def test_reconfigure_to_another_source(
    hass: HomeAssistant, entry: MockConfigEntry, nina_device: str
) -> None:
    """Switching to another source starts with that source's suggestions."""
    hass.config.language = "de"
    result = await _configure(
        hass, await _start(hass, entry), {CONF_SOURCE_ENTITY: SMOKE}
    )
    await _configure(hass, result, {CONF_DETAILS: "auto"})
    await hass.async_block_till_done()
    subentry = _only_subentry(entry)

    result = await entry.start_subentry_reconfigure_flow(hass, subentry.subentry_id)
    result = await _configure(hass, result, {CONF_SOURCE_DEVICE: nina_device})

    assert result["step_id"] == "filters"
    schema = result["data_schema"]
    assert suggested(schema, CONF_EXCLUDE_KEYWORDS) == list(
        NINA_DE[CONF_EXCLUDE_KEYWORDS]
    )
    result = await _configure(
        hass,
        result,
        {CONF_DETAILS: "auto", "keywords": {}, CONF_MIN_SEVERITY: "none"},
    )
    assert result["type"] is FlowResultType.ABORT
    await hass.async_block_till_done()
    subentry = _only_subentry(entry)
    assert subentry.unique_id == nina_device
    assert subentry.title == "Example City"
    assert CONF_SOURCE_ENTITY not in subentry.data


async def test_reconfigure_to_a_used_source_is_rejected(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """Another subentry's entity aborts the reconfigure (`already_configured`)."""
    for entity_id in (SMOKE, DANGER):
        result = await _configure(
            hass, await _start(hass, entry), {CONF_SOURCE_ENTITY: entity_id}
        )
        await _configure(hass, result, {CONF_DETAILS: "auto"})
        await hass.async_block_till_done()
    danger = next(s for s in entry.subentries.values() if s.unique_id == DANGER)

    result = await entry.start_subentry_reconfigure_flow(hass, danger.subentry_id)
    result = await _configure(hass, result, {CONF_SOURCE_ENTITY: SMOKE})

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_device_without_name_falls_back_to_its_id(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """A device without a name (or an unknown id) is named by its id."""
    result = await _configure(
        hass, await _start(hass, entry), {CONF_SOURCE_DEVICE: "unknown_device"}
    )
    assert result["step_id"] == "filters"
    assert (result["description_placeholders"] or {})["name"] == "unknown_device"
