"""Phone notifications with actions (BHV-41, BHV-43, BHV-24).

docs/spec/04-interface.md#notifications. The engine emits ``Notify`` and
``ClearNotification`` effects with logical tags; this module formats them with
the ``common`` translations in the server language (English as fallback) and
sends them to the configured ``notify.mobile_app_*`` services, or creates a
persistent notification when no configured service exists. It also sends the
problem notifications (after ``problem_delay``), listens to
``mobile_app_notification_action`` and maps the action ids of its entry to
commands with source ``notification``, and raises the repair issue
``missing_notify_service_<service>`` for configured services that no longer
exist.

The module is not called ``notify.py``: that name is the notify platform of an
integration (``notify/legacy.py`` imports ``<integration>.notify`` for a
configured platform).

Sending never blocks an evaluation: every notification runs in its own task,
in order (a clear never overtakes the notification it clears), each service
call with a timeout of :data:`SEND_TIMEOUT`. Failures are logged once per
service until it works again.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
import logging
from typing import Any, Final

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_SERVICE_REGISTERED, EVENT_SERVICE_REMOVED
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.translation import async_get_translations
from homeassistant.util import dt as dt_util
from homeassistant.util.hass_dict import HassKey

from .const import (
    CONF_NOTIFY_PROBLEMS,
    CONF_NOTIFY_REMINDERS,
    CONF_NOTIFY_SAFETY,
    CONF_NOTIFY_SERVICES,
    DEFAULTS,
    DOMAIN,
)
from .engine.models import (
    ClearNotification,
    Command,
    Decision,
    KeepOffCommand,
    NotificationTag,
    Notify,
    NotifyKind,
    OverrideSafetyCommand,
    ProblemKey,
    ResumeCommand,
    Source,
)
from .engine.safety import notification_text, warning_title

_LOGGER = logging.getLogger(__name__)

NOTIFY_DOMAIN: Final = "notify"
#: Prefix of the notify services of the companion apps.
MOBILE_APP_PREFIX: Final = "mobile_app_"
#: Event fired by the companion apps when a notification action is tapped.
EVENT_NOTIFICATION_ACTION: Final = "mobile_app_notification_action"
#: Message that makes the companion apps remove the notification with a tag.
CLEAR_MESSAGE: Final = "clear_notification"
#: Timeout of every notify service call in seconds.
SEND_TIMEOUT: Final = 10
#: Repair issue id prefix; the service name follows.
ISSUE_MISSING_NOTIFY_SERVICE: Final = "missing_notify_service"

_COMMON: Final = f"component.{DOMAIN}.common."

#: Missing notify services per loaded entry: entry id -> (title, services).
_MISSING: HassKey[dict[str, tuple[str, frozenset[str]]]] = HassKey(
    f"{DOMAIN}_missing_notify_services"
)


class Action(StrEnum):
    """Notification actions; the action id is ``BHV_<entry8>_<ACTION>``."""

    KEEP_VENTILATING = "KEEP_VENTILATING"
    OK = "OK"
    RESTART = "RESTART"
    KEEP_OFF = "KEEP_OFF"
    RESUME = "RESUME"


#: Actions of each notification kind, in the order they are shown.
KIND_ACTIONS: Final[Mapping[NotifyKind, tuple[Action, ...]]] = {
    NotifyKind.SAFETY: (Action.KEEP_VENTILATING, Action.OK),
    NotifyKind.RESTART: (Action.RESTART, Action.KEEP_OFF),
    NotifyKind.RESTARTED: (),
    NotifyKind.REMINDER: (Action.RESUME,),
}

#: Option that switches each notification kind on or off.
KIND_OPTION: Final[Mapping[NotifyKind, str]] = {
    NotifyKind.SAFETY: CONF_NOTIFY_SAFETY,
    NotifyKind.RESTART: CONF_NOTIFY_SAFETY,
    NotifyKind.RESTARTED: CONF_NOTIFY_SAFETY,
    NotifyKind.REMINDER: CONF_NOTIFY_REMINDERS,
}
TAG_OPTION: Final[Mapping[NotificationTag, str]] = {
    NotificationTag.SAFETY: CONF_NOTIFY_SAFETY,
    NotificationTag.REMINDER: CONF_NOTIFY_REMINDERS,
}
#: Kinds sent with the time-sensitive / high priority data.
URGENT_KINDS: Final = frozenset({NotifyKind.SAFETY, NotifyKind.RESTART})

#: Priority data of safety notifications: iOS interruption level, Android
#: priority and time to live (the companion app docs; the same keys as the
#: mobile_app timer notifications, ``mobile_app/timers.py``).
URGENT_DATA: Final[Mapping[str, Any]] = {
    "push": {"interruption-level": "time-sensitive"},
    "priority": "high",
    "ttl": 0,
}


def entry_short_id(entry_id: str) -> str:
    """Return ``<entry8>``, the first 8 characters of the entry id."""
    return entry_id[:8]


def action_id(entry_id: str, action: Action) -> str:
    """Return the action id ``BHV_<entry8>_<ACTION>``."""
    return f"BHV_{entry_short_id(entry_id)}_{action}"


def notification_tag(entry_id: str, tag: str) -> str:
    """Return the tag ``bhv_<entry8>_<tag>`` sent to the phones."""
    return f"bhv_{entry_short_id(entry_id)}_{tag}"


def problem_tag(entry_id: str, key: ProblemKey) -> str:
    """Return the tag of the problem notification of ``key``."""
    return notification_tag(entry_id, f"problem_{key}")


def action_command(action: Action) -> Command | None:
    """Return the command of a notification action (None for ``OK``)."""
    source = Source.NOTIFICATION
    match action:
        case Action.KEEP_VENTILATING:
            return OverrideSafetyCommand(source=source)
        case Action.RESTART | Action.RESUME:
            return ResumeCommand(source=source)
        case Action.KEEP_OFF:
            return KeepOffCommand(source=source)
    return None


def mobile_app_services(hass: HomeAssistant) -> list[str]:
    """Return the names of all ``notify.mobile_app_*`` services, sorted."""
    return sorted(
        service
        for service in hass.services.async_services_for_domain(NOTIFY_DOMAIN)
        if service.startswith(MOBILE_APP_PREFIX)
    )


def service_name(value: str) -> str:
    """Return a stored notify service without a ``notify.`` prefix."""
    return value.removeprefix(f"{NOTIFY_DOMAIN}.")


def format_time(when: datetime, now: datetime | None = None) -> str:
    """Return ``HH:MM`` in local time, with the date if it is not today."""
    local = dt_util.as_local(when)
    if now is not None and dt_util.as_local(now).date() != local.date():
        return local.strftime("%Y-%m-%d %H:%M")
    return local.strftime("%H:%M")


async def async_load_texts(hass: HomeAssistant, language: str) -> dict[str, str]:
    """Load the ``common`` translations in ``language``, English as fallback (Q7).

    Keys are returned without the ``component.<domain>.common.`` prefix.
    """
    texts: dict[str, str] = {}
    for lang in dict.fromkeys(("en", language)):
        translations = await async_get_translations(hass, lang, "common", {DOMAIN})
        texts.update(
            {
                key.removeprefix(_COMMON): value
                for key, value in translations.items()
                if key.startswith(_COMMON)
            }
        )
    return texts


def _text(texts: Mapping[str, str], key: str, **placeholders: str) -> str:
    """Return a translated text with its placeholders filled in."""
    template = texts.get(key, key)
    try:
        return template.format(**placeholders)
    except KeyError, IndexError, ValueError:
        return template


@dataclass(frozen=True, kw_only=True)
class Message:
    """A formatted notification."""

    title: str
    message: str
    actions: tuple[tuple[str, str], ...] = ()
    urgent: bool = False


def format_notify(
    effect: Notify, entry_id: str, texts: Mapping[str, str], now: datetime
) -> Message | None:
    """Format a ``Notify`` effect (04-interface.md#notifications).

    Returns None for a ``safety`` notification without warnings (the engine
    always sends at least one).
    """
    kind = effect.kind
    actions = tuple(
        (action_id(entry_id, action), _text(texts, f"notify_action_{action.lower()}"))
        for action in KIND_ACTIONS[kind]
    )
    urgent = kind in URGENT_KINDS
    match kind:
        case NotifyKind.SAFETY:
            if not effect.warnings:
                return None
            warning = effect.warnings[0]
            message = notification_text(warning) or _text(
                texts, "notify_safety_message_no_details", source=warning.source_name
            )
            title = _text(texts, "notify_safety_title", headline=warning_title(warning))
        case NotifyKind.RESTART:
            title = _text(texts, "notify_restart_title")
            restart_at = effect.restart_at or now
            message = _text(
                texts, "notify_restart_message", time=format_time(restart_at, now)
            )
        case NotifyKind.RESTARTED:
            title = _text(texts, "notify_restarted_title")
            message = _text(texts, "notify_restarted_message")
        case NotifyKind.REMINDER:
            off_since = effect.off_since or now
            title = _text(
                texts, "notify_reminder_title", time=format_time(off_since, now)
            )
            message = _text(texts, "notify_reminder_message")
    return Message(title=title, message=message, actions=actions, urgent=urgent)


def format_problem(
    key: ProblemKey, entities: list[str], texts: Mapping[str, str]
) -> Message:
    """Format the notification of a problem; ``entities`` are display names."""
    return Message(
        title=_text(texts, "notify_problem_title"),
        message=_text(texts, f"notify_problem_{key}", entities=", ".join(entities)),
    )


def service_data(message: Message, tag: str) -> dict[str, Any]:
    """Return the data of a ``notify.mobile_app_*`` call."""
    data: dict[str, Any] = {"tag": tag}
    if message.actions:
        data["actions"] = [
            {"action": action, "title": title} for action, title in message.actions
        ]
    if message.urgent:
        data.update(URGENT_DATA)
    return {"message": message.message, "title": message.title, "data": data}


@callback
def _async_sync_issues(hass: HomeAssistant) -> None:
    """Create or delete the ``missing_notify_service_<service>`` issues.

    One issue per service for all loaded entries that miss it; issues of
    services no entry misses any more are deleted.
    """
    by_service: dict[str, list[str]] = {}
    for title, services in hass.data.get(_MISSING, {}).values():
        for service in services:
            by_service.setdefault(service, []).append(title)
    prefix = f"{ISSUE_MISSING_NOTIFY_SERVICE}_"
    registry = ir.async_get(hass)
    for domain, issue_id in list(registry.issues):
        if (
            domain == DOMAIN
            and issue_id.startswith(prefix)
            and issue_id.removeprefix(prefix) not in by_service
        ):
            ir.async_delete_issue(hass, DOMAIN, issue_id)
    for service, titles in by_service.items():
        ir.async_create_issue(
            hass,
            DOMAIN,
            f"{prefix}{service}",
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_MISSING_NOTIFY_SERVICE,
            translation_placeholders={
                "service": f"{NOTIFY_DOMAIN}.{service}",
                "entries": ", ".join(sorted(titles)),
            },
        )


class Notifier:
    """Sends and clears the notifications of one entry."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        run_command: Callable[[Command], Awaitable[None]],
        on_problems_changed: Callable[[], None],
    ) -> None:
        """Read the notification options of the entry."""
        self.hass = hass
        self.entry = entry
        options = entry.options
        self.services: tuple[str, ...] = tuple(
            dict.fromkeys(
                service_name(str(service))
                for service in options.get(CONF_NOTIFY_SERVICES) or ()
            )
        )
        self._enabled = {
            key: bool(options.get(key, DEFAULTS[key]))
            for key in (CONF_NOTIFY_SAFETY, CONF_NOTIFY_REMINDERS, CONF_NOTIFY_PROBLEMS)
        }
        self._run_command = run_command
        self._on_problems_changed = on_problems_changed
        self._actions = {action_id(entry.entry_id, action): action for action in Action}
        self._unsubs: list[CALLBACK_TYPE] = []
        self._tail: asyncio.Task[None] | None = None
        self._failing: set[str] = set()
        self._problems_sent: dict[ProblemKey, tuple[str, ...]] = {}
        self._stopped = False
        #: Configured services that do not exist (problem and repair issue).
        self.missing: frozenset[str] = frozenset()

    # --- Lifecycle ------------------------------------------------------------

    @callback
    def async_setup(self) -> None:
        """Listen to notification actions (the answer can come at any time)."""
        self._unsubs.append(
            self.hass.bus.async_listen(EVENT_NOTIFICATION_ACTION, self._async_action)
        )

    @callback
    def async_start(self) -> None:
        """Check the configured services and follow (un)registered services."""
        if self._stopped:
            return
        if self.services:
            for event_type in (EVENT_SERVICE_REGISTERED, EVENT_SERVICE_REMOVED):
                self._unsubs.append(
                    self.hass.bus.async_listen(event_type, self._async_service_changed)
                )
        self._async_check_services()

    @callback
    def async_stop(self) -> None:
        """Stop listening; pending notifications still finish (with a timeout)."""
        self._stopped = True
        unsubs, self._unsubs = self._unsubs, []
        for unsub in unsubs:
            unsub()
        registry = self.hass.data.get(_MISSING)
        if registry is not None and self.entry.entry_id in registry:
            del registry[self.entry.entry_id]
            _async_sync_issues(self.hass)

    # --- Problems -------------------------------------------------------------

    @property
    def problems(self) -> frozenset[ProblemKey]:
        """Problems detected here (``missing_notify_service``)."""
        if self.missing:
            return frozenset({ProblemKey.MISSING_NOTIFY_SERVICE})
        return frozenset()

    @callback
    def _async_service_changed(self, event: Event) -> None:
        """Re-check the configured services when a notify service changes."""
        if event.data.get("domain") == NOTIFY_DOMAIN:
            self._async_check_services()

    @callback
    def _async_check_services(self) -> None:
        """Update the missing services, their repair issues and the problem."""
        missing = frozenset(
            service
            for service in self.services
            if not self.hass.services.has_service(NOTIFY_DOMAIN, service)
        )
        if missing == self.missing:
            return
        for service in sorted(missing - self.missing):
            _LOGGER.warning(
                "Notify service notify.%s of %s does not exist",
                service,
                self.entry.title,
            )
        self.missing = missing
        registry = self.hass.data.setdefault(_MISSING, {})
        if missing:
            registry[self.entry.entry_id] = (self.entry.title, missing)
        else:
            registry.pop(self.entry.entry_id, None)
        _async_sync_issues(self.hass)
        self._on_problems_changed()

    # --- Actions --------------------------------------------------------------

    @callback
    def _async_action(self, event: Event) -> None:
        """Map an action id of this entry to its command (source notification)."""
        action = self._actions.get(str(event.data.get("action")))
        if action is None or self._stopped:
            return
        _LOGGER.debug("Notification action %s for %s", action, self.entry.title)
        if (command := action_command(action)) is not None:
            self.entry.async_create_task(
                self.hass,
                self._async_command(command),
                "better_home_ventilation notification action",
            )

    async def _async_command(self, command: Command) -> None:
        """Run a command; a rejected one (e.g. a late answer) is ignored."""
        try:
            await self._run_command(command)
        except ServiceValidationError as err:
            _LOGGER.debug(
                "Notification action of %s ignored: %s",
                self.entry.title,
                err.translation_key,
            )

    # --- Sending --------------------------------------------------------------

    @callback
    def async_handle(self, effect: Notify | ClearNotification) -> None:
        """Send or clear a notification of the engine (if its kind is enabled)."""
        entry_id = self.entry.entry_id
        if isinstance(effect, Notify):
            if not self._enabled[KIND_OPTION[effect.kind]]:
                return
            tag = notification_tag(entry_id, effect.tag)

            async def build(texts: Mapping[str, str]) -> Message | None:
                return format_notify(effect, entry_id, texts, dt_util.now())

            self._enqueue(self._async_send(tag, build))
        elif self._enabled[TAG_OPTION[effect.tag]]:
            self._enqueue(self._async_clear(notification_tag(entry_id, effect.tag)))

    @callback
    def async_update_problems(
        self, decision: Decision, problem_delay: timedelta
    ) -> None:
        """Send problem notifications after ``problem_delay``; clear resolved ones.

        One notification (tag) per problem key; it is sent when the key first
        persists longer than ``problem_delay`` and cleared when no problem of
        the key is left.
        """
        if not self._enabled[CONF_NOTIFY_PROBLEMS]:
            return
        persistent: dict[ProblemKey, list[str]] = {}
        for problem in decision.problems:
            if decision.time - problem.since < problem_delay:
                continue
            entities = persistent.setdefault(problem.key, [])
            if problem.entity_id is not None:
                entities.append(problem.entity_id)
        entry_id = self.entry.entry_id
        for key in sorted(persistent.keys() - self._problems_sent.keys()):
            names = [self._display_name(entity_id) for entity_id in persistent[key]]

            async def build(
                texts: Mapping[str, str],
                key: ProblemKey = key,
                names: list[str] = names,
            ) -> Message:
                return format_problem(key, names, texts)

            self._enqueue(self._async_send(problem_tag(entry_id, key), build))
        for key in sorted(self._problems_sent.keys() - persistent.keys()):
            self._enqueue(self._async_clear(problem_tag(entry_id, key)))
        self._problems_sent = {
            key: tuple(entities) for key, entities in persistent.items()
        }

    def _display_name(self, entity_id: str) -> str:
        """Return the friendly name of an entity, or its id."""
        state = self.hass.states.get(entity_id)
        return state.name if state is not None else entity_id

    @callback
    def _enqueue(self, job: Awaitable[None]) -> None:
        """Run ``job`` after the previous notification job (in order)."""
        previous = self._tail

        async def run() -> None:
            if previous is not None and not previous.done():
                await asyncio.wait([previous])
            await job

        self._tail = self.entry.async_create_task(
            self.hass, run(), "better_home_ventilation notification"
        )

    def _targets(self) -> list[str]:
        """Return the configured services that exist."""
        return [
            service
            for service in self.services
            if self.hass.services.has_service(NOTIFY_DOMAIN, service)
        ]

    async def _async_send(
        self,
        tag: str,
        build: Callable[[Mapping[str, str]], Awaitable[Message | None]],
    ) -> None:
        """Format and send one notification to every target (or persistently)."""
        texts = await async_load_texts(self.hass, self.hass.config.language)
        message = await build(texts)
        if message is None:
            return
        targets = self._targets()
        if not targets:
            # No (existing) phone: a persistent notification, without actions.
            persistent_notification.async_create(
                self.hass, message.message, message.title, notification_id=tag
            )
            return
        data = service_data(message, tag)
        await asyncio.gather(*(self._async_call(service, data) for service in targets))

    async def _async_clear(self, tag: str) -> None:
        """Clear a notification on every target and the persistent one."""
        persistent_notification.async_dismiss(self.hass, tag)
        data = {"message": CLEAR_MESSAGE, "data": {"tag": tag}}
        await asyncio.gather(
            *(self._async_call(service, data) for service in self._targets())
        )

    async def _async_call(self, service: str, data: dict[str, Any]) -> None:
        """Call one notify service; errors are logged once until it works again."""
        try:
            async with asyncio.timeout(SEND_TIMEOUT):
                await self.hass.services.async_call(
                    NOTIFY_DOMAIN, service, data, blocking=True
                )
        except Exception as err:  # noqa: BLE001 - a notification never fails control
            if service not in self._failing:
                self._failing.add(service)
                _LOGGER.warning(
                    "Sending a notification of %s to notify.%s failed: %s",
                    self.entry.title,
                    service,
                    err or type(err).__name__,
                )
            return
        if service in self._failing:
            self._failing.discard(service)
            _LOGGER.info("Notifications to notify.%s work again", service)
