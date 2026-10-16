"""Entry lifecycle of the controller: renames, unload, restart, removal (C4a, C6, C9)."""

from __future__ import annotations

from typing import Any

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from homeassistant.util.async_ import get_scheduled_timer_handles
import pytest

from custom_components.better_home_ventilation import PLATFORMS
from custom_components.better_home_ventilation.const import (
    CONF_CO2_SENSORS,
    CONF_ENTITY_ID,
    DOMAIN,
)
from custom_components.better_home_ventilation.engine.models import (
    BoostCommand,
    Mode,
    Source,
)

from .common import CO2_LIVING, CONTROL_MODE, NUMBER_FLOW, MockUnit, advance
from .test_controller import controller_of, readiness, setup_entry

CO2 = {"device_class": "carbon_dioxide", "unit_of_measurement": "ppm"}


@pytest.fixture(autouse=True)
def daytime(freezer: FrozenDateTimeFactory) -> None:
    """Run every test at 13:00 local time, outside the night window."""
    freezer.move_to("2026-10-14T20:00:00+00:00")


def register(hass: HomeAssistant, entity_id: str) -> er.EntityRegistry:
    """Add an existing entity (with state) to the entity registry."""
    state = hass.states.get(entity_id)
    assert state is not None
    hass.states.async_remove(entity_id)
    registry = er.async_get(hass)
    domain, object_id = entity_id.split(".")
    entry = registry.async_get_or_create(
        domain, "test", object_id, suggested_object_id=object_id
    )
    assert entry.entity_id == entity_id
    hass.states.async_set(entity_id, state.state, state.attributes)
    return registry


@pytest.mark.usefixtures("entities")
async def test_c4a_rename_updates_options_and_reloads(
    hass: HomeAssistant, unit: MockUnit
) -> None:
    """A renamed sensor is written to the options; the entry reloads (BHV-53)."""
    registry = register(hass, CO2_LIVING)
    entry = await setup_entry(hass)
    old_controller = controller_of(entry)

    registry.async_update_entity(CO2_LIVING, new_entity_id="sensor.lounge_co2")
    await hass.async_block_till_done()

    assert entry.options[CONF_CO2_SENSORS] == ["sensor.lounge_co2"]
    assert entry.state is ConfigEntryState.LOADED
    controller = controller_of(entry)
    assert controller is not old_controller
    assert controller.options.co2_sensors == ("sensor.lounge_co2",)


@pytest.mark.usefixtures("entities")
async def test_c4a_rename_updates_readiness_subentry(
    hass: HomeAssistant, unit: MockUnit
) -> None:
    """A renamed readiness entity is written to its subentry (data and unique id)."""
    registry = register(hass, CONTROL_MODE)
    entry = await setup_entry(hass, subentries=[readiness(CONTROL_MODE, "Remote")])
    options = dict(entry.options)

    registry.async_update_entity(CONTROL_MODE, new_entity_id="select.unit_mode")
    await hass.async_block_till_done()

    [subentry] = entry.subentries.values()
    assert subentry.data[CONF_ENTITY_ID] == "select.unit_mode"
    assert subentry.unique_id == "select.unit_mode"
    assert dict(entry.options) == options
    assert entry.state is ConfigEntryState.LOADED
    assert [c.entity_id for c in controller_of(entry).options.readiness_conditions] == [
        "select.unit_mode"
    ]


@pytest.mark.usefixtures("entities")
async def test_c4a_removed_entity_keeps_running(
    hass: HomeAssistant, unit: MockUnit
) -> None:
    """A removed entity does not change the options; control continues."""
    registry = register(hass, CO2_LIVING)
    entry = await setup_entry(hass)
    controller = controller_of(entry)
    options = dict(entry.options)

    registry.async_remove(CO2_LIVING)
    await hass.async_block_till_done()
    # Other registry updates (no rename) are ignored as well.
    registry.async_get_or_create("number", "test", "flow", suggested_object_id="x")

    assert dict(entry.options) == options
    assert entry.state is ConfigEntryState.LOADED
    assert controller_of(entry) is controller


@pytest.mark.usefixtures("entities")
async def test_registry_update_without_rename_is_ignored(
    hass: HomeAssistant, unit: MockUnit
) -> None:
    """An update that does not change the entity id does not reload."""
    registry = register(hass, CO2_LIVING)
    entry = await setup_entry(hass)
    controller = controller_of(entry)

    registry.async_update_entity(CO2_LIVING, name="Lounge CO2")
    await hass.async_block_till_done()

    assert controller_of(entry) is controller


def _active_handles(hass: HomeAssistant) -> set[Any]:
    """Return the pending timers, without the daily registry cleanup of HA."""
    return {
        handle
        for handle in get_scheduled_timer_handles(hass.loop)
        if not handle.cancelled()
        and not (
            handle._args
            and "_async_setup_cleanup" in str(getattr(handle._args[0], "name", ""))
        )
    }


@pytest.mark.usefixtures("entities")
async def test_c6_unload_removes_listeners_and_timers(
    hass: HomeAssistant, unit: MockUnit, freezer: FrozenDateTimeFactory
) -> None:
    """Unload cancels subscriptions, debounce, scheduled and verification timers."""
    # The entity components stay loaded after an unload; set them up first.
    for platform in PLATFORMS:
        assert await async_setup_component(hass, platform, {})
    listeners_before = hass.bus.async_listeners()
    handles_before = _active_handles(hass)
    entry = await setup_entry(hass)
    controller = controller_of(entry)
    await advance(hass, freezer, 60)  # first write: verification pending
    await controller.async_command(BoostCommand(source=Source.SERVICE))  # boost end
    hass.states.async_set(CO2_LIVING, "1000", CO2)  # debounce pending
    await hass.async_block_till_done()
    assert _active_handles(hass) - handles_before

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.NOT_LOADED
    assert _active_handles(hass) - handles_before == set()
    assert hass.bus.async_listeners() == listeners_before
    count = len(controller.decisions)
    hass.states.async_set(NUMBER_FLOW, "200")
    await advance(hass, freezer, 120)
    assert len(controller.decisions) == count
    assert not controller.started


@pytest.mark.usefixtures("entities")
async def test_c9_boost_survives_reload(
    hass: HomeAssistant, unit: MockUnit, freezer: FrozenDateTimeFactory
) -> None:
    """Boost active -> reload -> `ends_at` unchanged, boost continues (BHV-50)."""
    entry = await setup_entry(hass)
    await advance(hass, freezer, 61)
    controller = controller_of(entry)
    await controller.async_command(BoostCommand(source=Source.BUTTON))
    decision = controller.current_decision
    assert decision is not None
    assert decision.mode is Mode.BOOST
    ends_at = decision.ends_at
    assert ends_at is not None

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    controller = controller_of(entry)
    decision = controller.current_decision
    assert decision is not None
    assert decision.mode is Mode.BOOST
    assert decision.ends_at == ends_at

    await advance(hass, freezer, 60)  # after the new startup grace
    assert controller.current_decision.mode is Mode.BOOST  # type: ignore[union-attr]
    assert unit.calls_for("number", "set_value")[-1].data["value"] == 300.0


@pytest.mark.usefixtures("entities")
async def test_remove_entry_deletes_store(
    hass: HomeAssistant, unit: MockUnit, hass_storage: dict[str, Any]
) -> None:
    """Removing the entry deletes its stored controller state."""
    entry = await setup_entry(hass)
    key = f"{DOMAIN}.{entry.entry_id}"
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert key in hass_storage

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert key not in hass_storage


@pytest.mark.usefixtures("entities")
async def test_controller_waits_for_ha_start(
    hass: HomeAssistant, unit: MockUnit
) -> None:
    """Before Home Assistant has started nothing is evaluated (02-control startup)."""
    hass.set_state(hass.state.__class__.starting)
    entry = await setup_entry(hass)
    controller = controller_of(entry)
    assert controller.current_decision is None
    await controller.async_request_evaluation()
    assert controller.current_decision is None

    await hass.async_start()
    await hass.async_block_till_done()
    assert controller.current_decision is not None
