"""Voice intents and their LLM tools (BHV-22, C16, Q2)."""

from __future__ import annotations

from typing import Any

from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import __version__ as ha_version
from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent, llm
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.better_home_ventilation.const import DOMAIN
from custom_components.better_home_ventilation.engine.models import (
    Boost,
    ControlSwitchCommand,
    Mode,
    Pause,
    Source,
)
from custom_components.better_home_ventilation.intent import (
    INTENT_BOOST,
    INTENT_PAUSE,
    INTENT_RESUME,
    INTENT_STATUS,
    IntentFailed,
    async_setup_intents,
)

from .common import NUMBER_RAW, MockUnit, advance, entry_options
from .test_controller import controller_of, setup_entry

INTENTS = (INTENT_BOOST, INTENT_PAUSE, INTENT_RESUME, INTENT_STATUS)


@pytest.fixture(autouse=True)
def daytime(freezer: FrozenDateTimeFactory) -> None:
    """Run every test at 13:00 local time, outside the night window."""
    freezer.move_to("2026-10-14T20:00:00+00:00")


async def handle(
    hass: HomeAssistant,
    intent_type: str,
    language: str = "en",
    **slots: Any,
) -> intent.IntentResponse:
    """Handle an intent like the conversation agent does."""
    return await intent.async_handle(
        hass,
        "test",
        intent_type,
        {name: {"value": value} for name, value in slots.items()},
        language=language,
    )


def speech(response: intent.IntentResponse) -> str:
    """Return the plain speech of a response."""
    return str(response.speech["plain"]["speech"])


async def second_entry(hass: HomeAssistant, title: str = "Attic") -> MockConfigEntry:
    """Add and load a second entry with its own actuator."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=title,
        data={},
        options=entry_options(actuator=NUMBER_RAW),
        version=1,
        minor_version=1,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_handlers_describe_themselves(hass: HomeAssistant) -> None:
    """Every handler has a description and a slot schema with `name` (for LLMs)."""
    handlers = {handler.intent_type: handler for handler in intent.async_get(hass)}
    for intent_type in INTENTS:
        handler = handlers[intent_type]
        assert handler.description
        assert handler.slot_schema is not None
        assert "name" in {str(key) for key in handler.slot_schema}
    boost_slots = {str(key) for key in handlers[INTENT_BOOST].slot_schema or {}}
    assert boost_slots == {"name", "duration", "level"}
    pause_slots = {str(key) for key in handlers[INTENT_PAUSE].slot_schema or {}}
    assert pause_slots == {"name", "duration"}


@pytest.fixture(autouse=True)
async def integration(hass: HomeAssistant) -> None:
    """Set up the integration (registers the intents in `async_setup`)."""
    assert await async_setup_component(hass, DOMAIN, {})


async def test_registration_is_idempotent(hass: HomeAssistant) -> None:
    """The `intent` platform hook does not replace the registered handlers."""
    before = {handler.intent_type: handler for handler in intent.async_get(hass)}
    await async_setup_intents(hass)
    after = {handler.intent_type: handler for handler in intent.async_get(hass)}
    for intent_type in INTENTS:
        assert after[intent_type] is before[intent_type]


@pytest.mark.usefixtures("entities")
async def test_c16_boost_pause_resume_status(
    hass: HomeAssistant, unit: MockUnit, freezer: FrozenDateTimeFactory
) -> None:
    """The four intents run their commands (source voice) and answer."""
    entry = await setup_entry(hass)
    controller = controller_of(entry)
    await advance(hass, freezer, 61)

    response = await handle(hass, INTENT_BOOST, duration=30, level=250)
    manual = controller.state.manual
    assert isinstance(manual, Boost)
    assert manual.source is Source.VOICE
    assert manual.level == 250
    assert speech(response) == "Ventilation boosted to 250 m³/h until 13:31."
    assert response.response_type is intent.IntentResponseType.ACTION_DONE

    response = await handle(hass, INTENT_STATUS)
    assert response.response_type is intent.IntentResponseType.QUERY_ANSWER
    assert speech(response) == (
        "Ventilation: Boost until 13:31, 250 m³/h, reason: Boost."
    )

    response = await handle(hass, INTENT_PAUSE, duration=60)
    manual = controller.state.manual
    assert isinstance(manual, Pause)
    assert manual.source is Source.VOICE
    assert speech(response) == "Ventilation paused until 14:01."

    response = await handle(hass, INTENT_PAUSE)
    manual = controller.state.manual
    assert isinstance(manual, Pause)
    assert manual.until is None
    assert speech(response) == "Ventilation paused until you resume it."

    response = await handle(hass, INTENT_RESUME)
    assert controller.state.manual is None
    assert speech(response) == "Ventilation is back to Automatic."

    response = await handle(hass, INTENT_STATUS)
    decision = controller.current_decision
    assert decision is not None
    assert decision.mode is Mode.AUTOMATIC
    assert speech(response).startswith("Ventilation: Automatic, ")
    assert speech(response).endswith(" m³/h, reason: Minimum.")


@pytest.mark.usefixtures("entities")
async def test_c16_boost_defaults_and_german(
    hass: HomeAssistant, unit: MockUnit
) -> None:
    """Without slots the boost uses the parameter entities; speech in German."""
    entry = await setup_entry(hass)
    controller = controller_of(entry)

    response = await handle(hass, INTENT_BOOST, language="de")
    manual = controller.state.manual
    assert isinstance(manual, Boost)
    assert manual.level == controller.boost_level
    assert speech(response) == (
        f"Ventilation läuft bis 13:30 verstärkt mit {manual.level:.0f} m³/h."
    )
    response = await handle(hass, INTENT_STATUS, language="de-CH")
    assert speech(response).startswith("Ventilation: Boost bis 13:30, ")
    assert speech(response).endswith("Grund: Boost.")


@pytest.mark.usefixtures("entities")
async def test_c16_errors(hass: HomeAssistant, unit: MockUnit) -> None:
    """Disabled control, a level out of range and entry resolution fail with text."""
    with pytest.raises(IntentFailed) as err:
        await handle(hass, INTENT_STATUS)
    assert err.value.code == "no_entry"
    assert str(err.value) == "No ventilation is set up."

    entry = await setup_entry(hass)
    controller = controller_of(entry)

    with pytest.raises(IntentFailed) as err:
        await handle(hass, INTENT_BOOST, level=5000)
    assert err.value.code == "level_out_of_range"
    assert str(err.value) == "The level must be between 80 and 400 m³/h."

    await controller.async_command(
        ControlSwitchCommand(source=Source.BUTTON, enabled=False)
    )
    with pytest.raises(IntentFailed) as err:
        await handle(hass, INTENT_PAUSE, language="de")
    assert err.value.code == "control_disabled"
    assert err.value.response_key == "better_home_ventilation_control_disabled"
    assert str(err.value) == "Die Regelung von Ventilation ist ausgeschaltet."

    # The status is still answered while control is disabled.
    response = await handle(hass, INTENT_STATUS)
    assert speech(response).startswith("Ventilation: Control off, ")

    await second_entry(hass)
    with pytest.raises(IntentFailed) as err:
        await handle(hass, INTENT_STATUS)
    assert err.value.code == "entry_required"

    # The name selects the entry (case and blanks are ignored).
    response = await handle(hass, INTENT_STATUS, name="  attic ")
    assert speech(response).startswith("Attic: ")

    with pytest.raises(IntentFailed) as err:
        await handle(hass, INTENT_STATUS, name="Cellar")
    assert err.value.code == "entry_not_found"
    assert str(err.value) == "There is no ventilation called Cellar."

    unloaded = MockConfigEntry(domain=DOMAIN, title="Cellar", data={}, options={})
    unloaded.add_to_hass(hass)
    with pytest.raises(IntentFailed) as err:
        await handle(hass, INTENT_STATUS, name="cellar")
    assert err.value.code == "entry_not_loaded"
    assert str(err.value) == "Cellar is not loaded."


async def test_status_before_start(hass: HomeAssistant) -> None:
    """Before the first evaluation the status says so."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Ventilation",
        data={},
        options=entry_options(),
        version=1,
        minor_version=1,
    )
    entry.add_to_hass(hass)
    hass.set_state(hass.state.__class__.starting)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    response = await handle(hass, INTENT_STATUS)
    assert speech(response) == "Ventilation has not started yet."


def _major_minor(version: str) -> tuple[int, int]:
    major, minor, *_ = version.split(".")
    return int(major), int(minor)


@pytest.mark.usefixtures("entities")
async def test_q2_intents_are_llm_tools(hass: HomeAssistant, unit: MockUnit) -> None:
    """The Assist API offers the four intents as tools to LLM agents (Q2).

    HA 2026.4: every registered intent handler is a tool, named after the
    intent. HA 2026.10: the `llm` platform of the integration contributes
    them with the `better_home_ventilation__` prefix.
    """
    entry = await setup_entry(hass)
    if _major_minor(ha_version) >= (2026, 10):
        assert await async_setup_component(hass, "llm", {})
        names = {intent_type: f"{DOMAIN}__{intent_type}" for intent_type in INTENTS}
        assistant: str | None = "conversation"
    else:
        names = {intent_type: intent_type for intent_type in INTENTS}
        assistant = None
    context = llm.LLMContext(
        platform="test",
        context=None,
        language="en",
        assistant=assistant,  # type: ignore[arg-type]
        device_id=None,
    )
    api = await llm.async_get_api(hass, llm.LLM_API_ASSIST, context)
    tools = {tool.name: tool for tool in api.tools}
    for name in names.values():
        assert name in tools, name
        assert tools[name].description
    boost = tools[names[INTENT_BOOST]]
    assert {str(key) for key in boost.parameters.schema} == {
        "name",
        "duration",
        "level",
    }

    # Called like `APIInstance.async_call_tool` does (that one needs the
    # conversation integration, whose requirements are not installed here).
    result = await boost.async_call(
        hass,
        llm.ToolInput(tool_name=names[INTENT_BOOST], tool_args={"duration": 15}),
        context,
    )
    data: Any = getattr(result, "data", result)  # ToolResult since 2026.10
    assert data["speech"]["plain"]["speech"].startswith("Ventilation boosted to")
    assert isinstance(controller_of(entry).state.manual, Boost)
