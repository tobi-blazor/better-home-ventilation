"""Notifications with actions (C10, C11; BHV-41, BHV-43, BHV-24).

docs/spec/04-interface.md#notifications, 03-modes.md#flow.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
from typing import Any

from freezegun.api import FrozenDateTimeFactory
from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.translation import async_get_translations
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)

from custom_components.better_home_ventilation.const import DOMAIN
from custom_components.better_home_ventilation.engine.models import (
    Mode,
    NotifyKind,
    ProblemKey,
)
from custom_components.better_home_ventilation.notifications import (
    Action,
    action_command,
    format_notify,
    format_time,
    service_name,
)

from .common import FAN, NUMBER_FLOW, MockUnit, advance
from .test_controller import controller_of, problem_keys, setup_entry
from .test_safety import SMOKE, mode_of, set_state, source, turn

PHONE = "mobile_app_example_phone"
TABLET = "mobile_app_example_tablet"


@pytest.fixture(autouse=True)
def daytime(freezer: FrozenDateTimeFactory) -> None:
    """Run every test at 13:00 local time, outside the night window."""
    freezer.move_to("2026-10-14T20:00:00+00:00")


@pytest.fixture
def phone(hass: HomeAssistant) -> list[ServiceCall]:
    """Register `notify.mobile_app_example_phone` and record its calls."""
    return async_mock_service(hass, "notify", PHONE)


def entry8(entry: MockConfigEntry) -> str:
    """Return the first 8 characters of the entry id."""
    return entry.entry_id[:8]


def notifications(hass: HomeAssistant) -> dict[str, Any]:
    """Return the persistent notifications by id."""
    return dict(persistent_notification._async_get_or_create_notifications(hass))


async def tap(hass: HomeAssistant, action: str) -> None:
    """Fire the companion app event of a tapped notification action."""
    hass.bus.async_fire("mobile_app_notification_action", {"action": action})
    await hass.async_block_till_done()


async def warning_entry(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, **overrides: Any
) -> MockConfigEntry:
    """Set up an entry with a smoke source, past the startup grace."""
    hass.states.async_set(SMOKE, "off")
    entry = await setup_entry(
        hass, subentries=[source(entity_id=SMOKE, name="Outdoor smoke")], **overrides
    )
    await advance(hass, freezer, 61)
    return entry


# --- C10: safety notification, actions -------------------------------------------------


@pytest.mark.usefixtures("entities")
async def test_c10_safety_notification_and_keep_ventilating(
    hass: HomeAssistant,
    unit: MockUnit,
    freezer: FrozenDateTimeFactory,
    phone: list[ServiceCall],
) -> None:
    """Safety off sends actions and tag; KEEP_VENTILATING overrides and clears."""
    entry = await warning_entry(hass, freezer, notify_services=[PHONE])
    short = entry8(entry)
    assert phone == []

    await set_state(hass, SMOKE, "on")

    assert mode_of(hass) == "safety_off"
    [call] = phone
    assert call.data["title"] == "Ventilation off: Outdoor smoke"
    assert call.data["message"] == (
        "Outdoor smoke reports a warning. The ventilation was switched off."
    )
    assert call.data["data"] == {
        "tag": f"bhv_{short}_safety",
        "actions": [
            {"action": f"BHV_{short}_KEEP_VENTILATING", "title": "Keep ventilating"},
            {"action": f"BHV_{short}_OK", "title": "OK"},
        ],
        "push": {"interruption-level": "time-sensitive"},
        "priority": "high",
        "ttl": 0,
    }

    await tap(hass, f"BHV_{short}_KEEP_VENTILATING")

    assert mode_of(hass) == "automatic"
    assert controller_of(entry).state.safety.overridden
    assert phone[-1].data == {
        "message": "clear_notification",
        "data": {"tag": f"bhv_{short}_safety"},
    }


@pytest.mark.usefixtures("entities")
async def test_c10_restart_question_and_restart(
    hass: HomeAssistant,
    unit: MockUnit,
    freezer: FrozenDateTimeFactory,
    phone: list[ServiceCall],
) -> None:
    """The warning ends: restart question; RESTART resumes (BHV-43)."""
    entry = await warning_entry(hass, freezer, notify_services=[PHONE])
    short = entry8(entry)
    await set_state(hass, SMOKE, "on")

    await set_state(hass, SMOKE, "off")

    call = phone[-1]
    pending = controller_of(entry).state.safety.restart_pending
    assert pending is not None
    assert call.data["title"] == "Warning lifted"
    time = dt_util.as_local(pending.deadline).strftime("%H:%M")
    assert call.data["message"] == (
        f"Restart ventilation? Restarts automatically at {time}."
    )
    assert call.data["data"]["tag"] == f"bhv_{short}_safety"
    assert [a["action"] for a in call.data["data"]["actions"]] == [
        f"BHV_{short}_RESTART",
        f"BHV_{short}_KEEP_OFF",
    ]
    assert call.data["data"]["priority"] == "high"

    await tap(hass, f"BHV_{short}_RESTART")

    assert mode_of(hass) == "automatic"
    assert controller_of(entry).state.safety.restart_pending is None
    assert phone[-1].data["message"] == "clear_notification"


@pytest.mark.usefixtures("entities")
async def test_c10_keep_off_and_reminder_with_resume(
    hass: HomeAssistant,
    unit: MockUnit,
    freezer: FrozenDateTimeFactory,
    phone: list[ServiceCall],
) -> None:
    """KEEP_OFF pauses; the reminder after 8 h offers RESUME (BHV-24)."""
    entry = await warning_entry(hass, freezer, notify_services=[PHONE])
    short = entry8(entry)
    await set_state(hass, SMOKE, "on")
    off_since = dt_util.now()
    await set_state(hass, SMOKE, "off")

    await tap(hass, f"BHV_{short}_KEEP_OFF")

    assert mode_of(hass) == "off"
    sent = len(phone)
    await advance(hass, freezer, 8 * 3600)

    [reminder] = phone[sent:]
    assert reminder.data["title"] == (
        f"Ventilation off since {format_time(off_since, dt_util.now())}"
    )
    assert reminder.data["message"] == "Moisture protection is inactive."
    assert reminder.data["data"] == {
        "tag": f"bhv_{short}_reminder",
        "actions": [{"action": f"BHV_{short}_RESUME", "title": "Resume"}],
    }

    await tap(hass, f"BHV_{short}_RESUME")

    assert mode_of(hass) not in {"off", "safety_off"}
    assert phone[-1].data == {
        "message": "clear_notification",
        "data": {"tag": f"bhv_{short}_reminder"},
    }


@pytest.mark.usefixtures("entities")
async def test_restarted_after_the_timeout(
    hass: HomeAssistant,
    unit: MockUnit,
    freezer: FrozenDateTimeFactory,
    phone: list[ServiceCall],
) -> None:
    """Without an answer the unit restarts and says so (no actions, not urgent)."""
    entry = await warning_entry(hass, freezer, notify_services=[PHONE])
    await set_state(hass, SMOKE, "on")
    await set_state(hass, SMOKE, "off")

    await advance(hass, freezer, 30 * 60 + 1)

    restarted = next(
        call for call in phone if call.data["title"] == "Ventilation restarted"
    )
    assert restarted.data["message"] == (
        "The warning has ended; the ventilation runs again."
    )
    assert restarted.data["data"] == {"tag": f"bhv_{entry8(entry)}_safety"}


@pytest.mark.usefixtures("entities")
async def test_other_and_unknown_actions_are_ignored(
    hass: HomeAssistant,
    unit: MockUnit,
    freezer: FrozenDateTimeFactory,
    phone: list[ServiceCall],
) -> None:
    """OK does nothing; other entries, unknown and late actions are ignored."""
    entry = await warning_entry(hass, freezer, notify_services=[PHONE])
    short = entry8(entry)
    controller = controller_of(entry)

    # Without a warning, a late KEEP_VENTILATING is rejected and ignored.
    await tap(hass, f"BHV_{short}_KEEP_VENTILATING")
    await tap(hass, f"BHV_{short}_KEEP_OFF")
    assert mode_of(hass) == "automatic"
    assert controller.state.manual is None

    await set_state(hass, SMOKE, "on")
    for action in (
        f"BHV_{short}_OK",
        "BHV_00000000_KEEP_VENTILATING",
        f"BHV_{short}_UNKNOWN",
        "SOMETHING_ELSE",
    ):
        await tap(hass, action)
    hass.bus.async_fire("mobile_app_notification_action", {})
    await hass.async_block_till_done()

    assert mode_of(hass) == "safety_off"
    assert not controller.state.safety.overridden


# --- C11: persistent notification ----------------------------------------------------


@pytest.mark.usefixtures("entities")
async def test_c11_without_services_a_persistent_notification(
    hass: HomeAssistant, unit: MockUnit, freezer: FrozenDateTimeFactory
) -> None:
    """No notify services: persistent notification (no actions), dismissed later."""
    entry = await warning_entry(hass, freezer)
    tag = f"bhv_{entry8(entry)}_safety"

    await set_state(hass, SMOKE, "on")

    notification = notifications(hass)[tag]
    assert notification["title"] == "Ventilation off: Outdoor smoke"
    assert notification["message"] == (
        "Outdoor smoke reports a warning. The ventilation was switched off."
    )

    await hass.services.async_call(DOMAIN, "override_safety", {}, blocking=True)
    await hass.async_block_till_done()

    assert tag not in notifications(hass)


@pytest.mark.usefixtures("entities")
async def test_disabled_kind_sends_nothing(
    hass: HomeAssistant,
    unit: MockUnit,
    freezer: FrozenDateTimeFactory,
    phone: list[ServiceCall],
) -> None:
    """`notify_safety` off: no safety notification and no clear."""
    await warning_entry(hass, freezer, notify_services=[PHONE], notify_safety=False)

    await set_state(hass, SMOKE, "on")
    await hass.services.async_call(DOMAIN, "override_safety", {}, blocking=True)
    await hass.async_block_till_done()

    assert phone == []
    assert notifications(hass) == {}


# --- Problems ----------------------------------------------------------------------------


@pytest.mark.usefixtures("entities")
async def test_problem_notification_after_problem_delay(
    hass: HomeAssistant,
    unit: MockUnit,
    freezer: FrozenDateTimeFactory,
    phone: list[ServiceCall],
) -> None:
    """A problem is notified after `problem_delay` and cleared when resolved."""
    entry = await setup_entry(hass, notify_services=[PHONE])
    await advance(hass, freezer, 61)
    tag = f"bhv_{entry8(entry)}_problem_actuator_unavailable"

    await set_state(hass, NUMBER_FLOW, "unavailable")
    await advance(hass, freezer, 9 * 60)
    assert phone == []

    await advance(hass, freezer, 61)

    [call] = phone
    assert call.data == {
        "title": "Ventilation problem",
        # The friendly name (here derived from the entity id).
        "message": "unit flow is unavailable; nothing can be written to the unit.",
        "data": {"tag": tag},
    }

    # Still there: not sent again.
    await advance(hass, freezer, 60)
    assert len(phone) == 1

    await set_state(
        hass,
        NUMBER_FLOW,
        "100",
        {"unit_of_measurement": "m³/h", "min": 0, "max": 400, "step": 1},
    )

    assert phone[-1].data == {"message": "clear_notification", "data": {"tag": tag}}


@pytest.mark.usefixtures("entities")
async def test_problem_notifications_can_be_switched_off(
    hass: HomeAssistant,
    unit: MockUnit,
    freezer: FrozenDateTimeFactory,
    phone: list[ServiceCall],
) -> None:
    """`notify_problems` off: the problem sensor turns on, no notification."""
    entry = await setup_entry(hass, notify_services=[PHONE], notify_problems=False)
    await advance(hass, freezer, 61)
    await set_state(hass, NUMBER_FLOW, "unavailable")

    await advance(hass, freezer, 11 * 60)

    decision = controller_of(entry).current_decision
    assert decision is not None
    assert decision.problem
    assert phone == []


# --- Missing notify service -----------------------------------------------------------------


@pytest.mark.usefixtures("entities")
async def test_missing_notify_service_repair_and_fallback(
    hass: HomeAssistant, unit: MockUnit, freezer: FrozenDateTimeFactory
) -> None:
    """A missing service raises a repair issue and a problem; it goes when back."""
    entry = await warning_entry(hass, freezer, notify_services=[f"notify.{PHONE}"])
    issue_id = f"missing_notify_service_{PHONE}"
    registry = ir.async_get(hass)
    controller = controller_of(entry)

    issue = registry.async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.translation_key == "missing_notify_service"
    assert issue.translation_placeholders == {
        "service": f"notify.{PHONE}",
        "entries": "Ventilation",
    }
    assert (str(ProblemKey.MISSING_NOTIFY_SERVICE), None) in problem_keys(controller)

    # Without any existing service the notification is persistent.
    await set_state(hass, SMOKE, "on")
    assert f"bhv_{entry8(entry)}_safety" in notifications(hass)

    calls = async_mock_service(hass, "notify", PHONE)
    await hass.async_block_till_done()

    assert registry.async_get_issue(DOMAIN, issue_id) is None
    assert (str(ProblemKey.MISSING_NOTIFY_SERVICE), None) not in problem_keys(
        controller
    )
    await hass.services.async_call(DOMAIN, "override_safety", {}, blocking=True)
    await hass.async_block_till_done()
    assert f"bhv_{entry8(entry)}_safety" not in notifications(hass)
    assert calls[-1].data["message"] == "clear_notification"

    # Removing the service raises the issue again; unloading removes it.
    hass.services.async_remove("notify", PHONE)
    await hass.async_block_till_done()
    assert registry.async_get_issue(DOMAIN, issue_id) is not None
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert registry.async_get_issue(DOMAIN, issue_id) is None


@pytest.mark.usefixtures("entities")
async def test_missing_service_of_two_entries(
    hass: HomeAssistant, unit: MockUnit, freezer: FrozenDateTimeFactory
) -> None:
    """One issue per service lists every entry that misses it."""
    first = await setup_entry(hass, notify_services=[PHONE])
    second = MockConfigEntry(
        domain=DOMAIN,
        title="Attic ventilation",
        data={},
        options={**first.options, "actuator": FAN, "notify_services": [PHONE]},
        version=1,
        minor_version=2,
    )
    second.add_to_hass(hass)
    assert await hass.config_entries.async_setup(second.entry_id)
    await hass.async_block_till_done()
    issue_id = f"missing_notify_service_{PHONE}"

    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.translation_placeholders is not None
    assert issue.translation_placeholders["entries"] == (
        "Attic ventilation, Ventilation"
    )

    assert await hass.config_entries.async_unload(first.entry_id)
    await hass.async_block_till_done()
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.translation_placeholders is not None
    assert issue.translation_placeholders["entries"] == "Attic ventilation"


# --- Robustness ------------------------------------------------------------------------------


@pytest.mark.usefixtures("entities")
async def test_failing_service_is_logged_once_and_others_still_get_it(
    hass: HomeAssistant,
    unit: MockUnit,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failing phone never blocks control; its error is logged once."""
    tablet = async_mock_service(hass, "notify", TABLET)
    attempts: list[ServiceCall] = []

    async def fail(call: ServiceCall) -> None:
        attempts.append(call)
        raise HomeAssistantError("phone not reachable")

    hass.services.async_register("notify", PHONE, fail)
    await warning_entry(hass, freezer, notify_services=[PHONE, TABLET])
    caplog.set_level(logging.WARNING)

    await set_state(hass, SMOKE, "on")
    await hass.services.async_call(DOMAIN, "override_safety", {}, blocking=True)
    await hass.async_block_till_done()

    assert mode_of(hass) == "automatic"
    assert len(attempts) == 2
    assert [call.data["message"] for call in tablet] == [
        "Outdoor smoke reports a warning. The ventilation was switched off.",
        "clear_notification",
    ]
    failures = [r for r in caplog.records if "notify." + PHONE in r.getMessage()]
    assert len(failures) == 1

    # The phone is back: removing the override shuts down and notifies again.
    hass.services.async_remove("notify", PHONE)
    phone = async_mock_service(hass, "notify", PHONE)
    caplog.clear()
    caplog.set_level(logging.INFO)
    await turn(hass, "turn_off")

    assert phone[-1].data["title"] == "Ventilation off: Outdoor smoke"
    assert any("work again" in r.getMessage() for r in caplog.records)


@pytest.mark.usefixtures("entities")
async def test_slow_service_does_not_block_the_evaluation(
    hass: HomeAssistant, unit: MockUnit, freezer: FrozenDateTimeFactory
) -> None:
    """The evaluation goes on while a notify call hangs (timeout 10 s)."""
    release = asyncio.Event()

    async def hang(call: ServiceCall) -> None:
        await release.wait()

    hass.services.async_register("notify", PHONE, hang)
    entry = await warning_entry(hass, freezer, notify_services=[PHONE])

    hass.states.async_set(SMOKE, "on")
    controller = controller_of(entry)
    for _ in range(100):
        await asyncio.sleep(0)
        decision = controller.current_decision
        if decision is not None and decision.mode is Mode.SAFETY_OFF:
            break
    decision = controller.current_decision
    assert decision is not None
    assert decision.mode is Mode.SAFETY_OFF
    release.set()
    await hass.async_block_till_done()


# --- Translations (Q7) ---------------------------------------------------------------------


@pytest.mark.parametrize("language", ["en", "de"])
async def test_q7_common_translations_load(hass: HomeAssistant, language: str) -> None:
    """The `common` category of the custom integration loads in en and de."""
    translations = await async_get_translations(hass, language, "common", {DOMAIN})
    prefix = f"component.{DOMAIN}.common."
    for key in (
        "notify_safety_title",
        "notify_restart_message",
        "notify_reminder_title",
        "notify_problem_title",
        "notify_action_keep_ventilating",
    ):
        assert translations[prefix + key]
    if language == "de":
        assert translations[prefix + "notify_safety_title"] == (
            "Lüftung aus: {headline}"
        )


@pytest.mark.usefixtures("entities")
async def test_notification_in_the_server_language(
    hass: HomeAssistant,
    unit: MockUnit,
    freezer: FrozenDateTimeFactory,
    phone: list[ServiceCall],
) -> None:
    """Texts follow the server language (German here)."""
    hass.config.language = "de"
    entry = await warning_entry(hass, freezer, notify_services=[PHONE])

    await set_state(hass, SMOKE, "on")

    [call] = phone
    assert call.data["title"] == "Lüftung aus: Outdoor smoke"
    assert call.data["data"]["actions"][0] == {
        "action": f"BHV_{entry8(entry)}_KEEP_VENTILATING",
        "title": "Weiter lüften",
    }


# --- Formatting helpers -----------------------------------------------------------------------


def test_action_commands_and_helpers() -> None:
    """Every action maps to its command; OK has none."""
    assert action_command(Action.OK) is None
    assert type(action_command(Action.KEEP_VENTILATING)).__name__ == (
        "OverrideSafetyCommand"
    )
    assert type(action_command(Action.RESTART)).__name__ == "ResumeCommand"
    assert type(action_command(Action.RESUME)).__name__ == "ResumeCommand"
    assert type(action_command(Action.KEEP_OFF)).__name__ == "KeepOffCommand"
    assert service_name("notify.mobile_app_x") == "mobile_app_x"
    assert service_name("mobile_app_x") == "mobile_app_x"
    now = dt_util.now()
    assert format_time(now, now) == dt_util.as_local(now).strftime("%H:%M")
    yesterday = now - timedelta(days=1)
    assert format_time(yesterday, now) == dt_util.as_local(yesterday).strftime(
        "%Y-%m-%d %H:%M"
    )


def test_format_safety_without_warnings_and_missing_texts() -> None:
    """No warnings: nothing to send; missing texts fall back to the key."""
    from custom_components.better_home_ventilation.engine.models import (  # noqa: PLC0415
        Notify,
    )

    now = dt_util.now()
    assert format_notify(Notify(kind=NotifyKind.SAFETY), "abc", {}, now) is None
    message = format_notify(Notify(kind=NotifyKind.RESTARTED), "abcdefghij", {}, now)
    assert message is not None
    assert message.title == "notify_restarted_title"
    broken = format_notify(
        Notify(kind=NotifyKind.REMINDER, off_since=now),
        "abcdefghij",
        {"notify_reminder_title": "since {unknown}"},
        now,
    )
    assert broken is not None
    assert broken.title == "since {unknown}"
