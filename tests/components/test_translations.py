"""Flow translations (C4c): every form field, section and menu option is translated.

The test walks every 0.1 flow step, takes the schema that is actually shown and
checks `strings.json`, `translations/en.json` and `translations/de.json`.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import selector
import pytest

from custom_components.better_home_ventilation.const import (
    CONF_ACTUATOR,
    CONF_ENTITY_ID,
    CONF_EXPECTED_STATE,
    CONF_MAX_FLOW,
    CONF_NAME,
    DOMAIN,
    SUBENTRY_READINESS_CONDITION,
)

from .common import CONTROL_MODE, NUMBER_FLOW, NUMBER_RAW, mock_entry, schema_fields

INTEGRATION = Path(__file__).parents[2] / "custom_components" / DOMAIN
FILES = {
    "strings": INTEGRATION / "strings.json",
    "en": INTEGRATION / "translations" / "en.json",
    "de": INTEGRATION / "translations" / "de.json",
}
PLACEHOLDER = re.compile(r"\{(\w+)\}")

CONFIG_ERRORS = {
    "actuator_not_found",
    "duplicate_sensor",
    "max_flow_required",
    "no_inputs",
    "select_mapping",
    "select_options",
    "sensor_not_found",
}
OPTIONS_ERRORS = {
    "actuator_in_use",
    "actuator_not_found",
    "co2_range",
    "dryness_overlap",
    "duplicate_sensor",
    "fallback_range",
    "humidity_range",
    "level_range",
    "night_window",
    "no_inputs",
    "quiet_range",
    "select_mapping",
    "select_options",
    "sensor_not_found",
}


def _load(name: str) -> dict[str, Any]:
    return json.loads(FILES[name].read_text(encoding="utf-8"))


def _keys(tree: Any, prefix: str = "") -> set[str]:
    """Return every key path of a nested dict."""
    if not isinstance(tree, dict):
        return {prefix}
    paths: set[str] = set()
    for key, value in tree.items():
        paths |= _keys(value, f"{prefix}/{key}")
    return paths


def _assert_form(strings: dict[str, Any], steps: dict[str, Any], result: Any) -> None:
    """Assert a shown form is completely translated."""
    assert result["type"] is FlowResultType.FORM
    step = steps[result["step_id"]]
    assert step["title"]
    assert step["description"]
    used = set(PLACEHOLDER.findall(step["description"]))
    assert used <= set(result["description_placeholders"] or {}), result["step_id"]
    for key, (section_name, _, field_selector) in schema_fields(
        result["data_schema"]
    ).items():
        node = step if section_name is None else step["sections"][section_name]
        if section_name is not None:
            assert node["name"], section_name
            assert node["description"], section_name
        assert node["data"][key], key
        assert node["data_description"][key], key
        if isinstance(field_selector, selector.SelectSelector) and (
            translation_key := field_selector.config.get("translation_key")
        ):
            options = strings["selector"][translation_key]["options"]
            for option in field_selector.config["options"]:
                assert options[option], (translation_key, option)


async def _walk_flows(hass: HomeAssistant) -> list[tuple[str, Any]]:
    """Open every 0.1 form and return (category, result) pairs."""
    shown: list[tuple[str, Any]] = []
    flow = hass.config_entries.flow
    result: Any = await flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    shown.append(("config", result))
    result = await flow.async_configure(
        result["flow_id"],
        {CONF_NAME: "Ventilation", CONF_ACTUATOR: NUMBER_RAW, CONF_MAX_FLOW: 400},
    )
    shown.append(("config", result))
    result = await flow.async_configure(result["flow_id"], {"actuator_unit": "flow"})
    shown.append(("config", result))

    entry = mock_entry(actuator=NUMBER_FLOW)
    entry.add_to_hass(hass)
    options = hass.config_entries.options
    for step in ("actuator", "inputs", "control", "boosts"):
        result = await options.async_init(entry.entry_id)
        result = await options.async_configure(
            result["flow_id"], {"next_step_id": step}
        )
        shown.append(("options", result))
    result = await options.async_init(entry.entry_id)
    result = await options.async_configure(
        result["flow_id"], {"next_step_id": "actuator"}
    )
    result = await options.async_configure(
        result["flow_id"],
        {CONF_ACTUATOR: NUMBER_RAW, CONF_MAX_FLOW: 400, "off": {}, "range": {}},
    )
    shown.append(("options", result))

    subentries = hass.config_entries.subentries
    for entity_id in (CONTROL_MODE, "binary_sensor.unit_ready", "number.unit_register"):
        result = await subentries.async_init(
            (entry.entry_id, SUBENTRY_READINESS_CONDITION),
            context={"source": SOURCE_USER},
        )
        shown.append(("readiness", result))
        result = await subentries.async_configure(
            result["flow_id"], {CONF_ENTITY_ID: entity_id}
        )
        shown.append(("readiness", result))
    await subentries.async_configure(result["flow_id"], {CONF_EXPECTED_STATE: 1})
    subentry_id = next(iter(entry.subentries))
    result = await entry.start_subentry_reconfigure_flow(hass, subentry_id)
    shown.append(("readiness", result))
    return shown


@pytest.mark.usefixtures("entities")
@pytest.mark.parametrize("language", list(FILES))
async def test_flows_are_translated(hass: HomeAssistant, language: str) -> None:
    """Every field has data and data_description, every section a name (C4c)."""
    hass.states.async_set("binary_sensor.unit_ready", "on")
    hass.states.async_set("number.unit_register", "1")
    strings = _load(language)
    steps = {
        "config": strings["config"]["step"],
        "options": strings["options"]["step"],
        "readiness": strings["config_subentries"][SUBENTRY_READINESS_CONDITION]["step"],
    }

    shown = await _walk_flows(hass)

    assert {result["step_id"] for _, result in shown} == {
        "user",
        "actuator",
        "inputs",
        "control",
        "boosts",
        "actuator_mapping",
        "state",
        "reconfigure",
    }
    for category, result in shown:
        _assert_form(strings, steps[category], result)


@pytest.mark.parametrize("language", list(FILES))
def test_menu_errors_and_aborts_are_translated(language: str) -> None:
    """Menu options, error and abort keys exist in every translation."""
    strings = _load(language)
    menu = strings["options"]["step"]["init"]
    assert menu["title"]
    for option in ("actuator", "inputs", "control", "boosts"):
        assert menu["menu_options"][option]
        assert menu["menu_option_descriptions"][option]
    assert set(strings["config"]["error"]) == CONFIG_ERRORS
    assert set(strings["options"]["error"]) == OPTIONS_ERRORS
    assert strings["config"]["abort"]["already_configured"]
    readiness = strings["config_subentries"][SUBENTRY_READINESS_CONDITION]
    assert readiness["entry_type"]
    assert readiness["initiate_flow"]["user"]
    assert readiness["initiate_flow"]["reconfigure"]
    assert readiness["abort"]["already_configured"]
    assert readiness["error"]["not_correctable"]


def test_translation_files_match() -> None:
    """`strings.json` equals `en.json`; `de.json` has the same keys and placeholders."""
    english = _load("en")
    german = _load("de")
    assert _load("strings") == english
    assert _keys(german) == _keys(english)

    def placeholders(tree: Any) -> list[set[str]]:
        if isinstance(tree, dict):
            return [p for key in sorted(tree) for p in placeholders(tree[key])]
        return [set(PLACEHOLDER.findall(tree))]

    assert placeholders(german) == placeholders(english)
