"""Repair issues `missing_entity` and `state_reset` (BHV-55, BHV-50, C14, C4a removal)."""

from __future__ import annotations

from typing import Any

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryDisabler
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
)
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.better_home_ventilation.const import (
    CONF_CO2_SENSORS,
    CONF_PRESENCE_ENTITIES,
    DOMAIN,
)

from .common import CO2_LIVING, NUMBER_RAW, MockUnit, advance, entry_options
from .test_controller import controller_of, problem_keys, readiness, setup_entry
from .test_lifecycle import register
from .test_safety import source

GONE = "sensor.gone_co2"
ISSUE = f"missing_entity_{GONE}"
WITH_GONE: dict[str, Any] = {CONF_CO2_SENSORS: [CO2_LIVING, GONE]}


@pytest.fixture(autouse=True)
def daytime(freezer: FrozenDateTimeFactory) -> None:
    """Run every test at 13:00 local time, outside the night window."""
    freezer.move_to("2026-10-14T20:00:00+00:00")


def issue(hass: HomeAssistant, issue_id: str = ISSUE) -> ir.IssueEntry | None:
    """Return a repair issue of the integration."""
    return ir.async_get(hass).async_get_issue(DOMAIN, issue_id)


def missing_issues(hass: HomeAssistant) -> set[str]:
    """Return the ids of all `missing_entity` issues."""
    return {
        issue_id
        for domain, issue_id in ir.async_get(hass).issues
        if domain == DOMAIN and issue_id.startswith("missing_entity_")
    }


@pytest.mark.usefixtures("entities")
async def test_c14_missing_entity_after_five_minutes(
    hass: HomeAssistant, unit: MockUnit, freezer: FrozenDateTimeFactory
) -> None:
    """An entity in neither the state machine nor the registry: issue after 5 min."""
    entry = await setup_entry(hass, **WITH_GONE)
    controller = controller_of(entry)

    await advance(hass, freezer, 299)
    assert issue(hass) is None
    assert controller.missing_entities == frozenset()

    await advance(hass, freezer, 2)
    found = issue(hass)
    assert found is not None
    assert found.translation_key == "missing_entity"
    assert not found.is_fixable
    assert found.severity is ir.IssueSeverity.WARNING
    assert found.translation_placeholders == {
        "entity": GONE,
        "usages": "Ventilation: CO₂ sensors (step Inputs)",
    }
    assert controller.missing_entities == {GONE}
    assert ("missing_entity", GONE) in problem_keys(controller)

    # The entity appears: the issue and the problem are resolved.
    hass.states.async_set(GONE, "800")
    await hass.async_block_till_done()
    assert issue(hass) is None
    assert controller.missing_entities == frozenset()
    assert ("missing_entity", GONE) not in problem_keys(controller)


@pytest.mark.usefixtures("entities")
async def test_entity_only_in_the_registry_is_not_missing(
    hass: HomeAssistant, unit: MockUnit, freezer: FrozenDateTimeFactory
) -> None:
    """A registered entity without a state (e.g. disabled) is not missing."""
    er.async_get(hass).async_get_or_create(
        "sensor", "test", "gone", suggested_object_id="gone_co2"
    )
    entry = await setup_entry(hass, **WITH_GONE)

    await advance(hass, freezer, 301)
    assert issue(hass) is None
    assert controller_of(entry).missing_entities == frozenset()


@pytest.mark.usefixtures("entities")
async def test_c4a_removed_entity_raises_issue_at_once(
    hass: HomeAssistant, unit: MockUnit
) -> None:
    """Removing a configured entity from the registry raises the issue at once."""
    registry = register(hass, CO2_LIVING)
    entry = await setup_entry(hass)
    controller = controller_of(entry)

    # A removed entity also leaves the state machine (the platform removes it).
    registry.async_remove(CO2_LIVING)
    hass.states.async_remove(CO2_LIVING)
    await hass.async_block_till_done()
    found = issue(hass, f"missing_entity_{CO2_LIVING}")
    assert found is not None
    assert controller.missing_entities == {CO2_LIVING}

    # The entity comes back (registry entry created again).
    registry.async_get_or_create(
        "sensor", "test", "living_room_co2", suggested_object_id="living_room_co2"
    )
    await hass.async_block_till_done()
    assert issue(hass, f"missing_entity_{CO2_LIVING}") is None
    assert controller.missing_entities == frozenset()


@pytest.mark.usefixtures("entities")
async def test_missing_readiness_entity_and_options_change(
    hass: HomeAssistant, unit: MockUnit, freezer: FrozenDateTimeFactory
) -> None:
    """Readiness entities count; the issue survives a reload and ends with the options."""
    entry = await setup_entry(
        hass,
        subentries=[readiness("select.gone_mode", "Remote")],
        **{CONF_PRESENCE_ENTITIES: ["person.gone"]},
    )
    await advance(hass, freezer, 301)
    assert missing_issues(hass) == {
        "missing_entity_select.gone_mode",
        "missing_entity_person.gone",
    }
    found = issue(hass, "missing_entity_select.gone_mode")
    assert found is not None
    assert found.translation_placeholders == {
        "entity": "select.gone_mode",
        "usages": "Ventilation: readiness condition select.gone_mode = Remote",
    }

    # A reload (here: of the unchanged entry) keeps the issues without a wait.
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert len(missing_issues(hass)) == 2
    assert controller_of(entry).missing_entities == {"select.gone_mode", "person.gone"}

    # The presence entity is removed from the options: its issue is deleted.
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONF_PRESENCE_ENTITIES: []}
    )
    await hass.async_block_till_done()
    assert missing_issues(hass) == {"missing_entity_select.gone_mode"}


@pytest.mark.usefixtures("entities")
async def test_missing_warning_source_device(
    hass: HomeAssistant, unit: MockUnit
) -> None:
    """A removed warning source device raises the issue under its device id."""
    other = MockConfigEntry(domain="example", title="Example City")
    other.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=other.entry_id, identifiers={("example", "region")}
    )
    entry = await setup_entry(
        hass, subentries=[source(device_id=device.id, name="Example City")]
    )

    dr.async_get(hass).async_remove_device(device.id)
    await hass.async_block_till_done()
    found = issue(hass, f"missing_entity_{device.id}")
    assert found is not None
    assert found.translation_placeholders == {
        "entity": device.id,
        "usages": "Ventilation: warning source Example City",
    }
    assert ("missing_entity", device.id) in problem_keys(controller_of(entry))


@pytest.mark.usefixtures("entities")
async def test_missing_source_entity_and_two_entries(
    hass: HomeAssistant, unit: MockUnit, freezer: FrozenDateTimeFactory
) -> None:
    """One issue per entity for all entries; removed with the last entry using it."""
    first = await setup_entry(
        hass, subentries=[source(entity_id="binary_sensor.gone", name="Smoke")]
    )
    second = MockConfigEntry(
        domain=DOMAIN,
        title="Attic",
        data={},
        options=entry_options(
            **{CONF_CO2_SENSORS: [CO2_LIVING], CONF_PRESENCE_ENTITIES: []},
            actuator=NUMBER_RAW,
        ),
        version=1,
        minor_version=1,
        subentries_data=[source(entity_id="binary_sensor.gone", name="Smoke")],
    )
    second.add_to_hass(hass)
    assert await hass.config_entries.async_setup(second.entry_id)
    await hass.async_block_till_done()
    await advance(hass, freezer, 301)

    found = issue(hass, "missing_entity_binary_sensor.gone")
    assert found is not None
    assert found.translation_placeholders == {
        "entity": "binary_sensor.gone",
        "usages": "Attic: warning source Smoke; Ventilation: warning source Smoke",
    }

    # Disabling an entry drops its usage; removing the last one the issue.
    await hass.config_entries.async_set_disabled_by(
        second.entry_id, ConfigEntryDisabler.USER
    )
    await hass.async_block_till_done()
    found = issue(hass, "missing_entity_binary_sensor.gone")
    assert found is not None
    assert found.translation_placeholders is not None
    assert found.translation_placeholders["usages"] == (
        "Ventilation: warning source Smoke"
    )
    assert await hass.config_entries.async_remove(first.entry_id)
    await hass.async_block_till_done()
    assert missing_issues(hass) == set()


@pytest.mark.usefixtures("entities")
async def test_bhv50_state_reset_issue(
    hass: HomeAssistant, unit: MockUnit, hass_storage: dict[str, Any]
) -> None:
    """An unreadable store raises the informational `state_reset` issue."""
    entry = MockConfigEntry(
        domain=DOMAIN, title="Ventilation", data={}, options=entry_options()
    )
    key = f"{DOMAIN}.{entry.entry_id}"
    hass_storage[key] = {
        "key": key,
        "version": 1,
        "minor_version": 1,
        "data": {"manual": {"type": "bogus"}},
    }
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    issue_id = f"state_reset_{entry.entry_id}"
    found = issue(hass, issue_id)
    assert found is not None
    assert found.translation_key == "state_reset"
    assert not found.is_fixable
    assert not found.is_persistent
    assert found.translation_placeholders == {"entry": "Ventilation"}

    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert issue(hass, issue_id) is None


@pytest.mark.usefixtures("entities")
async def test_readable_store_raises_no_issue(
    hass: HomeAssistant, unit: MockUnit, freezer: FrozenDateTimeFactory
) -> None:
    """A normal start raises no issue at all."""
    await setup_entry(hass)
    await advance(hass, freezer, 301)
    assert [
        issue_id for domain, issue_id in ir.async_get(hass).issues if domain == DOMAIN
    ] == []
