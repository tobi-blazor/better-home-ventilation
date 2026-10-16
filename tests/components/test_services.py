"""Services boost, pause, resume: entry resolution and errors (C7, BHV-22)."""

from __future__ import annotations

from datetime import timedelta
import json
from pathlib import Path
from typing import Any

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryDisabler
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
import voluptuous as vol

from custom_components.better_home_ventilation.const import DOMAIN
from custom_components.better_home_ventilation.engine.models import (
    Boost,
    Mode,
    Pause,
    Source,
)

from .common import NUMBER_PERCENT, MockUnit, advance, entry_options
from .test_controller import controller_of, setup_entry

STRINGS = json.loads(
    (
        Path(__file__).parents[2] / "custom_components" / DOMAIN / "strings.json"
    ).read_text(encoding="utf-8")
)


@pytest.fixture(autouse=True)
def daytime(freezer: FrozenDateTimeFactory) -> None:
    """Run every test at 13:00 local time, outside the night window."""
    freezer.move_to("2026-10-14T20:00:00+00:00")


async def call(hass: HomeAssistant, service: str, **data: Any) -> None:
    """Call a service of the integration."""
    await hass.services.async_call(DOMAIN, service, data, blocking=True)


async def rejected(
    hass: HomeAssistant, service: str, key: str, **data: Any
) -> ServiceValidationError:
    """Call a service, expect a translated rejection and return it."""
    with pytest.raises(ServiceValidationError) as err:
        await call(hass, service, **data)
    assert err.value.translation_domain == DOMAIN
    assert err.value.translation_key == key
    template = STRINGS["exceptions"][key]["message"]
    placeholders = err.value.translation_placeholders or {}
    assert str(err.value) == template.format(**placeholders).rstrip(".")
    return err.value


@pytest.mark.usefixtures("entities")
async def test_c7_one_entry_is_used_without_id(
    hass: HomeAssistant, unit: MockUnit, freezer: FrozenDateTimeFactory
) -> None:
    """With exactly one entry the services act on it (source `service`)."""
    entry = await setup_entry(hass)
    controller = controller_of(entry)
    await advance(hass, freezer, 60)

    await call(hass, "boost", duration=45, level=250)
    manual = controller.state.manual
    assert isinstance(manual, Boost)
    assert manual.source is Source.SERVICE
    assert manual.level == 250
    assert manual.until == dt_util.now() + timedelta(minutes=45)

    await call(hass, "pause", duration=90)
    manual = controller.state.manual
    assert isinstance(manual, Pause)
    assert manual.until == dt_util.now() + timedelta(minutes=90)

    await call(hass, "pause")
    manual = controller.state.manual
    assert isinstance(manual, Pause)
    assert manual.until is None

    await call(hass, "resume", config_entry_id=entry.entry_id)
    assert controller.state.manual is None
    assert controller.current_decision.mode is Mode.AUTOMATIC  # type: ignore[union-attr]


@pytest.mark.usefixtures("entities")
async def test_boost_defaults_come_from_parameter_entities(
    hass: HomeAssistant, unit: MockUnit, freezer: FrozenDateTimeFactory
) -> None:
    """Without duration and level the boost uses the parameter entities."""
    entry = await setup_entry(hass)
    controller = controller_of(entry)
    await advance(hass, freezer, 60)
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": "select.ventilation_boost_duration", "option": "120"},
        blocking=True,
    )

    await call(hass, "boost")

    manual = controller.state.manual
    assert isinstance(manual, Boost)
    assert manual.until == dt_util.now() + timedelta(minutes=120)
    assert manual.level == 300


@pytest.mark.usefixtures("entities")
async def test_c7_two_entries_need_an_id(hass: HomeAssistant, unit: MockUnit) -> None:
    """With two entries `config_entry_id` is required (`entry_required`)."""
    first = await setup_entry(hass)
    second = await setup_entry(hass, actuator=NUMBER_PERCENT)

    await rejected(hass, "boost", "entry_required")
    await rejected(hass, "pause", "entry_required")
    await rejected(hass, "resume", "entry_required")

    await call(hass, "pause", config_entry_id=second.entry_id)
    assert isinstance(controller_of(second).state.manual, Pause)
    assert controller_of(first).state.manual is None


@pytest.mark.usefixtures("entities")
async def test_c7_unknown_and_unloaded_entries(
    hass: HomeAssistant, unit: MockUnit
) -> None:
    """Unknown ids raise `entry_not_found`, unloaded entries `entry_not_loaded`."""
    entry = await setup_entry(hass)
    other = MockConfigEntry(domain="other", title="Other")
    other.add_to_hass(hass)

    err = await rejected(hass, "boost", "entry_not_found", config_entry_id="nope")
    assert err.translation_placeholders == {"entry_id": "nope"}
    await rejected(hass, "resume", "entry_not_found", config_entry_id=other.entry_id)

    assert await hass.config_entries.async_unload(entry.entry_id)
    err = await rejected(hass, "boost", "entry_not_loaded")
    assert err.translation_placeholders == {"entry_title": "Ventilation"}
    await rejected(hass, "pause", "entry_not_loaded", config_entry_id=entry.entry_id)


@pytest.mark.usefixtures("entities")
async def test_c7_disabled_entries_do_not_count(
    hass: HomeAssistant, unit: MockUnit
) -> None:
    """A disabled entry does not make the id required."""
    entry = await setup_entry(hass)
    disabled = MockConfigEntry(
        domain=DOMAIN,
        title="Disabled",
        options=entry_options(actuator=NUMBER_PERCENT),
        disabled_by=ConfigEntryDisabler.USER,
    )
    disabled.add_to_hass(hass)

    await call(hass, "pause")
    assert isinstance(controller_of(entry).state.manual, Pause)


@pytest.mark.usefixtures("entities")
async def test_c7_engine_rejections(
    hass: HomeAssistant, unit: MockUnit, freezer: FrozenDateTimeFactory
) -> None:
    """`level_out_of_range` and `control_disabled` are translated errors."""
    await setup_entry(hass)
    await advance(hass, freezer, 60)

    await rejected(hass, "boost", "level_out_of_range", level=50)
    await rejected(hass, "boost", "level_out_of_range", level=401)

    await hass.services.async_call(
        "switch",
        "turn_off",
        {"entity_id": "switch.ventilation_control"},
        blocking=True,
    )
    await rejected(hass, "boost", "control_disabled")
    await rejected(hass, "pause", "control_disabled", duration=10)
    await call(hass, "resume")  # nothing to end


@pytest.mark.usefixtures("entities")
@pytest.mark.parametrize(
    ("service", "data"),
    [
        ("boost", {"duration": 4}),
        ("boost", {"duration": 721}),
        ("pause", {"duration": 0}),
        ("pause", {"duration": 10081}),
        ("resume", {"duration": 5}),
    ],
)
async def test_service_schemas(
    hass: HomeAssistant, unit: MockUnit, service: str, data: dict[str, Any]
) -> None:
    """Durations outside the documented ranges are invalid."""
    await setup_entry(hass)
    with pytest.raises(vol.Invalid):
        await call(hass, service, **data)


def test_every_exception_is_translated() -> None:
    """Every service error key has a message in every translation."""
    translations = Path(__file__).parents[2] / "custom_components" / DOMAIN
    for name in ("strings.json", "translations/en.json", "translations/de.json"):
        strings = json.loads((translations / name).read_text(encoding="utf-8"))
        for key in (
            "entry_required",
            "entry_not_found",
            "entry_not_loaded",
            "level_out_of_range",
            "control_disabled",
        ):
            assert strings["exceptions"][key]["message"], (name, key)
        for service, fields in (
            ("boost", {"config_entry_id", "duration", "level"}),
            ("pause", {"config_entry_id", "duration"}),
            ("resume", {"config_entry_id"}),
        ):
            translated = strings["services"][service]
            assert translated["name"]
            assert translated["description"]
            assert set(translated["fields"]) == fields
            for field in translated["fields"].values():
                assert field["name"]
                assert field["description"]
