"""LLM tools of the voice intents for the Assist API (Q2, HA 2026.10).

Up to HA 2026.4 the Assist API offered every registered intent handler as a
tool by itself (``helpers/llm.py``, ``AssistAPI._async_get_tools``). In HA
2026.10 the Assist API lives in the ``llm`` integration and collects its tools
from the ``llm`` platform of every loaded integration
(``homeassistant.components.llm.async_get_tools``); this module is that
platform. Tool names carry the ``<domain>__`` prefix the ``llm`` integration
asks for. On HA versions without the ``llm`` integration this module is never
imported.
"""

from __future__ import annotations

from typing import Final

from homeassistant.components.llm import LLMTools
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import intent
from homeassistant.helpers.llm import (
    LLM_API_ASSIST,
    IntentTool,
    LLMContext,
    Tool,
    ToolAnnotations,
)

from .const import DOMAIN
from .intent import (
    INTENT_BOOST,
    INTENT_PAUSE,
    INTENT_RESUME,
    INTENT_STATUS,
    VentilationIntentHandler,
)

#: Behavior of each tool: everything stays inside Home Assistant; a boost or
#: pause can be undone (resume), the status only reads.
ANNOTATIONS: Final = {
    INTENT_BOOST: ToolAnnotations(destructive=False, open_world=False),
    INTENT_PAUSE: ToolAnnotations(destructive=False, idempotent=True, open_world=False),
    INTENT_RESUME: ToolAnnotations(
        destructive=False, idempotent=True, open_world=False
    ),
    INTENT_STATUS: ToolAnnotations(read_only=True, open_world=False),
}


def tool_name(intent_type: str) -> str:
    """Return the LLM tool name of an intent (``better_home_ventilation__<intent>``)."""
    return f"{DOMAIN}__{intent_type}"


@callback
def async_get_tools(
    hass: HomeAssistant, llm_context: LLMContext, api_id: str
) -> LLMTools | None:
    """Return the intent tools while at least one entry is loaded."""
    if api_id != LLM_API_ASSIST or not any(
        entry.state is ConfigEntryState.LOADED
        for entry in hass.config_entries.async_entries(DOMAIN)
    ):
        return None
    tools: list[Tool] = [
        IntentTool(
            tool_name(handler.intent_type),
            handler,
            title=handler.title,
            integration=DOMAIN,
            annotations=ANNOTATIONS[handler.intent_type],
        )
        for handler in intent.async_get(hass)
        if isinstance(handler, VentilationIntentHandler)
    ]
    return LLMTools(tools=tools)
