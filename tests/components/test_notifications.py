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
    ControlSwitchCommand,
    Mode,
    NotifyKind,
    PauseCommand,
    ProblemKey,
    Source,
)
from custom_components.better_home_ventilation.notifications import (
    Action,
    action_command,
    format_notify,
    format_time,
    service_name,
)

from .common import CONTROL_MODE, FAN, NUMBER_FLOW, STANDBY_SWITCH, MockUnit, advance
from .test_controller import controller_of, problem_keys, readiness, setup_entry
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


SAFETY_DISABLED_TEXTS = {
    "en": (
        "Warning: Outdoor smoke",
        "Outdoor smoke reports a warning. Control is off, so the integration"
        " does not switch the ventilation off.",
        "OK",
    ),
    "de": (
        "Warnung: Outdoor smoke",
        "Outdoor smoke meldet eine Warnung. Die Regelung ist aus, deshalb"
        " schaltet die Integration die Lüftung nicht ab.",
        "OK",
    ),
}


@pytest.mark.parametrize("language", ["en", "de"])
@pytest.mark.usefixtures("entities")
async def test_q14_warning_while_control_is_off_notifies_only(
    hass: HomeAssistant,
    unit: MockUnit,
    freezer: FrozenDateTimeFactory,
    phone: list[ServiceCall],
    language: str,
) -> None:
    """Q14: control off, `safety_when_disabled` off: notified, nothing switched.

    Only the action OK; time-sensitive like the safety notification; cleared
    when the warning ends (BHV-41, BHV-44).
    """
    entry = await warning_entry(
        hass, freezer, notify_services=[PHONE], safety_when_disabled=False
    )
    # After the setup: the entity ids stay English, the texts follow.
    hass.config.language = language
    short = entry8(entry)
    await hass.services.async_call(
        "switch",
        "turn_off",
        {"entity_id": "switch.ventilation_control"},
        blocking=True,
    )
    await hass.async_block_till_done()
    calls = len(unit.calls)

    await set_state(hass, SMOKE, "on")

    assert mode_of(hass) == "disabled"
    assert unit.calls[calls:] == []
    [call] = phone
    title, message, ok = SAFETY_DISABLED_TEXTS[language]
    assert call.data == {
        "title": title,
        "message": message,
        "data": {
            "tag": f"bhv_{short}_safety",
            "actions": [{"action": f"BHV_{short}_OK", "title": ok}],
            "push": {"interruption-level": "time-sensitive"},
            "priority": "high",
            "ttl": 0,
        },
    }

    await tap(hass, f"BHV_{short}_OK")
    assert not controller_of(entry).state.safety.overridden

    await set_state(hass, SMOKE, "off")

    assert phone[-1].data == {
        "message": "clear_notification",
        "data": {"tag": f"bhv_{short}_safety"},
    }
    assert controller_of(entry).state.safety.restart_pending is None
    assert unit.calls[calls:] == []


def test_q14_format_with_warning_text() -> None:
    """Q14: with details the text of the warning comes first."""
    from custom_components.better_home_ventilation.engine.models import (  # noqa: PLC0415
        Notify,
        WarningInfo,
    )

    warning = WarningInfo(
        source_id="nina",
        source_name="City warnings",
        entity_id="binary_sensor.example_city_warning_1",
        incident_key="nina:fire",
        headline="Fire in the industrial area",
        text="Smoke is drifting over the city.",
        recommended_actions="Close windows and doors.",
        severity=None,
        sender="Fire brigade",
        since=dt_util.now(),
    )
    texts = {
        "notify_safety_disabled_title": "Warning: {headline}",
        "notify_safety_disabled_message": "{text} Control is off.",
    }
    notice = Notify(kind=NotifyKind.SAFETY_DISABLED, warnings=(warning,))
    message = format_notify(notice, "abcdefghij", texts, dt_util.now())
    assert message is not None
    assert message.title == "Warning: Fire in the industrial area"
    assert message.message == "Close windows and doors. (Fire brigade) Control is off."
    assert message.urgent
    empty = Notify(kind=NotifyKind.SAFETY_DISABLED)
    assert format_notify(empty, "abcdefghij", texts, dt_util.now()) is None


REMINDER_TEXTS = {
    "en": (
        "Ventilation off since",
        "Moisture protection is inactive.",
        "Turn control on",
    ),
    "de": (
        "Lüftung aus seit",
        "Der Feuchteschutz ist nicht aktiv.",
        "Steuerung einschalten",
    ),
}


@pytest.mark.parametrize("language", ["en", "de"])
@pytest.mark.usefixtures("entities")
async def test_q15_reminder_while_control_is_off_and_unit_stopped(
    hass: HomeAssistant,
    unit: MockUnit,
    freezer: FrozenDateTimeFactory,
    phone: list[ServiceCall],
    language: str,
) -> None:
    """Q15: a pause stopped the unit, then control off: reminded after 24 h.

    The reminder offers "Turn control on" instead of Resume (owner decision
    2026-11-03, BHV-24); tapping it switches control on, the unit is started
    and the reminder cleared.
    """
    entry = await setup_entry(hass, notify_services=[PHONE], off_entity=STANDBY_SWITCH)
    await advance(hass, freezer, 61)
    hass.config.language = language
    short = entry8(entry)
    controller = controller_of(entry)
    await controller.async_command(PauseCommand(source=Source.SERVICE))
    await hass.async_block_till_done()
    assert unit.calls_for("switch", "turn_on")  # the standby switch: stopped
    off_since = dt_util.now()
    await controller.async_command(
        ControlSwitchCommand(source=Source.SERVICE, enabled=False)
    )
    await hass.async_block_till_done()
    assert mode_of(hass) == "disabled"

    def reminders() -> list[ServiceCall]:
        # The sensors go stale meanwhile: problem notifications are sent too.
        tag = f"bhv_{short}_reminder"
        return [call for call in phone if call.data["data"]["tag"] == tag]

    await advance(hass, freezer, 8 * 3600)
    assert reminders() == []
    await advance(hass, freezer, 16 * 3600)

    [reminder] = reminders()
    title, message, control_on = REMINDER_TEXTS[language]
    assert reminder.data == {
        "title": f"{title} {format_time(off_since, dt_util.now())}",
        "message": message,
        "data": {
            "tag": f"bhv_{short}_reminder",
            "actions": [{"action": f"BHV_{short}_CONTROL_ON", "title": control_on}],
        },
    }

    await tap(hass, f"BHV_{short}_CONTROL_ON")

    assert controller.state.control_enabled
    assert mode_of(hass) == "fallback"  # the CO₂ sensor went stale meanwhile
    assert unit.calls_for("switch", "turn_off")  # switched on again
    assert reminders()[-1].data == {
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
async def test_q13_blocked_safety_off_is_notified_as_not_ready(
    hass: HomeAssistant,
    unit: MockUnit,
    freezer: FrozenDateTimeFactory,
    phone: list[ServiceCall],
) -> None:
    """Q13: a safety off blocked by readiness is the problem `not_ready`.

    The warning is notified at once; nothing is written; the problem
    notification follows after `problem_delay`; the off once the condition is met.
    """
    hass.states.async_set(SMOKE, "off")
    entry = await setup_entry(
        hass,
        notify_services=[PHONE],
        subentries=[
            # Without details the periodic evaluations finish within
            # `async_block_till_done` (no provider tasks in the background).
            source(entity_id=SMOKE, name="Outdoor smoke", data={"details": "none"}),
            readiness(CONTROL_MODE, "Remote", auto_correct=False),
        ],
    )
    await advance(hass, freezer, 61)
    short = entry8(entry)

    await set_state(hass, SMOKE, "on")

    assert mode_of(hass) == "safety_off"
    assert [call.data["data"]["tag"] for call in phone] == [f"bhv_{short}_safety"]
    assert unit.calls_for("number", "set_value") == []
    assert ("not_ready", CONTROL_MODE) in problem_keys(controller_of(entry))

    # `not_ready` since the setup (unmet in the grace): notified 10 min later.
    await advance(hass, freezer, 8 * 60)
    assert len(phone) == 1
    await advance(hass, freezer, 60)

    problem = phone[-1]
    assert problem.data == {
        "title": "Ventilation problem",
        "message": (
            "The readiness condition on Unit control mode is not met; "
            "nothing is written to the unit."
        ),
        "data": {"tag": f"bhv_{short}_problem_not_ready"},
    }
    assert unit.calls_for("number", "set_value") == []

    await set_state(hass, CONTROL_MODE, "Remote", {"options": ["Manual", "Remote"]})

    assert [call.data["value"] for call in unit.calls_for("number", "set_value")] == [
        0.0
    ]
    assert phone[-1].data == {
        "message": "clear_notification",
        "data": {"tag": f"bhv_{short}_problem_not_ready"},
    }


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
    control_on = action_command(Action.CONTROL_ON)
    assert isinstance(control_on, ControlSwitchCommand)
    assert control_on.enabled
    assert control_on.source is Source.NOTIFICATION
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
