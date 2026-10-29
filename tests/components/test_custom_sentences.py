"""The example custom sentences match the intents (BHV-22, Q3)."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from homeassistant.util.yaml import parse_yaml
import pytest

from custom_components.better_home_ventilation.intent import (
    ERROR_CODES,
    INTENT_HANDLERS,
    RESPONSE_KEY_PREFIX,
)

EXAMPLES = Path(__file__).parents[2] / "examples" / "custom_sentences"
SLOT = re.compile(r"\{(\w+)(?::(\w+))?\}")


def _load(language: str) -> dict[str, Any]:
    path = EXAMPLES / language / "better_home_ventilation.yaml"
    data = parse_yaml(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


@pytest.mark.parametrize("language", ["en", "de"])
def test_sentences_use_the_intents_and_their_slots(language: str) -> None:
    """Every intent has sentences; slots exist and their lists are defined."""
    data = _load(language)
    assert data["language"] == language
    handlers = {handler.intent_type: handler() for handler in INTENT_HANDLERS}
    assert set(data["intents"]) == set(handlers)
    lists = set(data["lists"])
    for intent_type, intent_data in data["intents"].items():
        slots = {str(key) for key in handlers[intent_type].slot_schema}
        sentences = [
            sentence for block in intent_data["data"] for sentence in block["sentences"]
        ]
        assert sentences, intent_type
        for sentence in sentences:
            for list_name, slot_name in SLOT.findall(sentence):
                assert list_name in lists, sentence
                assert (slot_name or list_name) in slots, sentence


@pytest.mark.parametrize("language", ["en", "de"])
def test_error_responses_cover_every_error(language: str) -> None:
    """Every intent error has a response for the default agent."""
    errors = _load(language)["responses"]["errors"]
    assert set(errors) == {f"{RESPONSE_KEY_PREFIX}_{code}" for code in ERROR_CODES}
    assert all(errors.values())


def test_spec_examples_are_covered() -> None:
    """The German examples of the spec (04-interface.md#intents) are present."""
    text = str(_load("de")["intents"])
    for fragment in (
        "Lüftung (Boost",
        "Lüftung {bhv_minutes:duration} Minuten (stärker",
        "Lüftung [für] {bhv_pause_hours:duration} (Stunde|Stunden) (aus",
        "Lüftung wieder (normal",
        "was macht die Lüftung",
    ):
        assert fragment in text, fragment
