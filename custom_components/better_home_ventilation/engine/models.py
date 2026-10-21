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

    ``shower``, ``cooking`` and ``safety_off`` belong to phases 0.2/0.3; they
    are listed for the entity options but never resolved by the 0.1 engine.
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
    """Problems known to the 0.1 engine (04-interface.md#problem-attributes).

    ``write_failed`` and ``write_mismatch`` are detected by the glue and passed
    in through :attr:`Snapshot.reported_problems`.
    """

    ACTUATOR_UNAVAILABLE = "actuator_unavailable"
    NOT_READY = "not_ready"
    WRITE_FAILED = "write_failed"
    WRITE_MISMATCH = "write_mismatch"
    INPUTS_INVALID = "inputs_invalid"
    NO_VALID_CO2 = "no_valid_co2"


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
    readiness_conditions: tuple[ReadinessCondition, ...] = ()

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
            readiness_conditions=tuple(readiness_conditions),
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
    """

    now: datetime
    started_at: datetime
    states: Mapping[str, InputValue]
    actuator: ActuatorSnapshot
    off_actuator: OffActuatorSnapshot | None = None
    reported_problems: frozenset[ProblemKey] = frozenset()


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
class ControllerState:
    """State of the controller (docs/spec/03-modes.md#controller-state, 0.1 part).

    The first group is persisted (:mod:`.serialize`); the second group is
    runtime bookkeeping that starts fresh after every load.
    """

    # persisted
    control_enabled: bool = True
    profile: Profile = Profile.BALANCED
    manual: Boost | Pause | None = None
    dry_active: bool = False
    dry_humidity_high_since: datetime | None = None
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


type Effect = WriteFlow | TurnOff | TurnOn | CorrectReadiness | FireEvent | ScheduleAt


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


type Command = (
    BoostCommand | PauseCommand | ResumeCommand | ControlSwitchCommand | ProfileCommand
)


class CommandRejected(Exception):  # noqa: N818 - a rejection, not an error
    """A command is not allowed in the current state.

    ``code`` is the translation key the glue uses for its
    ``ServiceValidationError`` (``control_disabled``, ``level_out_of_range``).
    """

    def __init__(self, code: str) -> None:
        """Store the rejection code."""
        super().__init__(code)
        self.code = code
