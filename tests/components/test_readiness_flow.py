"""Readiness condition subentries (C4, flow part): BHV-04, BHV-53."""

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
from homeassistant.helpers import entity_registry as er, selector
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.better_home_ventilation.const import (
    CONF_AUTO_CORRECT,
    CONF_ENTITY_ID,
    CONF_EXPECTED_STATE,
    SUBENTRY_READINESS_CONDITION,
)

from .common import CONTROL_MODE, schema_fields, suggested

ENABLE_SWITCH = "switch.unit_enable"
READY_SENSOR = "binary_sensor.unit_ready"
STATUS_SENSOR = "sensor.unit_status"
MODE_NUMBER = "number.unit_mode_register"


@pytest.fixture
def reload_spy(hass: HomeAssistant) -> Iterator[MagicMock]:
    """Spy on reloads scheduled by the update listener (the reload still runs)."""
    with patch.object(
        hass.config_entries,
        "async_schedule_reload",
        wraps=hass.config_entries.async_schedule_reload,
    ) as spy:
        yield spy


@pytest.fixture(autouse=True)
def readiness_entities(hass: HomeAssistant, entities: None) -> None:
    """Create entities usable as readiness conditions."""
    hass.states.async_set(ENABLE_SWITCH, "off", {"friendly_name": "Unit enable"})
    hass.states.async_set(READY_SENSOR, "on", {"friendly_name": "Unit ready"})
    hass.states.async_set(STATUS_SENSOR, "running", {"friendly_name": "Unit status"})
    hass.states.async_set(
        MODE_NUMBER, "1", {"friendly_name": "Unit mode register", "min": 0, "max": 3}
    )


async def _add(
    hass: HomeAssistant, entry: MockConfigEntry, entity_id: str
) -> SubentryFlowResult:
    """Start adding a readiness condition and choose ``entity_id``."""
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_READINESS_CONDITION),
        context={"source": SOURCE_USER},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    return await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_ENTITY_ID: entity_id}
    )


async def _configure(
    hass: HomeAssistant, result: SubentryFlowResult, user_input: dict[str, Any]
) -> SubentryFlowResult:
    """Submit a subentry flow step."""
    return await hass.config_entries.subentries.async_configure(
        result["flow_id"], user_input
    )


def _only_subentry(entry: MockConfigEntry) -> ConfigSubentry:
    """Return the single subentry of ``entry``."""
    assert len(entry.subentries) == 1
    return next(iter(entry.subentries.values()))


async def test_bhv04_add_select_condition(
    hass: HomeAssistant, entry: MockConfigEntry, reload_spy: MagicMock
) -> None:
    """A select offers its options; the subentry is added and the entry reloaded."""
    result = await _add(hass, entry, CONTROL_MODE)
    assert result["step_id"] == "state"
    assert result["description_placeholders"] == {"entity": "Unit control mode"}
    fields = schema_fields(result["data_schema"])
    state_selector = fields[CONF_EXPECTED_STATE][2]
    assert isinstance(state_selector, selector.SelectSelector)
    assert state_selector.config["options"] == ["Manual", "Remote"]
    assert CONF_AUTO_CORRECT in fields

    result = await _configure(
        hass, result, {CONF_EXPECTED_STATE: "Remote", CONF_AUTO_CORRECT: True}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    subentry = _only_subentry(entry)
    assert subentry.subentry_type == SUBENTRY_READINESS_CONDITION
    assert subentry.title == "Unit control mode = Remote"
    assert subentry.unique_id == CONTROL_MODE
    assert dict(subentry.data) == {
        CONF_ENTITY_ID: CONTROL_MODE,
        CONF_EXPECTED_STATE: "Remote",
        CONF_AUTO_CORRECT: True,
    }
    reload_spy.assert_called_once_with(entry.entry_id)
    assert entry.state is ConfigEntryState.LOADED


async def test_duplicate_entity_already_configured(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """The same entity cannot get two readiness conditions."""
    result = await _add(hass, entry, CONTROL_MODE)
    await _configure(hass, result, {CONF_EXPECTED_STATE: "Remote"})

    result = await _add(hass, entry, CONTROL_MODE)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert len(entry.subentries) == 1


async def test_not_correctable(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Read-only domains offer no auto correction and reject it when submitted."""
    result = await _add(hass, entry, READY_SENSOR)
    fields = schema_fields(result["data_schema"])
    assert CONF_AUTO_CORRECT not in fields
    state_selector = fields[CONF_EXPECTED_STATE][2]
    assert isinstance(state_selector, selector.SelectSelector)
    assert state_selector.config["translation_key"] == "binary_state"

    result = await _configure(
        hass, result, {CONF_EXPECTED_STATE: "on", CONF_AUTO_CORRECT: True}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "not_correctable"}
    assert suggested(result["data_schema"], CONF_EXPECTED_STATE) == "on"

    result = await _configure(hass, result, {CONF_EXPECTED_STATE: "on"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    subentry = _only_subentry(entry)
    assert subentry.title == "Unit ready = on"
    assert subentry.data[CONF_AUTO_CORRECT] is False


async def test_switch_condition(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """A switch offers on/off and auto correction."""
    result = await _add(hass, entry, ENABLE_SWITCH)
    assert CONF_AUTO_CORRECT in schema_fields(result["data_schema"])

    result = await _configure(
        hass, result, {CONF_EXPECTED_STATE: "on", CONF_AUTO_CORRECT: True}
    )

    assert _only_subentry(entry).data[CONF_AUTO_CORRECT] is True


async def test_number_condition(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """A number takes a numeric expected state; integral values are stored as int."""
    result = await _add(hass, entry, MODE_NUMBER)
    assert isinstance(
        schema_fields(result["data_schema"])[CONF_EXPECTED_STATE][2],
        selector.NumberSelector,
    )

    await _configure(hass, result, {CONF_EXPECTED_STATE: 2.0})

    subentry = _only_subentry(entry)
    assert subentry.data[CONF_EXPECTED_STATE] == 2
    assert isinstance(subentry.data[CONF_EXPECTED_STATE], int)
    assert subentry.title == "Unit mode register = 2"


async def test_sensor_condition_takes_text(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """Other domains take a free text expected state."""
    result = await _add(hass, entry, STATUS_SENSOR)
    assert isinstance(
        schema_fields(result["data_schema"])[CONF_EXPECTED_STATE][2],
        selector.TextSelector,
    )

    await _configure(hass, result, {CONF_EXPECTED_STATE: "running"})

    assert _only_subentry(entry).title == "Unit status = running"


@pytest.mark.parametrize(
    ("registry_name", "title"),
    [
        ("Registry mode", "Registry mode = Remote"),
        (None, "select.unit_remote_mode = Remote"),
    ],
)
async def test_condition_without_state(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    registry_name: str | None,
    title: str,
) -> None:
    """Without a state, a select takes text and the name comes from the registry."""
    entity_id = "select.unit_remote_mode"
    if registry_name is not None:
        entity_registry.async_get_or_create(
            "select",
            "test",
            "remote_mode",
            suggested_object_id="unit_remote_mode",
            original_name=registry_name,
        )
    result = await _add(hass, entry, entity_id)
    assert isinstance(
        schema_fields(result["data_schema"])[CONF_EXPECTED_STATE][2],
        selector.TextSelector,
    )

    await _configure(hass, result, {CONF_EXPECTED_STATE: "Remote"})

    assert _only_subentry(entry).title == title


async def test_reconfigure(
    hass: HomeAssistant, entry: MockConfigEntry, reload_spy: MagicMock
) -> None:
    """Reconfigure is pre-filled and updates the subentry in place."""
    result = await _add(hass, entry, CONTROL_MODE)
    await _configure(hass, result, {CONF_EXPECTED_STATE: "Remote"})
    await hass.async_block_till_done()
    reload_spy.reset_mock()
    subentry = _only_subentry(entry)

    result = await entry.start_subentry_reconfigure_flow(hass, subentry.subentry_id)
    assert result["step_id"] == "reconfigure"
    assert suggested(result["data_schema"], CONF_ENTITY_ID) == CONTROL_MODE

    result = await _configure(hass, result, {CONF_ENTITY_ID: CONTROL_MODE})
    assert result["step_id"] == "state"
    assert suggested(result["data_schema"], CONF_EXPECTED_STATE) == "Remote"
    assert suggested(result["data_schema"], CONF_AUTO_CORRECT) is False

    result = await _configure(
        hass, result, {CONF_EXPECTED_STATE: "Manual", CONF_AUTO_CORRECT: True}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    updated = _only_subentry(entry)
    assert updated.subentry_id == subentry.subentry_id
    assert updated.title == "Unit control mode = Manual"
    assert updated.data[CONF_AUTO_CORRECT] is True
    reload_spy.assert_called_once_with(entry.entry_id)


async def test_reconfigure_to_other_entity(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """Changing the entity starts the state step empty and moves the unique id."""
    result = await _add(hass, entry, CONTROL_MODE)
    await _configure(hass, result, {CONF_EXPECTED_STATE: "Remote"})
    subentry = _only_subentry(entry)

    result = await entry.start_subentry_reconfigure_flow(hass, subentry.subentry_id)
    result = await _configure(hass, result, {CONF_ENTITY_ID: ENABLE_SWITCH})
    assert suggested(result["data_schema"], CONF_EXPECTED_STATE) is None

    result = await _configure(hass, result, {CONF_EXPECTED_STATE: "on"})

    assert result["reason"] == "reconfigure_successful"
    updated = _only_subentry(entry)
    assert updated.unique_id == ENABLE_SWITCH
    assert updated.title == "Unit enable = on"


async def test_reconfigure_to_entity_of_other_condition(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """Reconfiguring to an entity that already has a condition aborts."""
    for entity_id, state in ((CONTROL_MODE, "Remote"), (ENABLE_SWITCH, "on")):
        result = await _add(hass, entry, entity_id)
        await _configure(hass, result, {CONF_EXPECTED_STATE: state})
    subentry_id = next(
        sub.subentry_id
        for sub in entry.subentries.values()
        if sub.unique_id == CONTROL_MODE
    )

    result = await entry.start_subentry_reconfigure_flow(hass, subentry_id)
    result = await _configure(hass, result, {CONF_ENTITY_ID: ENABLE_SWITCH})

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_delete_reloads_entry(
    hass: HomeAssistant, entry: MockConfigEntry, reload_spy: MagicMock
) -> None:
    """Removing a readiness condition reloads the entry via the update listener."""
    result = await _add(hass, entry, CONTROL_MODE)
    await _configure(hass, result, {CONF_EXPECTED_STATE: "Remote"})
    await hass.async_block_till_done()
    reload_spy.reset_mock()

    assert hass.config_entries.async_remove_subentry(
        entry, _only_subentry(entry).subentry_id
    )
    await hass.async_block_till_done()

    assert entry.subentries == {}
    reload_spy.assert_called_once_with(entry.entry_id)
    assert entry.state is ConfigEntryState.LOADED
