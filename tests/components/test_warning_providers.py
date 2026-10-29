"""Details providers with recorded states (C11c) and the NINA service (C11a).

The states follow the attribute layout of the HA core integrations, checked on
2026.4.0 and 2026.10.1 (03-modes.md#details-providers); texts and places are
neutral examples.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any
from unittest.mock import patch

from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    State,
    SupportsResponse,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
import pytest

from custom_components.better_home_ventilation.engine.models import (
    Severity,
    WarningDetails,
    WarningSource,
)
from custom_components.better_home_ventilation.warning_providers import (
    DetailsCollector,
    DetailsError,
    detect_provider,
    runtime_provider,
)
from custom_components.better_home_ventilation.warning_providers.attributes import (
    AttributesProvider,
)
from custom_components.better_home_ventilation.warning_providers.dwd import (
    DwdProvider,
)
from custom_components.better_home_ventilation.warning_providers.environment_canada import (
    EnvironmentCanadaProvider,
)
from custom_components.better_home_ventilation.warning_providers.nina import (
    NinaProvider,
)

NINA_1 = "binary_sensor.example_city_warning_1"
NINA_2 = "binary_sensor.example_city_warning_2"

#: A DWD "current warning level" sensor with two warnings (level 3 and 0).
DWD_STATE = State(
    "sensor.example_district_current_warning_level",
    "3",
    {
        "region_name": "Example District",
        "region_id": 100000000,
        "last_update": "2026-10-28T06:00:00+00:00",
        "warning_count": 2,
        "warning_1_name": "Sturmböen",
        "warning_1_type": 52,
        "warning_1_level": 3,
        "warning_1_headline": "Amtliche WARNUNG vor STURMBÖEN",
        "warning_1_description": "Es treten Sturmböen mit Geschwindigkeiten um 70 km/h auf.",
        "warning_1_instruction": "Achten Sie auf herabfallende Gegenstände.",
        "warning_1_start": "2026-10-28T10:00:00+00:00",
        "warning_1_end": "2026-10-28T18:00:00+00:00",
        "warning_1_parameters": {"gusts": "70 km/h"},
        "warning_1_color": "#ff9900",
        "warning_2_name": "Vorabinformation",
        "warning_2_level": 0,
        "warning_2_headline": "Vorabinformation",
        "warning_2_start": "2026-10-29T00:00:00+00:00",
    },
)

#: An Environment Canada alert sensor (state = number of alerts).
EC_STATE = State(
    "sensor.example_town_warnings",
    "2",
    {
        "location": "Example Town",
        "station": "Example Station",
        "alert_1": "Air quality warning in effect",
        "alert_time_1": "06:00 AM EDT Tuesday 28 October 2026",
        "alert_2": "Smoke advisory",
        "alert_time_2": None,
        "alert_3": "",
    },
)

#: A Meteoalarm binary sensor: the alert dict of the API as attributes.
METEOALARM_STATE = State(
    "binary_sensor.example_region_meteoalarm",
    "on",
    {
        "attribution": "Information provided by MeteoAlarm",
        "language": "en-GB",
        "category": "Met",
        "event": "Orange wind warning",
        "severity": "Severe",
        "headline": "Orange wind warning for Example Region",
        "description": "Gusts up to 90 km/h &amp; more <br/>near the coast.",
        "instruction": "Stay indoors.",
        "awareness_level": "3; orange; Severe",
        "awareness_type": "1; Wind",
        "identifier": "2.49.0.0.000.0.EX.000001",
        "expires": "2026-10-29T00:00:00+00:00",
    },
)

#: A template binary sensor (no attributes besides the friendly name).
TEMPLATE_STATE = State(
    "binary_sensor.example_outdoor_smoke", "on", {"friendly_name": "Outdoor smoke"}
)

#: `nina.get_details` response data of one slot (HA 2026.10 `get_details`).
NINA_DETAILS: dict[str, Any] = {
    "headline": "Großbrand - Example City",
    "description": "Es kommt zu einer <b>starken Rauchentwicklung</b>.",
    "sender": "Example Fire Brigade",
    "severity": "Severe",
    "recommended_actions": "Schalten Sie Lüftungs- und Klimaanlagen ab.",
    "affected_areas": "Example City",
    "web": "",
    "id": "example.warning.1",
    "sent": "2026-10-28T10:00:00+01:00",
    "start": "",
    "expires": "",
}


async def test_c11c_dwd_indexed_attributes(hass: HomeAssistant) -> None:
    """DWD: one warning per `warning_<i>_*` with level > 0, key headline:start."""
    details = await DwdProvider().async_get_details(
        hass, DWD_STATE.entity_id, DWD_STATE
    )
    assert details == [
        WarningDetails(
            key="Amtliche WARNUNG vor STURMBÖEN:2026-10-28T10:00:00+00:00",
            headline="Amtliche WARNUNG vor STURMBÖEN",
            text="Es treten Sturmböen mit Geschwindigkeiten um 70 km/h auf.",
            actions="Achten Sie auf herabfallende Gegenstände.",
            severity=Severity.SEVERE,
            sender="DWD",
        )
    ]


@pytest.mark.parametrize(
    "attributes",
    [{}, {"warning_count": "x"}, {"warning_count": 1}],
)
async def test_dwd_without_warnings(
    hass: HomeAssistant, attributes: dict[str, Any]
) -> None:
    """A missing or broken count and missing fields give no or empty warnings."""
    state = State("sensor.example_dwd", "0", attributes)
    details = await DwdProvider().async_get_details(hass, state.entity_id, state)
    if attributes.get("warning_count") == 1:
        assert details == [WarningDetails(sender="DWD")]
    else:
        assert details == []


async def test_c11c_environment_canada_alert_titles(hass: HomeAssistant) -> None:
    """Environment Canada: one headline per `alert_<i>`, empty titles skipped."""
    details = await EnvironmentCanadaProvider().async_get_details(
        hass, EC_STATE.entity_id, EC_STATE
    )
    assert [(d.key, d.headline, d.text) for d in details] == [
        (
            "Air quality warning in effect:06:00 AM EDT Tuesday 28 October 2026",
            "Air quality warning in effect",
            None,
        ),
        ("Smoke advisory", "Smoke advisory", None),
    ]


async def test_c11c_meteoalarm_like_attributes(hass: HomeAssistant) -> None:
    """Generic attributes: first present name wins, HTML stripped (Q8 layout)."""
    details = await AttributesProvider().async_get_details(
        hass, METEOALARM_STATE.entity_id, METEOALARM_STATE
    )
    assert details == [
        WarningDetails(
            key="2.49.0.0.000.0.EX.000001",
            headline="Orange wind warning for Example Region",
            text="Gusts up to 90 km/h & more\nnear the coast.",
            actions="Stay indoors.",
            severity=Severity.SEVERE,
            sender=None,
        )
    ]


async def test_attributes_awareness_level_only(hass: HomeAssistant) -> None:
    """Without `severity` the awareness level string is normalised."""
    state = State(
        "binary_sensor.example_alert",
        "on",
        {"event": "Thunderstorm", "awareness_level": "4; red; Extreme"},
    )
    [details] = await AttributesProvider().async_get_details(
        hass, state.entity_id, state
    )
    assert details.headline == "Thunderstorm"
    assert details.severity is Severity.EXTREME


@pytest.mark.parametrize(
    "attributes",
    [{"friendly_name": "Outdoor smoke"}, {"headline": "<br/>", "description": ""}],
)
async def test_c11c_template_binary_sensor_has_no_details(
    hass: HomeAssistant, attributes: dict[str, Any]
) -> None:
    """Neither headline nor text: no details (counts as one warning)."""
    state = State(TEMPLATE_STATE.entity_id, "on", attributes)
    assert (
        await AttributesProvider().async_get_details(hass, state.entity_id, state) == []
    )


# --- Provider selection ---------------------------------------------------------------


def _register(hass: HomeAssistant, platform: str, entity_id: str) -> None:
    domain, object_id = entity_id.split(".")
    er.async_get(hass).async_get_or_create(
        domain, platform, object_id, suggested_object_id=object_id
    )


async def test_runtime_provider_by_platform_and_option(hass: HomeAssistant) -> None:
    """`auto` follows the registry platform; `attributes`/`none` force it."""
    _register(hass, "nina", NINA_1)
    _register(hass, "dwd_weather_warnings", DWD_STATE.entity_id)
    _register(hass, "meteoalarm", METEOALARM_STATE.entity_id)
    assert runtime_provider(hass, NINA_1, "auto") == "nina"
    assert runtime_provider(hass, DWD_STATE.entity_id, "auto") == "dwd_weather_warnings"
    assert runtime_provider(hass, METEOALARM_STATE.entity_id, "auto") == "attributes"
    assert (
        runtime_provider(hass, "binary_sensor.not_registered", "auto") == "attributes"
    )
    assert runtime_provider(hass, NINA_1, "attributes") == "attributes"
    assert runtime_provider(hass, NINA_1, "none") == "none"


async def test_detect_provider_for_the_flow(hass: HomeAssistant) -> None:
    """Helpers and indoor detectors are `none`, other entities `attributes`."""
    _register(hass, "template", TEMPLATE_STATE.entity_id)
    _register(hass, "environment_canada", EC_STATE.entity_id)
    hass.states.async_set(
        "binary_sensor.example_co", "off", {"device_class": "carbon_monoxide"}
    )
    hass.states.async_set("binary_sensor.example_other", "off")
    assert detect_provider(hass, [TEMPLATE_STATE.entity_id]) == "none"
    assert detect_provider(hass, ["input_boolean.example_danger"]) == "none"
    assert detect_provider(hass, ["binary_sensor.example_co"]) == "none"
    assert detect_provider(hass, ["binary_sensor.example_other"]) == "attributes"
    assert (
        detect_provider(hass, [TEMPLATE_STATE.entity_id, EC_STATE.entity_id])
        == "environment_canada"
    )
    assert detect_provider(hass, []) == "none"


# --- NINA (C11a) ------------------------------------------------------------------------


def _nina_service(
    hass: HomeAssistant,
    responses: dict[str, dict[str, Any]],
    calls: list[ServiceCall],
    *,
    fail: bool = False,
    hang: bool = False,
) -> None:
    """Register a stand-in for the entity service `nina.get_details`."""

    async def handler(call: ServiceCall) -> ServiceResponse:
        calls.append(call)
        if fail:
            raise HomeAssistantError("NINA API not reachable")
        if hang:
            await asyncio.sleep(3600)
        entity_id = call.data["entity_id"]
        entity_id = entity_id if isinstance(entity_id, str) else entity_id[0]
        return {entity_id: responses.get(entity_id)}

    hass.services.async_register(
        "nina", "get_details", handler, supports_response=SupportsResponse.ONLY
    )


def _nina_state(entity_id: str, warning_id: str, **attributes: Any) -> State:
    return State(entity_id, "on", {"id": warning_id, **attributes})


async def test_c11a_nina_service_called_once_per_id(hass: HomeAssistant) -> None:
    """`nina.get_details` is called per new id; a slot change keeps the cache."""
    calls: list[ServiceCall] = []
    _nina_service(hass, {NINA_1: NINA_DETAILS, NINA_2: NINA_DETAILS}, calls)
    provider = NinaProvider()

    [details] = await provider.async_get_details(
        hass, NINA_1, _nina_state(NINA_1, "example.warning.1")
    )
    assert details == WarningDetails(
        key="example.warning.1",
        headline="Großbrand - Example City",
        text="Es kommt zu einer starken Rauchentwicklung.",
        actions="Schalten Sie Lüftungs- und Klimaanlagen ab.",
        severity=Severity.SEVERE,
        sender="Example Fire Brigade",
    )
    assert len(calls) == 1
    assert calls[0].data["entity_id"] == NINA_1

    # The same warning moves to slot 2: no new call.
    again = await provider.async_get_details(
        hass, NINA_2, _nina_state(NINA_2, "example.warning.1")
    )
    assert again == [details]
    assert len(calls) == 1

    # A new id in slot 1: a new call.
    await provider.async_get_details(hass, NINA_1, _nina_state(NINA_1, "example.2"))
    assert len(calls) == 2


async def test_nina_empty_response_and_no_id(hass: HomeAssistant) -> None:
    """An empty response gives no details; without id nothing is cached."""
    calls: list[ServiceCall] = []
    _nina_service(hass, {}, calls)
    provider = NinaProvider()
    state = State(NINA_1, "on", {})
    assert await provider.async_get_details(hass, NINA_1, state) == []
    assert await provider.async_get_details(hass, NINA_1, state) == []
    assert len(calls) == 2


async def test_nina_cache_is_bounded(hass: HomeAssistant) -> None:
    """Only the most recent warnings are kept."""
    calls: list[ServiceCall] = []
    _nina_service(hass, {NINA_1: NINA_DETAILS}, calls)
    provider = NinaProvider()
    with patch(
        "custom_components.better_home_ventilation.warning_providers.nina.CACHE_SIZE",
        2,
    ):
        for warning_id in ("a", "b", "c", "a"):
            await provider.async_get_details(
                hass, NINA_1, _nina_state(NINA_1, warning_id)
            )
    assert len(calls) == 4  # "a" was evicted by "c"


async def test_c11a_nina_failure_and_retry(hass: HomeAssistant) -> None:
    """A failed call raises `DetailsError` and is retried only after 5 minutes."""
    calls: list[ServiceCall] = []
    _nina_service(hass, {}, calls, fail=True)
    provider = NinaProvider()
    state = _nina_state(NINA_1, "example.warning.1")
    module = "custom_components.better_home_ventilation.warning_providers.nina.time"
    with patch(f"{module}.monotonic", return_value=1000.0):
        with pytest.raises(DetailsError):
            await provider.async_get_details(hass, NINA_1, state)
        with pytest.raises(DetailsError):
            await provider.async_get_details(hass, NINA_1, state)
    assert len(calls) == 1
    _nina_service(hass, {NINA_1: NINA_DETAILS}, calls)
    with patch(f"{module}.monotonic", return_value=1301.0):
        assert len(await provider.async_get_details(hass, NINA_1, state)) == 1
    assert len(calls) == 2


async def test_c11a_nina_timeout(hass: HomeAssistant) -> None:
    """A call that does not answer within 10 s fails (fail-safe)."""
    calls: list[ServiceCall] = []
    _nina_service(hass, {}, calls, hang=True)
    module = "custom_components.better_home_ventilation.warning_providers.nina"
    with (
        patch(
            f"{module}.DETAILS_TIMEOUT",
            new=type("T", (), {"total_seconds": lambda _: 0.01})(),
        ),
        pytest.raises(DetailsError),
    ):
        await NinaProvider().async_get_details(
            hass, NINA_1, _nina_state(NINA_1, "example.warning.1")
        )


async def test_nina_attributes_without_the_service(hass: HomeAssistant) -> None:
    """Before `nina.get_details` existed (HA 2026.4) the attributes are read."""
    state = _nina_state(NINA_1, "example.warning.1", **NINA_DETAILS)
    [details] = await NinaProvider().async_get_details(hass, NINA_1, state)
    assert details.headline == "Großbrand - Example City"
    assert details.key == "example.warning.1"
    assert (
        await NinaProvider().async_get_details(hass, NINA_1, _nina_state(NINA_1, "x"))
        == []
    )


# --- Collector ------------------------------------------------------------------------------


class _Broken:
    name = "broken"

    async def async_get_details(
        self, hass: HomeAssistant, entity_id: str, state: State
    ) -> list[WarningDetails]:
        raise KeyError("bug")


class _Slow:
    name = "slow"

    async def async_get_details(
        self, hass: HomeAssistant, entity_id: str, state: State
    ) -> list[WarningDetails]:
        await asyncio.sleep(3600)
        return []


@pytest.mark.parametrize("factory", [_Broken, _Slow])
async def test_collector_marks_broken_providers_failed(
    hass: HomeAssistant, factory: Callable[[], Any]
) -> None:
    """Any exception or a timeout of a provider counts as failed (fail-safe)."""
    hass.states.async_set(METEOALARM_STATE.entity_id, "on", METEOALARM_STATE.attributes)
    collector = DetailsCollector(hass)
    collector._providers["attributes"] = factory()
    source = WarningSource(
        subentry_id="s1", name="Meteoalarm", entities=(METEOALARM_STATE.entity_id,)
    )
    module = "custom_components.better_home_ventilation.warning_providers"
    with patch(
        f"{module}._GUARD", new=type("T", (), {"total_seconds": lambda _: 0.01})()
    ):
        collected = await collector.async_collect([source])
    assert collected.failed == {METEOALARM_STATE.entity_id}
    assert not collected.details


async def test_collector_only_asks_for_active_entities(hass: HomeAssistant) -> None:
    """Inactive entities, sources with `none` and empty details are skipped."""
    hass.states.async_set(METEOALARM_STATE.entity_id, "on", METEOALARM_STATE.attributes)
    hass.states.async_set("binary_sensor.example_off", "off", {"headline": "x"})
    hass.states.async_set(TEMPLATE_STATE.entity_id, "on")
    collector = DetailsCollector(hass)
    sources = [
        WarningSource(
            subentry_id="s1",
            name="All",
            entities=(
                METEOALARM_STATE.entity_id,
                "binary_sensor.example_off",
                TEMPLATE_STATE.entity_id,
                "binary_sensor.example_missing",
            ),
        ),
        WarningSource(
            subentry_id="s2",
            name="Forced none",
            entities=(METEOALARM_STATE.entity_id,),
            details="none",
        ),
    ]
    collected = await collector.async_collect(sources)
    assert set(collected.details) == {METEOALARM_STATE.entity_id}
    assert not collected.failed
    assert (await collector.async_collect([sources[1]])).details == {}
