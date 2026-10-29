"""Voice intents ``BetterVentilationBoost/Pause/Resume/Status`` (BHV-22, phase 0.2).

docs/spec/04-interface.md#intents. The handlers are registered in ``async_setup``
(and by the ``intent`` platform hook, see :func:`async_setup_intents`). Each sets ``description`` and ``slot_schema``, so LLM
conversation agents can call them through the Assist API (Q2: offered
automatically up to HA 2026.4, through the ``llm`` platform of this
integration in 2026.10). The default conversation agent reaches them only
through custom sentences (Q3), see ``examples/custom_sentences/``.

Speech is built from the ``common`` translations in the language of the
request (English as fallback); mode and reason names come from the state
translations of the ``mode`` and ``reason`` sensors. Errors (control switched
off, no or several matching entries) are ``IntentHandleError``s with a
translated message and a ``response_key`` for the error responses of the
custom sentences.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Final

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, intent
from homeassistant.helpers.translation import async_get_translations
from homeassistant.util import dt as dt_util

if TYPE_CHECKING:
    # Since HA 2026.9 `voluptuous` is an alias of `probatio` at runtime
    # (docs/spec/05-architecture.md, compatibility); type check against it.
    import probatio as vol
else:
    import voluptuous as vol

from .const import DOMAIN
from .controller import BetterVentilationConfigEntry, VentilationController
from .engine.models import (
    Boost,
    BoostCommand,
    Command,
    Pause,
    PauseCommand,
    ResumeCommand,
    Source,
)
from .notifications import async_load_texts, format_time

INTENT_BOOST: Final = "BetterVentilationBoost"
INTENT_PAUSE: Final = "BetterVentilationPause"
INTENT_RESUME: Final = "BetterVentilationResume"
INTENT_STATUS: Final = "BetterVentilationStatus"

SLOT_NAME: Final = "name"
SLOT_DURATION: Final = "duration"
SLOT_LEVEL: Final = "level"

#: Prefix of the error ``response_key``s (``<prefix>_<code>``).
RESPONSE_KEY_PREFIX: Final = DOMAIN
#: Codes of :class:`IntentFailed` (text ``common.intent_error_<code>``).
ERROR_CODES: Final = (
    "control_disabled",
    "level_out_of_range",
    "entry_required",
    "entry_not_found",
    "no_entry",
    "entry_not_loaded",
)

_ENTITY: Final = f"component.{DOMAIN}.entity.sensor."

_NAME_SLOT: Final[dict[Any, Any]] = {
    vol.Optional(
        SLOT_NAME,
        description=(
            "Name of the ventilation (the title of its entry). Only needed if "
            "there is more than one."
        ),
    ): cv.string,
}


class IntentFailed(intent.IntentHandleError):
    """A translated error of a ventilation intent; ``code`` names the reason."""

    def __init__(self, message: str, code: str) -> None:
        """Store the message, the code and the response key."""
        super().__init__(message, response_key=f"{RESPONSE_KEY_PREFIX}_{code}")
        self.code = code


def _text(texts: Mapping[str, str], key: str, **placeholders: Any) -> str:
    """Return a translated text with its placeholders filled in."""
    template = texts.get(key, key)
    try:
        return template.format(**placeholders)
    except KeyError, IndexError, ValueError:
        return template


def _flow(value: float) -> str:
    """Format a flow in m³/h without decimals."""
    return f"{value:.0f}"


def _normalize(name: str) -> str:
    """Normalize an entry title or a spoken name for comparison."""
    return " ".join(name.casefold().split())


def _language(language: str | None, hass: HomeAssistant) -> str:
    """Return the base language of a request (``de-CH`` -> ``de``)."""
    return (language or hass.config.language).partition("-")[0]


async def _async_state_names(hass: HomeAssistant, language: str) -> dict[str, str]:
    """Return the state translations of the sensors, English as fallback."""
    names: dict[str, str] = {}
    for lang in dict.fromkeys(("en", language)):
        translations = await async_get_translations(hass, lang, "entity", {DOMAIN})
        names.update(
            {
                key.removeprefix(_ENTITY): value
                for key, value in translations.items()
                if key.startswith(_ENTITY)
            }
        )
    return names


@callback
def async_resolve_entry(
    hass: HomeAssistant, name: str | None, texts: Mapping[str, str]
) -> VentilationController:
    """Return the controller of the entry the request is about.

    ``name`` is compared with the entry titles (case and blanks ignored).
    Without a name exactly one (enabled) entry must exist.
    """
    entries: list[BetterVentilationConfigEntry] = hass.config_entries.async_entries(
        DOMAIN, include_ignore=False, include_disabled=False
    )
    if name is not None:
        wanted = _normalize(name)
        entries = [entry for entry in entries if _normalize(entry.title) == wanted]
        if not entries:
            raise IntentFailed(
                _text(texts, "intent_error_entry_not_found", name=name),
                "entry_not_found",
            )
    elif not entries:
        raise IntentFailed(_text(texts, "intent_error_no_entry"), "no_entry")
    if len(entries) > 1:
        raise IntentFailed(
            _text(texts, "intent_error_entry_required"), "entry_required"
        )
    entry = entries[0]
    if entry.state is not ConfigEntryState.LOADED:
        raise IntentFailed(
            _text(texts, "intent_error_entry_not_loaded", name=entry.title),
            "entry_not_loaded",
        )
    return entry.runtime_data


class VentilationIntentHandler(intent.IntentHandler):
    """Base of the ventilation intents: slots, entry resolution, speech."""

    #: Short title of the intent (LLM tool title).
    title: str
    #: Slots in addition to ``name``.
    extra_slots: Mapping[Any, Any] = {}
    response_type = intent.IntentResponseType.ACTION_DONE

    @property
    def slot_schema(self) -> dict[Any, Any]:
        """Return the slot schema (``name`` plus the intent's own slots)."""
        return {**_NAME_SLOT, **self.extra_slots}

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        """Resolve the entry, run the intent and answer in the request language."""
        hass = intent_obj.hass
        slots = self.async_validate_slots(intent_obj.slots)
        language = _language(intent_obj.language, hass)
        texts = await async_load_texts(hass, language)
        controller = async_resolve_entry(hass, _slot(slots, SLOT_NAME), texts)
        speech = await self._async_run(
            controller, slots, texts, await _async_state_names(hass, language)
        )
        response = intent_obj.create_response()
        response.response_type = self.response_type
        response.async_set_speech(speech)
        return response

    async def _async_run(
        self,
        controller: VentilationController,
        slots: Mapping[str, Any],
        texts: Mapping[str, str],
        states: Mapping[str, str],
    ) -> str:
        """Run the intent and return the speech."""
        raise NotImplementedError

    async def _async_command(
        self,
        controller: VentilationController,
        command: Command,
        texts: Mapping[str, str],
    ) -> None:
        """Run a command; a rejection becomes a translated intent error."""
        try:
            await controller.async_command(command)
        except ServiceValidationError as err:
            code = str(err.translation_key)
            raise IntentFailed(
                _text(
                    texts,
                    f"intent_error_{code}",
                    name=controller.entry.title,
                    min=_flow(controller.options.min_flow),
                    max=_flow(controller.options.max_flow),
                ),
                code,
            ) from err


def _slot(slots: Mapping[str, Any], name: str) -> Any:
    """Return the value of a slot, or None if it is not set."""
    slot = slots.get(name)
    return slot.get("value") if isinstance(slot, Mapping) else None


def _minutes(slots: Mapping[str, Any]) -> timedelta | None:
    minutes = _slot(slots, SLOT_DURATION)
    return timedelta(minutes=minutes) if minutes is not None else None


def _mode_name(states: Mapping[str, str], mode: str) -> str:
    return states.get(f"mode.state.{mode}", mode)


class BoostIntentHandler(VentilationIntentHandler):
    """``BetterVentilationBoost``: boost with optional duration and level."""

    intent_type = INTENT_BOOST
    title = "Boost ventilation"
    description = (
        "Boosts the home ventilation (raises the flow) for a while. Without a "
        "duration or level, the boost duration and level set in Home Assistant "
        "are used. Boosting again extends the boost."
    )
    extra_slots: Mapping[Any, Any] = {
        vol.Optional(SLOT_DURATION, description="Duration in minutes"): vol.All(
            vol.Coerce(int), vol.Range(min=5, max=720)
        ),
        vol.Optional(
            SLOT_LEVEL,
            description="Flow in m³/h, between the minimum and the maximum flow",
        ): vol.Coerce(float),
    }

    async def _async_run(
        self,
        controller: VentilationController,
        slots: Mapping[str, Any],
        texts: Mapping[str, str],
        states: Mapping[str, str],
    ) -> str:
        """Boost and confirm level and end."""
        await self._async_command(
            controller,
            BoostCommand(
                source=Source.VOICE,
                duration=_minutes(slots),
                level=_slot(slots, SLOT_LEVEL),
                default_duration=controller.boost_duration,
                default_level=controller.boost_level,
            ),
            texts,
        )
        manual = controller.state.manual
        if not isinstance(manual, Boost):  # pragma: no cover - accepted above
            return status_speech(controller, texts, states)
        return _text(
            texts,
            "intent_boost",
            name=controller.entry.title,
            level=_flow(manual.level),
            time=format_time(manual.until, dt_util.now()),
        )


class PauseIntentHandler(VentilationIntentHandler):
    """``BetterVentilationPause``: pause with optional duration."""

    intent_type = INTENT_PAUSE
    title = "Pause ventilation"
    description = (
        "Pauses (switches off) the home ventilation for a duration in minutes, "
        "or until it is resumed if no duration is given."
    )
    extra_slots: Mapping[Any, Any] = {
        vol.Optional(SLOT_DURATION, description="Duration in minutes"): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=10080)
        ),
    }

    async def _async_run(
        self,
        controller: VentilationController,
        slots: Mapping[str, Any],
        texts: Mapping[str, str],
        states: Mapping[str, str],
    ) -> str:
        """Pause and confirm the end."""
        await self._async_command(
            controller,
            PauseCommand(source=Source.VOICE, duration=_minutes(slots)),
            texts,
        )
        manual = controller.state.manual
        if not isinstance(manual, Pause):  # pragma: no cover - accepted above
            return status_speech(controller, texts, states)
        if manual.until is None:
            return _text(texts, "intent_pause_indefinite", name=controller.entry.title)
        return _text(
            texts,
            "intent_pause",
            name=controller.entry.title,
            time=format_time(manual.until, dt_util.now()),
        )


class ResumeIntentHandler(VentilationIntentHandler):
    """``BetterVentilationResume``: end a boost or pause."""

    intent_type = INTENT_RESUME
    title = "Resume ventilation"
    description = (
        "Ends a boost or a pause of the home ventilation; it returns to "
        "automatic control."
    )

    async def _async_run(
        self,
        controller: VentilationController,
        slots: Mapping[str, Any],
        texts: Mapping[str, str],
        states: Mapping[str, str],
    ) -> str:
        """Resume and name the mode it runs in now."""
        await self._async_command(controller, ResumeCommand(source=Source.VOICE), texts)
        decision = controller.current_decision
        mode = str(decision.mode) if decision is not None else "automatic"
        return _text(
            texts,
            "intent_resume",
            name=controller.entry.title,
            mode=_mode_name(states, mode),
        )


class StatusIntentHandler(VentilationIntentHandler):
    """``BetterVentilationStatus``: mode, setpoint, reason and end time."""

    intent_type = INTENT_STATUS
    title = "Ventilation status"
    description = (
        "Tells the current state of the home ventilation: its mode, the flow "
        "in m³/h, the reason for it and when a boost or pause ends."
    )
    response_type = intent.IntentResponseType.QUERY_ANSWER

    async def _async_run(
        self,
        controller: VentilationController,
        slots: Mapping[str, Any],
        texts: Mapping[str, str],
        states: Mapping[str, str],
    ) -> str:
        """Describe the last decision."""
        return status_speech(controller, texts, states)


def status_speech(
    controller: VentilationController,
    texts: Mapping[str, str],
    states: Mapping[str, str],
) -> str:
    """Return the status speech: mode, setpoint, reason and end time."""
    name = controller.entry.title
    decision = controller.current_decision
    if decision is None:
        return _text(texts, "intent_status_not_started", name=name)
    placeholders = {
        "name": name,
        "mode": _mode_name(states, str(decision.mode)),
        "setpoint": _flow(decision.setpoint),
        "reason": states.get(f"reason.state.{decision.reason}", str(decision.reason)),
    }
    if decision.ends_at is not None:
        return _text(
            texts,
            "intent_status_until",
            time=format_time(decision.ends_at, dt_util.now()),
            **placeholders,
        )
    return _text(texts, "intent_status", **placeholders)


INTENT_HANDLERS: Final[tuple[type[VentilationIntentHandler], ...]] = (
    BoostIntentHandler,
    PauseIntentHandler,
    ResumeIntentHandler,
    StatusIntentHandler,
)


async def async_setup_intents(hass: HomeAssistant) -> None:
    """Register the intent handlers; called in ``async_setup``.

    This module is also the ``intent`` platform of the integration: when the
    ``intent`` integration is loaded it calls this function as well
    (``homeassistant.components.intent``, ``_async_process_intent``, HA 2026.4
    and 2026.10). Handlers that are registered already are kept, so the
    second call does not overwrite them.
    """
    registered = {handler.intent_type for handler in intent.async_get(hass)}
    for handler in INTENT_HANDLERS:
        if handler.intent_type not in registered:
            intent.async_register(hass, handler())
