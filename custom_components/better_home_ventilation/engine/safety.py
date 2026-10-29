"""Safety shutdown: triggers, details, filters and keys (BHV-40, BHV-45).

docs/spec/03-modes.md#safety-shutdown separates three questions:

- Is the source active? The **trigger** (:func:`entity_active`), from the state.
- What are the warnings behind it? The **details provider** (HA glue); it
  builds :class:`~.models.WarningDetails` with :func:`make_details`, which
  strips HTML and normalises the severity.
- Does a warning count? The **filters** (:func:`warning_counts`).

:func:`collect_warnings` combines them for all sources of one snapshot and
assigns warning and incident keys. Pure: no I/O, no clock, no logging.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
import html
import math
import re
from types import MappingProxyType
from typing import Final

from . import options as opt
from .models import (
    InputValue,
    Severity,
    Snapshot,
    WarningDetails,
    WarningInfo,
    WarningSource,
    is_unavailable,
)

# --- HTML to text ------------------------------------------------------------

_BREAK_TAG: Final = re.compile(r"<\s*(?:br|/?p|/?div|/?li|/?tr)\b[^>]*>", re.IGNORECASE)
_TAG: Final = re.compile(r"<[^>]*>")


def html_to_text(value: object) -> str | None:
    """Strip HTML tags, unescape entities and tidy whitespace.

    Line breaks (``<br>``, paragraphs, list items) become new lines; runs of
    blanks collapse to one; empty results are None.
    """
    if value is None:
        return None
    text = _BREAK_TAG.sub("\n", str(value))
    text = html.unescape(_TAG.sub("", text))
    lines = (" ".join(line.split()) for line in text.splitlines())
    return "\n".join(line for line in lines if line) or None


# --- Severity normalisation --------------------------------------------------

#: DWD warning levels and Meteoalarm awareness levels (0 = no warning).
_LEVELS: Final = MappingProxyType(
    {
        1: Severity.MINOR,
        2: Severity.MODERATE,
        3: Severity.SEVERE,
        4: Severity.EXTREME,
    }
)
#: Meteoalarm awareness colours.
_COLOURS: Final = MappingProxyType(
    {"yellow": Severity.MODERATE, "orange": Severity.SEVERE, "red": Severity.EXTREME}
)
_PART_SEPARATOR: Final = re.compile(r"[;,|]")
#: CAP severity words.
_CAP_WORDS: Final = frozenset(severity.value for severity in Severity)


def _level(number: float) -> Severity | None:
    if not math.isfinite(number) or number != int(number):
        return None
    return _LEVELS.get(int(number))


def normalise_severity(value: object) -> Severity | None:
    """Normalise a provider severity (03-modes.md#details-providers).

    CAP words (any case), DWD levels 1-4, Meteoalarm awareness levels and
    colours, also combined as in ``"3; orange; Severe"`` (the first part that
    can be read wins). Anything else is None, which the severity filter treats
    as ``extreme`` (fail-safe).
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Severity):
        return value
    if isinstance(value, int | float):
        return _level(float(value))
    for raw in _PART_SEPARATOR.split(str(value)):
        part = raw.strip().casefold()
        if part in _CAP_WORDS:
            return Severity(part)
        if part in _COLOURS:
            return _COLOURS[part]
        try:
            level = _level(float(part))
        except ValueError:
            continue
        if level is not None:
            return level
    return None


def make_details(
    *,
    key: object = None,
    headline: object = None,
    text: object = None,
    actions: object = None,
    severity: object = None,
    sender: object = None,
) -> WarningDetails:
    """Build :class:`WarningDetails` from raw provider values.

    Text fields are converted with :func:`html_to_text`, the severity with
    :func:`normalise_severity`; an empty key is None.
    """
    key_text = str(key).strip() if key is not None else ""
    return WarningDetails(
        key=key_text or None,
        headline=html_to_text(headline),
        text=html_to_text(text),
        actions=html_to_text(actions),
        severity=normalise_severity(severity),
        sender=html_to_text(sender),
    )


# --- Triggers ----------------------------------------------------------------


def _number(state: str) -> float | None:
    try:
        number = float(state)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def entity_active(source: WarningSource, value: InputValue | None) -> bool | None:
    """Evaluate the trigger of ``source`` for one entity.

    Returns True (active), False (inactive) or None (missing,
    ``unavailable``/``unknown``: never active, but a problem). ``on_off``: the
    state is ``on``; ``numeric``: the state is a number ≥ ``threshold``;
    ``state_list``: the state is one of ``states`` (case-insensitive).
    """
    state = value.state if value is not None else None
    if state is None or is_unavailable(state):
        return None
    state = state.strip()
    if source.trigger == opt.TRIGGER_NUMERIC:
        number = _number(state)
        return (
            number is not None
            and source.threshold is not None
            and number >= source.threshold
        )
    if source.trigger == opt.TRIGGER_STATE_LIST:
        wanted = {item.casefold() for item in source.states}
        return state.casefold() in wanted
    return state.casefold() == "on"


# --- Filters -----------------------------------------------------------------


def is_regex(keyword: str) -> bool:
    """Return True for a ``/…/`` keyword (a regular expression)."""
    return (
        len(keyword) > len("//") and keyword.startswith("/") and keyword.endswith("/")
    )


def invalid_regex(keyword: str) -> bool:
    """Return True for a ``/…/`` keyword that does not compile (``invalid_regex``)."""
    if not is_regex(keyword):
        return False
    try:
        re.compile(keyword[1:-1], re.IGNORECASE)
    except re.error:
        return True
    return False


def keyword_matches(keyword: str, text: str) -> bool:
    """Match one keyword: ``/…/`` with ``re.search`` (IGNORECASE), else a substring.

    Substrings are compared case-insensitively. A regular expression that
    does not compile (rejected by the subentry flow) is matched as a plain
    substring, slashes included.
    """
    if is_regex(keyword):
        try:
            return re.search(keyword[1:-1], text, re.IGNORECASE) is not None
        except re.error:
            pass
    return keyword.casefold() in text.casefold()


def _any_match(keywords: Iterable[str], text: str) -> bool:
    return any(keyword_matches(keyword, text) for keyword in keywords)


def _full_text(details: WarningDetails) -> str:
    return "\n".join(
        part for part in (details.headline, details.text, details.actions) if part
    )


def warning_counts(source: WarningSource, details: WarningDetails) -> bool:
    """Apply the filters of ``source`` to one warning (03-modes.md#filters).

    Exclude keywords (headline + text + actions) are checked first; then the
    severity minimum (None counts as ``extreme``); then the warning counts if
    a headline keyword occurs in the headline or a text keyword in headline +
    text + actions. Both include lists empty: every warning that is not
    excluded counts.
    """
    full = _full_text(details)
    if _any_match(source.exclude_keywords, full):
        return False
    if source.min_severity is not None:
        severity = details.severity or Severity.EXTREME
        if severity.rank < source.min_severity.rank:
            return False
    if not source.headline_keywords and not source.text_keywords:
        return True
    return _any_match(source.headline_keywords, details.headline or "") or (
        _any_match(source.text_keywords, full)
    )


# --- Warning key and incident key --------------------------------------------

#: Update and all-clear prefixes removed from the headline for the incident key.
INCIDENT_PREFIXES: Final = (
    re.compile(r"^\d+\.\s*aktualisierung:\s*"),
    re.compile(r"^entwarnung:\s*"),
    re.compile(r"^update:\s*"),
    re.compile(r"^all clear:\s*"),
)


def incident_headline(headline: str) -> str:
    """Lower-case the headline and remove leading update/all-clear prefixes."""
    text = headline.strip().lower()
    changed = True
    while changed:
        changed = False
        for prefix in INCIDENT_PREFIXES:
            stripped = prefix.sub("", text, count=1).strip()
            if stripped != text:
                text, changed = stripped, True
    return text


def fallback_key_prefix(source_id: str, entity_id: str) -> str:
    """Prefix of the warning key of a warning without provider key."""
    return f"{source_id}:{entity_id}:"


def warning_key(
    source_id: str, details_key: str | None, entity_id: str, activated: datetime
) -> str:
    """Return the warning key (03-modes.md#warning-key-and-incident).

    ``subentry_id:key`` if the provider knows a key (NINA id, CAP id, DWD
    ``headline:start``), else ``subentry_id:entity_id:<ISO activation time>``.
    A NINA key does not contain the entity, so a slot change keeps the key.
    """
    if details_key:
        return f"{source_id}:{details_key}"
    return f"{fallback_key_prefix(source_id, entity_id)}{activated.isoformat()}"


def incident_key(source_id: str, headline: str | None, key: str) -> str:
    """Return the incident key: ``subentry_id:<normalised headline>`` or the warning key."""
    if headline and (normalised := incident_headline(headline)):
        return f"{source_id}:{normalised}"
    return key


# --- Collecting the warnings of a snapshot -----------------------------------


@dataclass(frozen=True, kw_only=True)
class SourceReport:
    """Warnings of all sources in one snapshot, with the source problems.

    ``unavailable`` names one unavailable entity per source
    (``warning_source_unavailable``); ``details_failed`` the active entities
    whose details call failed (``warning_details_failed``).
    """

    warnings: Mapping[str, WarningInfo]
    unavailable: tuple[str, ...] = ()
    details_failed: tuple[str, ...] = ()


def _fallback_key(
    source: WarningSource,
    entity_id: str,
    previous: Mapping[str, WarningInfo],
    now: datetime,
) -> str:
    """Key of a warning without provider key; an existing one is kept.

    The activation time in the key is the first evaluation that saw the
    entity active; while it stays active (also across a restart, through the
    loaded warnings) the key does not change, so an override keeps applying.
    """
    prefix = fallback_key_prefix(source.subentry_id, entity_id)
    for key in previous:
        if key.startswith(prefix):
            return key
    return warning_key(source.subentry_id, None, entity_id, now)


def _since(key: str, previous: Mapping[str, WarningInfo], now: datetime) -> datetime:
    known = previous.get(key)
    return known.since if known is not None else now


def _without_details(
    source: WarningSource,
    entity_id: str,
    previous: Mapping[str, WarningInfo],
    now: datetime,
) -> tuple[str, WarningInfo]:
    key = _fallback_key(source, entity_id, previous, now)
    return key, WarningInfo(
        source_id=source.subentry_id,
        source_name=source.name,
        entity_id=entity_id,
        incident_key=key,
        headline=None,
        text=None,
        recommended_actions=None,
        severity=None,
        sender=None,
        since=_since(key, previous, now),
        details=False,
    )


def _with_details(
    source: WarningSource,
    entity_id: str,
    details: WarningDetails,
    previous: Mapping[str, WarningInfo],
    now: datetime,
) -> tuple[str, WarningInfo]:
    key = (
        warning_key(source.subentry_id, details.key, entity_id, now)
        if details.key
        else _fallback_key(source, entity_id, previous, now)
    )
    return key, WarningInfo(
        source_id=source.subentry_id,
        source_name=source.name,
        entity_id=entity_id,
        incident_key=incident_key(source.subentry_id, details.headline, key),
        headline=details.headline,
        text=details.text,
        recommended_actions=details.actions,
        severity=details.severity,
        sender=details.sender,
        since=_since(key, previous, now),
    )


def collect_warnings(
    sources: Sequence[WarningSource],
    snapshot: Snapshot,
    previous: Mapping[str, WarningInfo],
) -> SourceReport:
    """Return every warning that counts, keyed by warning key.

    Per source entity: the trigger decides whether it is active. An active
    entity without details (provider ``none``, a failed details call, no or
    empty details) is one warning without details and always counts; with
    details, every warning that passes the filters of its source counts.
    ``previous`` are the warnings active so far; their ``since`` and fallback
    keys are kept.
    """
    now = snapshot.now
    warnings: dict[str, WarningInfo] = {}
    unavailable: list[str] = []
    failed: list[str] = []
    for source in sources:
        first_unavailable: str | None = None
        for entity_id in source.entities:
            active = entity_active(source, snapshot.states.get(entity_id))
            if active is None:
                first_unavailable = first_unavailable or entity_id
                continue
            if not active:
                continue
            details: tuple[WarningDetails, ...] | None = None
            if entity_id in snapshot.details_failed:
                failed.append(entity_id)
            elif source.details != opt.DETAILS_NONE:
                details = snapshot.warning_details.get(entity_id)
            if not details:
                key, info = _without_details(source, entity_id, previous, now)
                warnings[key] = info
                continue
            for item in details:
                if warning_counts(source, item):
                    key, info = _with_details(source, entity_id, item, previous, now)
                    warnings[key] = info
        if first_unavailable is not None:
            unavailable.append(first_unavailable)
    return SourceReport(
        warnings=warnings,
        unavailable=tuple(unavailable),
        details_failed=tuple(failed),
    )


def by_priority(warnings: Iterable[WarningInfo]) -> tuple[WarningInfo, ...]:
    """Sort warnings: most severe first (None = extreme), then newest first."""
    return tuple(
        sorted(
            warnings,
            key=lambda w: (
                -(w.severity or Severity.EXTREME).rank,
                -w.since.timestamp(),
                w.incident_key,
            ),
        )
    )


def warning_title(warning: WarningInfo) -> str:
    """Headline of a warning, or the source name for a warning without details."""
    return warning.headline or warning.source_name


# --- Suggested filters -------------------------------------------------------

PROVIDER_NINA: Final = "nina"
PROVIDER_DWD: Final = "dwd_weather_warnings"
PROVIDER_ENVIRONMENT_CANADA: Final = "environment_canada"
PROVIDER_ATTRIBUTES: Final = "attributes"
PROVIDER_NONE: Final = "none"

#: German suggestions for NINA (docs/spec/01-configuration.md, basis there).
NINA_DE: Final = MappingProxyType(
    {
        opt.CONF_HEADLINE_KEYWORDS: (
            "/brand(?!enburg)/",
            "Rauch",
            "Geruchsbelästigung",
            "Geruchsentwicklung",
            "Gefahrstoff",
            "Gefahrgut",
            "Schadstoff",
            "Gasaustritt",
            "Gasleck",
            "Gasgeruch",
            "Gaswolke",
            "Erdgas",
            "Chemie",
            "chemisch",
            "giftig",
            "Giftwolke",
            "Explosion",
            "radioaktiv",
            "Kernkraft",
            "Atom",
        ),
        opt.CONF_TEXT_KEYWORDS: ("Lüftung", "Klimaanlage"),
        opt.CONF_EXCLUDE_KEYWORDS: (
            "Entwarnung",
            "Probealarm",
            "Probewarnung",
            "Fehlauslösung",
            "Warntag",
            "Übung",
            "wieder öffnen",
            "wieder geöffnet",
            "wieder eingeschaltet",
        ),
    }
)

#: Suggestions for NINA in every other language.
NINA_OTHER: Final = MappingProxyType(
    {
        opt.CONF_HEADLINE_KEYWORDS: (
            "fire",
            "smoke",
            "odour",
            "odor",
            "hazardous",
            "chemical",
            "toxic",
            "gas leak",
            "explosion",
            "radioactive",
            "nuclear",
        ),
        opt.CONF_TEXT_KEYWORDS: ("ventilation", "air conditioning"),
        opt.CONF_EXCLUDE_KEYWORDS: (
            "all clear",
            "test",
            "exercise",
            "drill",
            "can be reopened",
            "switched on again",
        ),
    }
)

_EMPTY_FILTERS: Final = MappingProxyType(
    {
        opt.CONF_HEADLINE_KEYWORDS: (),
        opt.CONF_TEXT_KEYWORDS: (),
        opt.CONF_EXCLUDE_KEYWORDS: (),
    }
)


def suggested_filters(
    provider: str, language: str
) -> Mapping[str, tuple[str, ...]] | None:
    """Keyword lists pre-filled for a new source (01-configuration.md).

    None for provider ``none`` (the keyword fields are not shown). NINA has
    German suggestions for ``de`` and English ones otherwise; every other
    provider starts with empty lists.
    """
    if provider == PROVIDER_NONE:
        return None
    if provider == PROVIDER_NINA:
        return (
            NINA_DE
            if language.split("-", maxsplit=1)[0].lower() == "de"
            else NINA_OTHER
        )
    return _EMPTY_FILTERS
