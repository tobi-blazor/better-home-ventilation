"""Triggers, details, filters and keys of the safety shutdown (BHV-40, BHV-45).

docs/spec/03-modes.md#safety-shutdown; pure functions of ``engine.safety``.
"""

from __future__ import annotations

from datetime import timedelta
import json
from pathlib import Path
from typing import Any

import pytest

from engine import options as opt
from engine.models import (
    InputValue,
    Severity,
    WarningDetails,
    WarningInfo,
    WarningSource,
    parse_min_severity,
)
from engine.safety import (
    NINA_DE,
    NINA_OTHER,
    PROVIDER_ATTRIBUTES,
    PROVIDER_DWD,
    PROVIDER_NINA,
    PROVIDER_NONE,
    by_priority,
    entity_active,
    html_to_text,
    incident_headline,
    incident_key,
    invalid_regex,
    is_regex,
    keyword_matches,
    make_details,
    normalise_severity,
    notification_text,
    suggested_filters,
    warning_counts,
    warning_key,
)

from .helpers import T0

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "warning_texts_de.json"


def _source(**overrides: Any) -> WarningSource:
    values: dict[str, Any] = {
        "subentry_id": "src1",
        "name": "Test source",
        "entities": ("binary_sensor.warning_1",),
    }
    values.update(overrides)
    return WarningSource(**values)


def _nina_de(**overrides: Any) -> WarningSource:
    return _source(
        headline_keywords=NINA_DE[opt.CONF_HEADLINE_KEYWORDS],
        text_keywords=NINA_DE[opt.CONF_TEXT_KEYWORDS],
        exclude_keywords=NINA_DE[opt.CONF_EXCLUDE_KEYWORDS],
        **overrides,
    )


# --- E28: German defaults on the warning text fixture -------------------------


def _fixture() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return cases


def test_e28_fixture_has_the_25_cases_of_the_spec() -> None:
    """The fixture holds 12 shut-down and 13 keep-running cases (06-testing.md)."""
    cases = _fixture()
    assert len(cases) == 25
    assert sum(case["expected"] == "shut_down" for case in cases) == 12
    assert {case["expected"] for case in cases} == {"shut_down", "keep_running"}
    assert all(isinstance(case["constructed"], bool) for case in cases)


@pytest.mark.parametrize("case", _fixture(), ids=lambda case: case["headline"])
def test_e28_bhv40_de_defaults_on_fixture(case: dict[str, Any]) -> None:
    """E28: the de default keyword lists decide every fixture case as expected."""
    details = make_details(
        headline=case["headline"], text=case["text"], actions=case["actions"]
    )
    expected = case["expected"] == "shut_down"
    assert warning_counts(_nina_de(), details) is expected


# --- E29, E30i: severity -------------------------------------------------------


@pytest.mark.parametrize(
    ("severity", "counts"),
    [("Moderate", False), ("Extreme", True), ("Severe", True), ("Bogus", True)],
)
def test_e29_bhv40_severity_filter(severity: str, counts: bool) -> None:
    """E29: minimum `severe`: Moderate no, Extreme yes, unknown counts as extreme."""
    source = _source(min_severity=Severity.SEVERE)
    details = make_details(headline="Großbrand", severity=severity)
    assert warning_counts(source, details) is counts


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Severe", Severity.SEVERE),
        ("3", Severity.SEVERE),
        ("3; orange; Severe", Severity.SEVERE),
        ("bogus", None),
        ("MINOR", Severity.MINOR),
        (" moderate ", Severity.MODERATE),
        ("Extreme", Severity.EXTREME),
        (1, Severity.MINOR),
        (2, Severity.MODERATE),
        (4.0, Severity.EXTREME),
        ("4", Severity.EXTREME),
        (0, None),
        ("0", None),
        (5, None),
        (2.5, None),
        (float("nan"), None),
        ("yellow", Severity.MODERATE),
        ("Orange", Severity.SEVERE),
        ("red", Severity.EXTREME),
        ("2; yellow; Moderate", Severity.MODERATE),
        ("4; red; Extreme", Severity.EXTREME),
        ("green; bogus", None),
        ("", None),
        (None, None),
        (True, None),
        (Severity.MINOR, Severity.MINOR),
    ],
)
def test_e30i_bhv45_severity_normalisation(raw: object, expected: Severity) -> None:
    """E30i: CAP words, DWD levels, Meteoalarm awareness levels; else None."""
    assert normalise_severity(raw) is expected


def test_severity_rank_and_min_severity_option() -> None:
    """Severity order and the `min_severity` option (`none` = no filter)."""
    ranks = [s.rank for s in Severity]
    assert ranks == [1, 2, 3, 4]
    assert parse_min_severity(None) is None
    assert parse_min_severity("none") is None
    assert parse_min_severity("") is None
    assert parse_min_severity("moderate") is Severity.MODERATE
    with pytest.raises(ValueError, match="bogus"):
        parse_min_severity("bogus")


def test_severity_filter_minimum_and_missing_severity() -> None:
    """The minimum itself counts; a warning without severity counts as extreme."""
    source = _source(min_severity=Severity.EXTREME)
    assert warning_counts(source, make_details(headline="x", severity="Extreme"))
    assert not warning_counts(source, make_details(headline="x", severity="Severe"))
    assert warning_counts(source, make_details(headline="x"))


# --- E30a, E30d, E30e: filters and HTML -----------------------------------------


def test_e30a_all_clear_is_excluded() -> None:
    """E30a: "Entwarnung: Feuerwehreinsatz - X" with window advice: no warning."""
    details = make_details(
        headline="Entwarnung: Feuerwehreinsatz - Musterstadt",
        actions="Schließen Sie vorsorglich Fenster und Türen.",
    )
    assert not warning_counts(_nina_de(), details)


def test_e30d_html_is_stripped_before_matching() -> None:
    """E30d: `<br/>` and `&quot;` are stripped/unescaped; text keywords still match."""
    details = make_details(
        headline="Hinweis",
        text="Bitte die L&uuml;ftung<br/>abschalten: &quot;sofort&quot;",
    )
    assert details.text == 'Bitte die Lüftung\nabschalten: "sofort"'
    assert warning_counts(_source(text_keywords=("Lüftung",)), details)
    split = make_details(headline="Hinweis", text="Lüf<b>tung</b> aus")
    assert split.text == "Lüftung aus"
    assert warning_counts(_source(text_keywords=("lüftung",)), split)


@pytest.mark.parametrize(
    ("raw", "text"),
    [
        (None, None),
        ("", None),
        ("<p></p>", None),
        ("a<br>b", "a\nb"),
        ("<p>one</p><p>two</p>", "one\ntwo"),
        ("<ul><li>one</li><li>two</li></ul>", "one\ntwo"),
        ("  many   blanks  ", "many blanks"),
        ("a &amp; b &lt;c&gt;", "a & b <c>"),
        ('<a href="x">link</a> text', "link text"),
        (42, "42"),
    ],
)
def test_html_to_text(raw: object, text: str | None) -> None:
    """Tags are removed, entities unescaped, blanks collapsed, empty is None."""
    assert html_to_text(raw) == text


def test_e30e_regex_keyword() -> None:
    """E30e: the keyword ``/rauch<word chars>entwicklung/`` is a regex."""
    source = _source(headline_keywords=("/rauch\\w*entwicklung/",))
    assert warning_counts(source, make_details(headline="Starke Rauchentwicklung"))
    assert warning_counts(source, make_details(headline="RAUCHGASENTWICKLUNG"))
    assert not warning_counts(source, make_details(headline="Rauch über der Stadt"))


def test_regex_lookahead_of_the_de_defaults() -> None:
    """`/brand(?!enburg)/` matches "Großbrand" but not "Brandenburg"."""
    assert keyword_matches("/brand(?!enburg)/", "Großbrand - X")
    assert not keyword_matches("/brand(?!enburg)/", "Sperrung - Brandenburg")


def test_keyword_matching_rules() -> None:
    """Substrings are case-insensitive; `/…/` is a regex; bad regex = substring."""
    assert keyword_matches("Lüftung", "LÜFTUNGSANLAGE")
    assert keyword_matches("gas leak", "Gas Leak at the plant")
    assert not keyword_matches("Gasaustritt", "Gasse")
    assert keyword_matches("/^groß/", "Großbrand")
    assert not keyword_matches("/^brand/", "Großbrand")
    assert keyword_matches("/(/", "a /(/ b")
    assert not keyword_matches("/(/", "a ( b")
    assert keyword_matches("/", "a/b")
    assert not is_regex("//")
    assert is_regex("/a/")
    assert invalid_regex("/(/")
    assert not invalid_regex("/a+/")
    assert not invalid_regex("plain (")


def test_filter_lists() -> None:
    """Headline keywords only in the headline, text keywords everywhere."""
    source = _source(headline_keywords=("Brand",), text_keywords=("Lüftung",))
    assert warning_counts(source, make_details(headline="Großbrand"))
    # an event word in the body does not count (sender, intro sentences)
    body = make_details(
        headline="Hinweis", text="Amt für Brand- und Katastrophenschutz"
    )
    assert not warning_counts(source, body)
    assert warning_counts(
        source, make_details(headline="Hinweis", actions="Lüftung aus")
    )
    assert warning_counts(source, make_details(headline="Lüftung aus"))
    # exclude is checked first, over headline + text + actions
    excluded = _source(headline_keywords=("Brand",), exclude_keywords=("Übung",))
    assert not warning_counts(
        excluded, make_details(headline="Brand", text="Dies ist eine Übung")
    )


def test_empty_include_lists_count_every_warning_not_excluded() -> None:
    """Both include lists empty: every warning that is not excluded counts."""
    source = _source(exclude_keywords=("test",))
    assert warning_counts(source, make_details(headline="Anything"))
    assert warning_counts(source, WarningDetails())
    assert not warning_counts(source, make_details(text="This is a TEST"))


def test_make_details() -> None:
    """Raw provider values become plain text; empty keys are None."""
    details = make_details(
        key=" 123 ",
        headline="<b>Großbrand</b>",
        sender="Leitstelle &amp; Feuerwehr",
        severity="Severe",
    )
    assert details == WarningDetails(
        key="123",
        headline="Großbrand",
        sender="Leitstelle & Feuerwehr",
        severity=Severity.SEVERE,
    )
    assert make_details(key="").key is None
    assert make_details(key=7).key == "7"


# --- Triggers: E30f, E30g --------------------------------------------------------


@pytest.mark.parametrize(
    ("state", "active"),
    [
        ("2", False),
        ("3", True),
        ("4", True),
        ("3.5", True),
        ("abc", False),
        ("inf", False),
        ("unavailable", None),
        ("unknown", None),
        (None, None),
    ],
)
def test_e30f_numeric_trigger(state: str | None, active: bool | None) -> None:
    """E30f: trigger `numeric` threshold 3: DWD level 2 inactive, 3 active."""
    source = _source(trigger=opt.TRIGGER_NUMERIC, threshold=3)
    assert entity_active(source, InputValue(state=state)) is active


def test_numeric_trigger_without_threshold_is_never_active() -> None:
    """A numeric source without threshold (not possible in the flow) stays inactive."""
    source = _source(trigger=opt.TRIGGER_NUMERIC)
    assert entity_active(source, InputValue(state="4")) is False


@pytest.mark.parametrize(
    ("state", "active"),
    [
        ("rouge", True),
        ("Orange", True),
        (" ROUGE ", True),
        ("Jaune", False),
        ("vert", False),
        ("unavailable", None),
    ],
)
def test_e30g_state_list_trigger(state: str, active: bool | None) -> None:
    """E30g: trigger `state_list` [Orange, Rouge]: `rouge` active (case-insensitive)."""
    source = _source(trigger=opt.TRIGGER_STATE_LIST, states=("Orange", "Rouge"))
    assert entity_active(source, InputValue(state=state)) is active


@pytest.mark.parametrize(
    ("state", "active"),
    [
        ("on", True),
        ("ON", True),
        ("off", False),
        ("unavailable", None),
        ("unknown", None),
        ("", None),
    ],
)
def test_on_off_trigger(state: str, active: bool | None) -> None:
    """Trigger `on_off`: on is active; unavailable/unknown never."""
    assert entity_active(_source(), InputValue(state=state)) is active
    assert entity_active(_source(), None) is None


# --- Keys --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("headline", "normalised"),
    [
        ("Großbrand - X", "großbrand - x"),
        ("1. AKTUALISIERUNG: Großbrand - X", "großbrand - x"),
        ("12. Aktualisierung:Großbrand - X", "großbrand - x"),
        ("Entwarnung: Großbrand - X", "großbrand - x"),
        ("Entwarnung: 2. Aktualisierung: Großbrand - X", "großbrand - x"),
        ("Update: Fire at the plant", "fire at the plant"),
        ("ALL CLEAR: Fire at the plant", "fire at the plant"),
        ("  Brand  ", "brand"),
    ],
)
def test_incident_headline(headline: str, normalised: str) -> None:
    """Lower-cased headline without update/all-clear prefixes (03-modes.md)."""
    assert incident_headline(headline) == normalised


def test_warning_and_incident_keys() -> None:
    """Provider key -> `subentry:key`; else `subentry:entity:activation`."""
    assert warning_key("s1", "nina.123", "binary_sensor.w_2", T0) == "s1:nina.123"
    fallback = warning_key("s1", None, "binary_sensor.w_2", T0)
    assert fallback == f"s1:binary_sensor.w_2:{T0.isoformat()}"
    assert incident_key("s1", "1. Aktualisierung: Brand", "s1:k") == "s1:brand"
    assert incident_key("s1", None, "s1:k") == "s1:k"
    assert incident_key("s1", "Entwarnung:", "s1:k") == "s1:k"


def test_by_priority() -> None:
    """Most severe first (None counts as extreme), then newest first."""

    def info(key: str, severity: Severity | None, minutes: int) -> WarningInfo:
        return WarningInfo(
            source_id="s",
            source_name="S",
            entity_id="binary_sensor.x",
            incident_key=key,
            headline=key,
            text=None,
            recommended_actions=None,
            severity=severity,
            sender=None,
            since=T0 + timedelta(minutes=minutes),
        )

    minor = info("a", Severity.MINOR, 5)
    old = info("b", Severity.SEVERE, 0)
    new = info("c", Severity.SEVERE, 1)
    unknown = info("d", None, 0)
    assert by_priority([minor, old, new, unknown]) == (unknown, new, old, minor)


# --- Suggested filters ---------------------------------------------------------


def test_suggested_filters_per_provider_and_language() -> None:
    """NINA: de lists for `de`, English otherwise; others empty; `none` hidden."""
    assert suggested_filters(PROVIDER_NINA, "de") is NINA_DE
    assert suggested_filters(PROVIDER_NINA, "de-CH") is NINA_DE
    assert suggested_filters(PROVIDER_NINA, "en") is NINA_OTHER
    assert suggested_filters(PROVIDER_NINA, "fr") is NINA_OTHER
    for provider in (PROVIDER_DWD, PROVIDER_ATTRIBUTES, "environment_canada"):
        filters = suggested_filters(provider, "de")
        assert filters is not None
        assert all(not keywords for keywords in filters.values())
    assert suggested_filters(PROVIDER_NONE, "de") is None


def test_suggested_keywords_compile_and_do_not_conflict() -> None:
    """Every suggested regex compiles; no keyword is both included and excluded."""
    for filters in (NINA_DE, NINA_OTHER):
        keywords = [k for values in filters.values() for k in values]
        assert not [k for k in keywords if invalid_regex(k)]
        include = {
            k.casefold()
            for k in (
                *filters[opt.CONF_HEADLINE_KEYWORDS],
                *filters[opt.CONF_TEXT_KEYWORDS],
            )
        }
        assert not include & {k.casefold() for k in filters[opt.CONF_EXCLUDE_KEYWORDS]}


def test_warning_source_from_subentry_data() -> None:
    """A subentry dict becomes a `WarningSource` with clean keyword lists."""
    source = WarningSource.from_mapping(
        "sub1",
        {
            opt.CONF_SOURCE_ENTITY: "sensor.dwd_level",
            opt.CONF_TRIGGER: opt.TRIGGER_NUMERIC,
            opt.CONF_THRESHOLD: "3",
            opt.CONF_HEADLINE_KEYWORDS: ["Brand", " ", "  Rauch "],
            opt.CONF_TEXT_KEYWORDS: "Lüftung",
            opt.CONF_MIN_SEVERITY: "severe",
            opt.CONF_DETAILS: opt.DETAILS_ATTRIBUTES,
        },
        title="Weather",
    )
    assert source.name == "Weather"
    assert source.entities == ("sensor.dwd_level",)
    assert source.threshold == 3
    assert source.headline_keywords == ("Brand", "Rauch")
    assert source.text_keywords == ("Lüftung",)
    assert source.exclude_keywords == ()
    assert source.min_severity is Severity.SEVERE
    assert source.details == opt.DETAILS_ATTRIBUTES

    device = WarningSource.from_mapping(
        "sub2",
        {opt.CONF_SOURCE_DEVICE: "abc", opt.CONF_NAME: "NINA region"},
        entities=["binary_sensor.warning_1", "binary_sensor.warning_2"],
    )
    assert device.name == "NINA region"
    assert device.trigger == opt.TRIGGER_ON_OFF
    assert device.entities == ("binary_sensor.warning_1", "binary_sensor.warning_2")
    assert device.min_severity is None
    unnamed = WarningSource.from_mapping("sub3", {})
    assert (unnamed.name, unnamed.entities) == ("", ())
    named_by_entity = WarningSource.from_mapping(
        "sub4", {opt.CONF_SOURCE_ENTITY: "binary_sensor.smoke"}
    )
    assert named_by_entity.name == "binary_sensor.smoke"


# --- Notification text (BHV-41, 04-interface.md#notifications) -----------------


def _warning(**overrides: Any) -> WarningInfo:
    values: dict[str, Any] = {
        "source_id": "s",
        "source_name": "Outdoor smoke",
        "entity_id": "binary_sensor.example_smoke",
        "incident_key": "s:1",
        "headline": "Fire nearby",
        "text": "Large fire in the industrial area.",
        "recommended_actions": "Close windows and doors.",
        "severity": Severity.SEVERE,
        "sender": "Fire brigade",
        "since": T0,
    }
    values.update(overrides)
    return WarningInfo(**values)


def test_bhv41_notification_text_prefers_recommended_actions() -> None:
    """Recommended actions first, then the description; the sender in brackets."""
    assert notification_text(_warning()) == "Close windows and doors. (Fire brigade)"
    assert (
        notification_text(_warning(recommended_actions=None, sender=None))
        == "Large fire in the industrial area."
    )


def test_bhv41_notification_text_without_text_is_none() -> None:
    """A warning without actions and description has no text (the glue words it)."""
    assert notification_text(_warning(recommended_actions=None, text=None)) is None
    assert (
        notification_text(
            _warning(recommended_actions=None, text=None, sender=None, details=False)
        )
        is None
    )


def test_bhv41_notification_text_is_shortened_to_300_chars() -> None:
    """The text is cut to 300 characters (with an ellipsis) before the sender."""
    long_text = "word " * 100
    text = notification_text(_warning(recommended_actions=long_text, sender=None))
    assert text is not None
    assert len(text) == 300
    assert text.endswith("…")
    assert not text[:-1].endswith(" ")
    with_sender = notification_text(_warning(recommended_actions=long_text))
    assert with_sender is not None
    assert with_sender.endswith("… (Fire brigade)")
    exact = "x" * 300
    assert notification_text(_warning(recommended_actions=exact, sender=None)) == exact
