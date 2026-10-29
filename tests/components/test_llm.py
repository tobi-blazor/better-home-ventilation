"""The `llm` platform of the intents (Q2, HA 2026.10 and later only)."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm
from homeassistant.setup import async_setup_component
import pytest

pytest.importorskip("homeassistant.components.llm")

from custom_components.better_home_ventilation.const import DOMAIN
from custom_components.better_home_ventilation.llm import (
    ANNOTATIONS,
    async_get_tools,
    tool_name,
)

from .common import MockUnit
from .test_controller import setup_entry

CONTEXT = llm.LLMContext(
    platform="test",
    context=None,
    language="en",
    assistant="conversation",
    device_id=None,
)


async def test_tools_only_for_assist_and_loaded_entries(
    hass: HomeAssistant, entities: None, unit: MockUnit
) -> None:
    """No tools for other APIs or without a loaded entry; four with one."""
    assert await async_setup_component(hass, DOMAIN, {})
    assert async_get_tools(hass, CONTEXT, llm.LLM_API_ASSIST) is None

    await setup_entry(hass)
    assert async_get_tools(hass, CONTEXT, "other_api") is None
    result = async_get_tools(hass, CONTEXT, llm.LLM_API_ASSIST)
    assert result is not None
    assert {tool.name for tool in result.tools} == {
        tool_name(intent_type) for intent_type in ANNOTATIONS
    }
    for tool in result.tools:
        assert tool.integration == DOMAIN
        assert tool.title
    status = next(tool for tool in result.tools if tool.name.endswith("Status"))
    assert status.annotations.read_only
