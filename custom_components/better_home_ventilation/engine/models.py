"""Data model of the engine: options, snapshot, controller state, decision, effects.

Everything here is plain, immutable data (frozen dataclasses and string enums).
The glue builds an :class:`Options` once per entry load and a :class:`Snapshot`
per evaluation; the engine returns a :class:`Decision`, a new
:class:`ControllerState` and a list of effects. See
docs/spec/05-architecture.md#engine-and-glue.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from enum import StrEnum
from typing import Any, Final

from . import options as opt

# --- Enumerations ------------------------------------------------------------


class Mode(StrEnum):
    """Resolved operating mode (docs/spec/03-modes.md#mode-resolution).

    ``shower`` and ``cooking`` belong to phase 0.3; they are listed for the
    entity options but never resolved by the engine yet.
    """

    AUTOMATIC = "automatic"
    FALLBACK = "fallback"
    BOOST = "boost"
    SHOWER = "shower"
    COOKING = "cooking"
    OFF = "off"
    SAFETY_OFF = "safety_off"
    DISABLED = "disabled"


#: Modes in which the unit runs (the off actuator is switched on if needed).
RUNNING_MODES: Final = frozenset(
    {Mode.AUTOMATIC, Mode.FALLBACK, Mode.BOOST, Mode.SHOWER, Mode.COOKING}
)

#: Off states (CONTEXT.md): the modes that stop the unit.
OFF_MODES: Final = frozenset({Mode.OFF, Mode.SAFETY_OFF})


class Reason(StrEnum):
    """Rule or mode that determined the setpoint (04-interface.md#reasons)."""

    MINIMUM = "minimum"
    CO2 = "co2"
    HUMIDITY_HIGH = "humidity_high"
    DRYNESS_PROTECTION = "dryness_protection"
    AWAY = "away"
    QUIET_LIMIT = "quiet_limit"
    FALLBACK = "fallback"
    BOOST = "boost"
    SHOWER = "shower"
    COOKING = "cooking"
    OFF = "off"
    SAFETY_OFF = "safety_off"


class Profile(StrEnum):
    """Strategy of the automatic demand (BHV-26, 02-control.md#profiles)."""

    BALANCED = "balanced"
    AIR_QUALITY = "air_quality"
    QUIET = "quiet"
    WINTER = "winter"


class Source(StrEnum):
    """Origin of a command, for the logbook (03-modes.md#commands)."""

    BUTTON = "button"
    SERVICE = "service"
    VOICE = "voice"
    NOTIFICATION = "notification"
    EXTERNAL = "external"


class Trigger(StrEnum):
    """Trigger of a mode change (03-modes.md#mode-change-events)."""

    STARTUP = "startup"
    COMMAND = "command"
    TIMEOUT = "timeout"
    TIMEOUT_WHILE_OFFLINE = "timeout_while_offline"
    WARNING = "warning"
    WARNING_ENDED = "warning_ended"
    OVERRIDE = "override"
    DETECTOR_START = "detector_start"
    DETECTOR_END = "detector_end"
    INPUTS_LOST = "inputs_lost"
    INPUTS_RESTORED = "inputs_restored"
    CONTROL_SWITCH = "control_switch"


class WriteBlock(StrEnum):
    """Why no value was written (``write_blocked_by``, 02-control.md)."""

    GRACE = "grace"
    INTERVAL = "interval"
    DEADBAND = "deadband"
    DISABLED = "disabled"
    NOT_READY = "not_ready"
    UNAVAILABLE = "unavailable"


class Limit(StrEnum):
    """Active limit of the automatic demand (02-control.md#limits)."""

    AWAY = "away"
    QUIET = "quiet"
    DRY = "dry"


class ProblemKey(StrEnum):
    """Problems known to the engine (04-interface.md#problem-attributes).

    ``write_failed``, ``write_mismatch`` and ``missing_notify_service`` are
    detected by the glue and passed in through
    :attr:`Snapshot.reported_problems`; ``warning_details_failed`` comes from
    :attr:`Snapshot.details_failed`, ``missing_entity`` from
    :attr:`Snapshot.missing_entities`.
    """

    ACTUATOR_UNAVAILABLE = "actuator_unavailable"
    NOT_READY = "not_ready"
    WRITE_FAILED = "write_failed"
    WRITE_MISMATCH = "write_mismatch"
    INPUTS_INVALID = "inputs_invalid"
    NO_VALID_CO2 = "no_valid_co2"
    WARNING_SOURCE_UNAVAILABLE = "warning_source_unavailable"
    WARNING_DETAILS_FAILED = "warning_details_failed"
    MISSING_ENTITY = "missing_entity"
    MISSING_NOTIFY_SERVICE = "missing_notify_service"


class Severity(StrEnum):
    """Normalised severity of a warning (03-modes.md#details-providers)."""

    MINOR = "minor"
    MODERATE = "moderate"
    SEVERE = "severe"
    EXTREME = "extreme"

    @property
    def rank(self) -> int:
        """Order of the severity: minor 1 … extreme 4."""
        return _SEVERITY_RANK[self]


_SEVERITY_RANK: Final = {
    Severity.MINOR: 1,
    Severity.MODERATE: 2,
    Severity.SEVERE: 3,
    Severity.EXTREME: 4,
}


class NotifyKind(StrEnum):
    """Kind of a notification (04-interface.md#notifications)."""

    SAFETY = "safety"
    RESTART = "restart"
    RESTARTED = "restarted"
    REMINDER = "reminder"


class NotificationTag(StrEnum):
    """Logical notification tag; the glue sends it as ``bhv_<entry8>_<tag>``."""

    SAFETY = "safety"
    REMINDER = "reminder"


#: Tag of each notification kind (``restart`` and ``restarted`` replace
#: ``safety``).
NOTIFY_TAGS: Final = {
    NotifyKind.SAFETY: NotificationTag.SAFETY,
    NotifyKind.RESTART: NotificationTag.SAFETY,
    NotifyKind.RESTARTED: NotificationTag.SAFETY,
    NotifyKind.REMINDER: NotificationTag.REMINDER,
}


class OffMethod(StrEnum):
    """How an off mode stops the unit (02-control.md#off-and-on)."""

    OFF_ACTUATOR = "off_actuator"
    MINIMUM = "minimum"


class ActuatorDomain(StrEnum):
    """Supported actuator domains (BHV-01)."""

    NUMBER = "number"
    FAN = "fan"
    SELECT = "select"


#: Event fired on every change of the resolved mode (BHV-52).
EVENT_MODE_CHANGED: Final = "better_home_ventilation_mode_changed"

#: States that mean "no value" for every entity.
UNAVAILABLE_STATES: Final = frozenset({"unavailable", "unknown", ""})


def entity_domain(entity_id: str) -> str:
    """Return the domain part of an entity id (``"fan.unit"`` -> ``"fan"``)."""
    return entity_id.partition(".")[0]


def is_unavailable(state: str | None) -> bool:
    """Return True for a missing entity or an ``unavailable``/``unknown``/empty state."""
    return state is None or state.strip().lower() in UNAVAILABLE_STATES


# --- Options -----------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class ReadinessCondition:
    """One readiness condition (a ``readiness_condition`` config subentry, BHV-04)."""

    entity_id: str
    expected_state: str
    auto_correct: bool = False


def _seconds(value: Any) -> timedelta:
    return timedelta(seconds=float(value))


def _time(value: Any) -> time:
    if isinstance(value, time):
        return value
    return time.fromisoformat(str(value))


def _optional_float(value: Any) -> float | None:
    return None if value is None or value == "" else float(value)


def _entity_list(value: Any) -> tuple[str, ...]:
    if value is None or value == "":
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(item) for item in value)


def _keywords(value: Any) -> tuple[str, ...]:
    """Keyword list without empty entries (surrounding blanks removed)."""
    return tuple(item.strip() for item in _entity_list(value) if item.strip())


def parse_min_severity(value: Any) -> Severity | None:
    """Return the ``min_severity`` option; ``none``/missing disables the filter."""
    if value is None or str(value) in ("", opt.MIN_SEVERITY_NONE):
        return None
    return Severity(str(value))


@dataclass(frozen=True, kw_only=True)
class WarningSource:
    """One warning source (a ``warning_source`` config subentry, BHV-40).

    ``entities`` are the entities whose state the trigger checks: the source
    entity, or all enabled ``binary_sensor`` entities of a source device
    (expanded by the glue). ``details`` is the configured mode
    (``auto``/``attributes``/``none``); the glue picks the provider from it.
    """

    subentry_id: str
    name: str
    entities: tuple[str, ...]
    trigger: str = opt.TRIGGER_ON_OFF
    threshold: float | None = None
    states: tuple[str, ...] = ()
    details: str = opt.DETAILS_AUTO
    headline_keywords: tuple[str, ...] = ()
    text_keywords: tuple[str, ...] = ()
    exclude_keywords: tuple[str, ...] = ()
    min_severity: Severity | None = None

    @classmethod
    def from_mapping(
        cls,
        subentry_id: str,
        data: Mapping[str, Any],
        *,
        title: str | None = None,
        entities: Sequence[str] | None = None,
    ) -> WarningSource:
        """Build a source from the subentry data.

        ``entities`` defaults to the ``source_entity``; a device source passes
        its expanded entities. The name is ``name``, else ``title``, else the
        first entity id.
        """
        members = (
            tuple(entities)
            if entities is not None
            else _entity_list(data.get(opt.CONF_SOURCE_ENTITY))
        )
        name = data.get(opt.CONF_NAME) or title or (members[0] if members else "")
        return cls(
            subentry_id=subentry_id,
            name=str(name),
            entities=members,
            trigger=str(data.get(opt.CONF_TRIGGER) or opt.TRIGGER_ON_OFF),
            threshold=_optional_float(data.get(opt.CONF_THRESHOLD)),
            states=_keywords(data.get(opt.CONF_STATES)),
            details=str(data.get(opt.CONF_DETAILS) or opt.DETAILS_AUTO),
            headline_keywords=_keywords(data.get(opt.CONF_HEADLINE_KEYWORDS)),
            text_keywords=_keywords(data.get(opt.CONF_TEXT_KEYWORDS)),
            exclude_keywords=_keywords(data.get(opt.CONF_EXCLUDE_KEYWORDS)),
            min_severity=parse_min_severity(data.get(opt.CONF_MIN_SEVERITY)),
        )


@dataclass(frozen=True, kw_only=True)
class Options:
    """Typed view of ``entry.options`` plus the readiness subentries.

    Build it with :meth:`from_mapping`; missing keys get the defaults of
    :mod:`.options` (durations as :class:`~datetime.timedelta`, times as
    :class:`~datetime.time`).
    """

    actuator: str
    max_flow: float
    actuator_unit: str
    select_mapping: Mapping[str, float]
    off_entity: str | None
    off_state: str
    actuator_min: float | None
    actuator_max: float | None
    co2_sensors: tuple[str, ...]
    humidity_sensors: tuple[str, ...]
    presence_entities: tuple[str, ...]
    co2_start: float
    co2_end: float
    min_flow: float
    fallback_flow: float
    humidity_high_start: float
    humidity_high_end: float
    dryness_threshold: float
    dryness_hysteresis: float
    dryness_humidity_high_limit: timedelta
    night_enabled: bool
    night_start: time
    night_end: time
    quiet_max_flow: float
    quiet_humidity_override: float
    away_enabled: bool
    min_write_interval: timedelta
    max_step_up: float
    max_step_down: float
    deadband_up: float
    deadband_down: float
    flow_quantum: float
    stale_timeout: timedelta
    startup_grace: timedelta
    problem_delay: timedelta
    boost_default_duration: timedelta
    boost_default_level: float
    boost_max_duration: timedelta
    restart_confirm_timeout: timedelta
    safety_when_disabled: bool
    pause_reminder_after: timedelta
    off_notice_after: timedelta
    readiness_conditions: tuple[ReadinessCondition, ...] = ()
    warning_sources: tuple[WarningSource, ...] = ()

    @property
    def actuator_domain(self) -> str:
        """Domain of the actuator entity (``number``, ``fan`` or ``select``)."""
        return entity_domain(self.actuator)

    @property
    def off_domain(self) -> str | None:
        """Domain of the off actuator, or None if none is configured."""
        return entity_domain(self.off_entity) if self.off_entity else None

    @classmethod
    def from_mapping(
        cls,
        data: Mapping[str, Any],
        readiness_conditions: Sequence[ReadinessCondition] = (),
        warning_sources: Sequence[WarningSource] = (),
    ) -> Options:
        """Build the options from the stored ``entry.options`` dict.

        ``actuator`` and ``max_flow`` are required; every other key falls back
        to its default (relative defaults are calculated from ``max_flow``).
        """
        max_flow = float(data[opt.CONF_MAX_FLOW])
        values: dict[str, Any] = {
            **opt.DEFAULTS,
            **opt.defaults_for_max_flow(max_flow),
            **{key: value for key, value in data.items() if value is not None},
        }
        mapping = values.get(opt.CONF_SELECT_MAPPING) or {}
        off_entity = values.get(opt.CONF_OFF_ENTITY) or None
        return cls(
            actuator=str(values[opt.CONF_ACTUATOR]),
            max_flow=max_flow,
            actuator_unit=str(
                values.get(opt.CONF_ACTUATOR_UNIT, opt.ACTUATOR_UNIT_FLOW)
            ),
            select_mapping={str(k): float(v) for k, v in dict(mapping).items()},
            off_entity=str(off_entity) if off_entity else None,
            off_state=str(values[opt.CONF_OFF_STATE]),
            actuator_min=_optional_float(values.get(opt.CONF_ACTUATOR_MIN)),
            actuator_max=_optional_float(values.get(opt.CONF_ACTUATOR_MAX)),
            co2_sensors=_entity_list(values.get(opt.CONF_CO2_SENSORS)),
            humidity_sensors=_entity_list(values.get(opt.CONF_HUMIDITY_SENSORS)),
            presence_entities=_entity_list(values.get(opt.CONF_PRESENCE_ENTITIES)),
            co2_start=float(values[opt.CONF_CO2_START]),
            co2_end=float(values[opt.CONF_CO2_END]),
            min_flow=float(values[opt.CONF_MIN_FLOW]),
            fallback_flow=float(values[opt.CONF_FALLBACK_FLOW]),
            humidity_high_start=float(values[opt.CONF_HUMIDITY_HIGH_START]),
            humidity_high_end=float(values[opt.CONF_HUMIDITY_HIGH_END]),
            dryness_threshold=float(values[opt.CONF_DRYNESS_THRESHOLD]),
            dryness_hysteresis=float(values[opt.CONF_DRYNESS_HYSTERESIS]),
            dryness_humidity_high_limit=_seconds(
                values[opt.CONF_DRYNESS_HUMIDITY_HIGH_LIMIT]
            ),
            night_enabled=bool(values[opt.CONF_NIGHT_ENABLED]),
            night_start=_time(values[opt.CONF_NIGHT_START]),
            night_end=_time(values[opt.CONF_NIGHT_END]),
            quiet_max_flow=float(values[opt.CONF_QUIET_MAX_FLOW]),
            quiet_humidity_override=float(values[opt.CONF_QUIET_HUMIDITY_OVERRIDE]),
            away_enabled=bool(values[opt.CONF_AWAY_ENABLED]),
            min_write_interval=_seconds(values[opt.CONF_MIN_WRITE_INTERVAL]),
            max_step_up=float(values[opt.CONF_MAX_STEP_UP]),
            max_step_down=float(values[opt.CONF_MAX_STEP_DOWN]),
            deadband_up=float(values[opt.CONF_DEADBAND_UP]),
            deadband_down=float(values[opt.CONF_DEADBAND_DOWN]),
            flow_quantum=float(values[opt.CONF_FLOW_QUANTUM]),
            stale_timeout=_seconds(values[opt.CONF_STALE_TIMEOUT]),
            startup_grace=_seconds(values[opt.CONF_STARTUP_GRACE]),
            problem_delay=_seconds(values[opt.CONF_PROBLEM_DELAY]),
            boost_default_duration=_seconds(values[opt.CONF_BOOST_DEFAULT_DURATION]),
            boost_default_level=float(values[opt.CONF_BOOST_DEFAULT_LEVEL]),
            boost_max_duration=_seconds(values[opt.CONF_BOOST_MAX_DURATION]),
            restart_confirm_timeout=_seconds(values[opt.CONF_RESTART_CONFIRM_TIMEOUT]),
            safety_when_disabled=bool(values[opt.CONF_SAFETY_WHEN_DISABLED]),
            pause_reminder_after=_seconds(values[opt.CONF_PAUSE_REMINDER_AFTER]),
            off_notice_after=_seconds(values[opt.CONF_OFF_NOTICE_AFTER]),
            readiness_conditions=tuple(readiness_conditions),
            warning_sources=tuple(warning_sources),
        )


# --- Snapshot ----------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class InputValue:
    """State of one entity as seen by the glue.

    ``state`` is None if the entity does not exist. ``last_reported`` is the
    HA ``State.last_reported``; None means "unknown age" and is not treated as
    stale.
    """

    state: str | None
    last_reported: datetime | None = None


@dataclass(frozen=True, kw_only=True)
class ActuatorSnapshot:
    """State and attributes of the actuator needed for the mapping.

    - ``number``: ``state`` is the value; ``step``, ``min_value``, ``max_value``
      are the entity attributes (``actuator_min``/``actuator_max`` options win).
    - ``fan``: ``state`` is ``on``/``off``; ``percentage`` and ``step``
      (= ``percentage_step``) are the attributes.
    - ``select``: ``state`` is the current option.

    ``foreign_change`` is True if the last state change was not the
    integration's own (neither its context nor a late confirmation of its last
    command, docs/spec/02-control.md#writing; used for an external stop/start
    of a ``fan`` actuator without off actuator).
    """

    state: str | None
    foreign_change: bool = False
    step: float | None = None
    min_value: float | None = None
    max_value: float | None = None
    percentage: float | None = None


@dataclass(frozen=True, kw_only=True)
class OffActuatorSnapshot:
    """State of the off actuator and whether its last change was external.

    ``foreign_change`` follows the same rule as for the actuator.
    """

    state: str | None
    foreign_change: bool = False


@dataclass(frozen=True, kw_only=True)
class WarningDetails:
    """One warning behind an active source, as delivered by a details provider.

    Text fields are plain text (providers build them with
    :func:`.safety.make_details`, which strips HTML and normalises the
    severity). ``key`` is the provider's identity of the warning: the NINA or
    CAP ``id``, ``headline + ":" + start`` for DWD, None if unknown.
    """

    key: str | None = None
    headline: str | None = None
    text: str | None = None
    actions: str | None = None
    severity: Severity | None = None
    sender: str | None = None


@dataclass(frozen=True, kw_only=True)
class Snapshot:
    """Everything one evaluation needs from the outside world.

    ``now`` must be timezone-aware; the night window is evaluated on the wall
    clock of ``now``'s time zone, so the glue passes local time
    (``dt_util.now()``). ``started_at`` is when the controller started (start
    of the startup grace). ``states`` holds every configured input and
    readiness entity by entity id; a configured entity missing from it counts
    as not existing. ``reported_problems`` are problems detected by the glue
    (``write_failed``, ``write_mismatch``); the glue keeps reporting them while
    they persist.

    Warning sources (0.2): the states of their entities are in ``states``.
    ``warning_details`` holds, per active source entity, the warnings its
    details provider returned; a missing entry, None or an empty tuple means
    "no details" and the entity counts as one warning without details
    (fail-safe). ``details_failed`` lists the entities whose details call
    failed (problem ``warning_details_failed``; they count without details).

    ``missing_entities`` are configured entities (or warning source devices)
    the glue found neither in the state machine nor in the registry (0.2,
    BHV-55); each raises the problem ``missing_entity``.
    """

    now: datetime
    started_at: datetime
    states: Mapping[str, InputValue]
    actuator: ActuatorSnapshot
    off_actuator: OffActuatorSnapshot | None = None
    reported_problems: frozenset[ProblemKey] = frozenset()
    warning_details: Mapping[str, tuple[WarningDetails, ...] | None] = field(
        default_factory=dict
    )
    details_failed: frozenset[str] = frozenset()
    missing_entities: frozenset[str] = frozenset()


# --- Controller state --------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class Boost:
    """Manual boost in the ``manual`` slot (BHV-20)."""

    until: datetime
    level: float
    started: datetime
    source: Source


@dataclass(frozen=True, kw_only=True)
class Pause:
    """Manual pause in the ``manual`` slot; ``until`` None = indefinite (BHV-21)."""

    until: datetime | None
    started: datetime
    source: Source


@dataclass(frozen=True, kw_only=True)
class ReadinessTracker:
    """Correction bookkeeping of one unmet readiness condition (runtime only)."""

    attempts: int = 0
    last_attempt: datetime | None = None


type ProblemId = tuple[ProblemKey, str | None]


@dataclass(frozen=True, kw_only=True)
class WarningInfo:
    """An active warning in ``safety.active`` (03-modes.md#controller-state).

    ``details`` is False for a warning without details (provider ``none``, a
    failed details call, a smoke detector); such a warning always counts.
    """

    source_id: str
    source_name: str
    entity_id: str
    incident_key: str
    headline: str | None
    text: str | None
    recommended_actions: str | None
    severity: Severity | None
    sender: str | None
    since: datetime
    details: bool = True


@dataclass(frozen=True, kw_only=True)
class RestartPending:
    """The restart wait after the last warning ended (BHV-43)."""

    deadline: datetime


@dataclass(frozen=True, kw_only=True)
class SafetyState:
    """Safety part of the controller state (BHV-40, BHV-42, BHV-43).

    ``active`` maps warning keys to the warnings currently active;
    ``overridden`` holds the incident keys lifted by the user.
    """

    active: Mapping[str, WarningInfo] = field(default_factory=dict)
    overridden: frozenset[str] = frozenset()
    restart_pending: RestartPending | None = None

    @property
    def blocking(self) -> dict[str, WarningInfo]:
        """Active warnings whose incident is not overridden."""
        return {
            key: warning
            for key, warning in self.active.items()
            if warning.incident_key not in self.overridden
        }

    @property
    def shuts_down(self) -> bool:
        """True if safety asks for ``safety_off`` (blocking warning or restart wait)."""
        return bool(self.blocking) or self.restart_pending is not None


@dataclass(frozen=True, kw_only=True)
class SafetyRestore:
    """How to restore the unit after a safety off while control is disabled.

    ``safety_when_disabled`` (03-modes.md#mode-resolution): the integration
    switches the unit off and, after the restart confirmation, back on to the
    state it had before. ``turn_on``: switch the off actuator to running
    again; ``flow``: write this flow again (off as lowest value).
    """

    turn_on: bool = False
    flow: float | None = None


@dataclass(frozen=True, kw_only=True)
class ControllerState:
    """State of the controller (docs/spec/03-modes.md#controller-state).

    The first group is persisted (:mod:`.serialize`); the second group is
    runtime bookkeeping that starts fresh after every load.
    """

    # persisted
    control_enabled: bool = True
    profile: Profile = Profile.BALANCED
    manual: Boost | Pause | None = None
    safety: SafetyState = field(default_factory=SafetyState)
    safety_restore: SafetyRestore | None = None
    dry_active: bool = False
    dry_humidity_high_since: datetime | None = None
    off_since: datetime | None = None
    last_reminder: datetime | None = None
    stopped_by_integration: bool = False
    last_write: datetime | None = None
    last_written_flow: float | None = None
    last_mode: Mode | None = None
    # runtime only
    started: bool = False
    written_since_start: bool = False
    bypass_pending: bool = False
    pending_trigger: Trigger | None = None
    pending_source: Source | None = None
    unit_running_seen: bool | None = None
    readiness: Mapping[str, ReadinessTracker] = field(default_factory=dict)
    problem_since: Mapping[ProblemId, datetime] = field(default_factory=dict)
    #: Keys of warnings loaded from the store that no source has reported
    #: again yet; they count until the startup grace ends
    #: (03-modes.md#persistence).
    safety_unconfirmed: frozenset[str] = frozenset()
    #: The restart wait ended by its timeout; the next evaluation sends
    #: "restarted".
    restarted_pending: bool = False


# --- Decision ----------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class Contributions:
    """Flow each rule asks for (raw, before quantisation), None if not applicable."""

    minimum: float
    co2: float | None = None
    humidity_high: float | None = None
    fallback: float | None = None
    boost: float | None = None
    shower: float | None = None
    cooking: float | None = None


@dataclass(frozen=True, kw_only=True)
class InputSummary:
    """Aggregated valid inputs (``inputs`` of the decision record)."""

    co2_max: float | None = None
    co2_max_entity: str | None = None
    h_max: float | None = None
    h_max_entity: str | None = None
    h_mean: float | None = None
    presence_home: bool | None = None


@dataclass(frozen=True, kw_only=True)
class Problem:
    """A current problem; it counts for the problem sensor after ``problem_delay``."""

    key: ProblemKey
    since: datetime
    entity_id: str | None = None


@dataclass(frozen=True, kw_only=True)
class Decision:
    """Result of one evaluation (docs/spec/02-control.md#decision-record).

    ``setpoint`` is the flow after shaping (0 in off modes, the demand in
    ``disabled``), ``target`` the flow before shaping, ``written`` the flow
    sent to the actuator in this evaluation (None if nothing was written).
    ``problem`` is True if any problem persists longer than ``problem_delay``.

    Safety (0.2): ``warnings`` are all active warnings (overridden or not),
    ``overridden_incidents`` the overridden incident keys, ``awaiting_restart``
    is True while ``safety_off`` is caused only by the restart wait.
    """

    time: datetime
    mode: Mode
    profile: Profile
    reason: Reason
    setpoint: float
    target: float
    written: float | None
    contributions: Contributions
    inputs: InputSummary
    limits: tuple[Limit, ...]
    invalid_inputs: tuple[str, ...]
    problems: tuple[Problem, ...]
    problem: bool
    ends_at: datetime | None
    write_blocked_by: WriteBlock | None
    off_method: OffMethod | None
    actuator_value: float | str | None
    last_write: datetime | None
    last_written_flow: float | None
    warnings: tuple[WarningInfo, ...] = ()
    overridden_incidents: frozenset[str] = frozenset()
    awaiting_restart: bool = False


# --- Effects -----------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class WriteFlow:
    """Write a flow to the actuator; ``native`` is the mapped actuator value."""

    flow: float
    native: float | str
    bypass: bool = False


@dataclass(frozen=True, kw_only=True)
class TurnOff:
    """Switch the off actuator to its stopped state."""

    entity_id: str


@dataclass(frozen=True, kw_only=True)
class TurnOn:
    """Switch the off actuator to its running state."""

    entity_id: str


@dataclass(frozen=True, kw_only=True)
class CorrectReadiness:
    """Set a readiness entity to its expected state (BHV-04).

    The glue issues the service call and, on success, fires
    ``better_home_ventilation_readiness_corrected`` with ``from_state`` and
    ``to_state``.
    """

    entity_id: str
    to_state: str
    from_state: str | None


@dataclass(frozen=True, kw_only=True)
class FireEvent:
    """Fire an event; the glue adds ``entry_id``, ``device_id`` and ``name``."""

    event_type: str
    data: Mapping[str, Any]


@dataclass(frozen=True, kw_only=True)
class ScheduleAt:
    """Run an evaluation at ``when`` (replaces any earlier scheduled time)."""

    when: datetime


@dataclass(frozen=True, kw_only=True)
class Notify:
    """Send a notification (04-interface.md#notifications); the glue formats it.

    - ``safety``: ``warnings`` are the blocking warnings, most severe and
      newest first (the title uses the first one).
    - ``restart``: ``restart_at`` is the deadline of the restart wait.
    - ``restarted``: no data.
    - ``reminder``: ``off_since`` is the start of the off state.

    The engine always emits them; the glue sends nothing if the matching
    ``notify_*`` option is off.
    """

    kind: NotifyKind
    warnings: tuple[WarningInfo, ...] = ()
    restart_at: datetime | None = None
    off_since: datetime | None = None

    @property
    def tag(self) -> NotificationTag:
        """Tag of the notification; a new one replaces the last with the same tag."""
        return NOTIFY_TAGS[self.kind]


@dataclass(frozen=True, kw_only=True)
class ClearNotification:
    """Clear the notification with this tag (its reason disappeared)."""

    tag: NotificationTag


type Effect = (
    WriteFlow
    | TurnOff
    | TurnOn
    | CorrectReadiness
    | FireEvent
    | ScheduleAt
    | Notify
    | ClearNotification
)


# --- Commands ----------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class BoostCommand:
    """``boost(duration?, level?)``; defaults come from the parameter entities.

    ``default_duration``/``default_level`` are the values of the
    ``boost_duration``/``boost_level`` entities; if None, the options
    ``boost_default_duration``/``boost_default_level`` are used.
    """

    source: Source
    duration: timedelta | None = None
    level: float | None = None
    default_duration: timedelta | None = None
    default_level: float | None = None


@dataclass(frozen=True, kw_only=True)
class PauseCommand:
    """``pause(duration?)``; no duration = indefinite."""

    source: Source
    duration: timedelta | None = None


@dataclass(frozen=True, kw_only=True)
class ResumeCommand:
    """``resume()``: end boost or pause."""

    source: Source


@dataclass(frozen=True, kw_only=True)
class ControlSwitchCommand:
    """Control switch on/off (BHV-03)."""

    source: Source
    enabled: bool


@dataclass(frozen=True, kw_only=True)
class ProfileCommand:
    """Select a profile (BHV-26); accepted while control is disabled."""

    source: Source
    profile: Profile


@dataclass(frozen=True, kw_only=True)
class OverrideSafetyCommand:
    """``override_safety()``: lift the shutdown for the current warnings (BHV-42)."""

    source: Source


@dataclass(frozen=True, kw_only=True)
class RemoveOverrideCommand:
    """Remove all overrides (``safety_override`` switch turned off)."""

    source: Source


@dataclass(frozen=True, kw_only=True)
class KeepOffCommand:
    """``keep_off()``: the answer "Keep off" to the restart question (BHV-43)."""

    source: Source


type Command = (
    BoostCommand
    | PauseCommand
    | ResumeCommand
    | ControlSwitchCommand
    | ProfileCommand
    | OverrideSafetyCommand
    | RemoveOverrideCommand
    | KeepOffCommand
)


class CommandRejected(Exception):  # noqa: N818 - a rejection, not an error
    """A command is not allowed in the current state.

    ``code`` is the translation key the glue uses for its
    ``ServiceValidationError`` (``control_disabled``, ``level_out_of_range``,
    ``no_active_warning``).
    """

    def __init__(self, code: str) -> None:
        """Store the rejection code."""
        super().__init__(code)
        self.code = code
