"""Readiness conditions and their auto-correction (BHV-04).

docs/spec/02-control.md#readiness-and-availability: conditions are checked in
every evaluation. An unmet condition with ``auto_correct`` is corrected at most
once per 60 s, re-evaluated after 5 s; after 3 failed attempts it is a problem
``not_ready`` and retried every 5 min. No correction while control is disabled
or during the startup grace.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from .models import (
    CorrectReadiness,
    InputValue,
    ReadinessCondition,
    ReadinessTracker,
    entity_domain,
    is_unavailable,
)
from .validity import parse_float

#: Tolerance of the numeric compare for ``number`` entities.
NUMERIC_TOLERANCE: Final = 0.5
#: Minimum time between two corrections of one condition.
CORRECTION_INTERVAL: Final = timedelta(seconds=60)
#: Re-evaluation delay after a correction.
REEVALUATE_AFTER: Final = timedelta(seconds=5)
#: Attempts before the condition is reported as ``not_ready``.
MAX_ATTEMPTS: Final = 3
#: Retry interval after ``MAX_ATTEMPTS`` failed attempts.
RETRY_INTERVAL: Final = timedelta(minutes=5)
#: Domains a correction can be issued for.
CORRECTABLE_DOMAINS: Final = frozenset(
    {"select", "input_select", "switch", "input_boolean", "number"}
)


def condition_met(condition: ReadinessCondition, value: InputValue | None) -> bool:
    """Compare the entity state with the expected state.

    String compare; for ``number`` entities a numeric compare with a tolerance
    of 0.5. A missing or unavailable entity never meets a condition.
    """
    if value is None or value.state is None or is_unavailable(value.state):
        return False
    if entity_domain(condition.entity_id) == "number":
        actual = parse_float(value.state)
        expected = parse_float(condition.expected_state)
        if actual is not None and expected is not None:
            return abs(actual - expected) <= NUMERIC_TOLERANCE
    return value.state == condition.expected_state


@dataclass(frozen=True, kw_only=True)
class ReadinessResult:
    """Outcome of the readiness check of one evaluation."""

    ready: bool
    trackers: Mapping[str, ReadinessTracker]
    corrections: tuple[CorrectReadiness, ...]
    not_ready: tuple[str, ...]
    restored: bool
    wake_at: tuple[datetime, ...]


def check_readiness(
    conditions: Sequence[ReadinessCondition],
    states: Mapping[str, InputValue],
    trackers: Mapping[str, ReadinessTracker],
    *,
    now: datetime,
    may_correct: bool,
) -> ReadinessResult:
    """Check all conditions, issue due corrections and report problems.

    ``restored`` is True if a condition that had been corrected is met again;
    the next write then uses bypass (the unit may have run at another flow).

    SPEC-GAP: an unavailable readiness entity is not met and not corrected
    (there is nothing to correct); it is reported as ``not_ready`` at once.
    A condition that cannot be corrected right now (no ``auto_correct``,
    control disabled, startup grace) is also reported as ``not_ready`` at once;
    the problem sensor still waits ``problem_delay``.
    """
    ready = True
    restored = False
    new_trackers: dict[str, ReadinessTracker] = {}
    corrections: list[CorrectReadiness] = []
    not_ready: list[str] = []
    wake_at: list[datetime] = []

    for condition in conditions:
        value = states.get(condition.entity_id)
        tracker = trackers.get(condition.entity_id, ReadinessTracker())
        if condition_met(condition, value):
            restored = restored or tracker.attempts > 0
            continue
        ready = False
        state = value.state if value is not None else None
        correctable = (
            condition.auto_correct
            and entity_domain(condition.entity_id) in CORRECTABLE_DOMAINS
            and not is_unavailable(state)
        )
        if not (correctable and may_correct):
            not_ready.append(condition.entity_id)
            if tracker.attempts:
                new_trackers[condition.entity_id] = tracker
            continue
        if tracker.attempts >= MAX_ATTEMPTS:
            not_ready.append(condition.entity_id)
        interval = (
            CORRECTION_INTERVAL if tracker.attempts < MAX_ATTEMPTS else RETRY_INTERVAL
        )
        if tracker.last_attempt is None or now - tracker.last_attempt >= interval:
            corrections.append(
                CorrectReadiness(
                    entity_id=condition.entity_id,
                    to_state=condition.expected_state,
                    from_state=state,
                )
            )
            tracker = ReadinessTracker(attempts=tracker.attempts + 1, last_attempt=now)
            wake_at.append(now + REEVALUATE_AFTER)
        else:
            wake_at.append(tracker.last_attempt + interval)
        new_trackers[condition.entity_id] = tracker

    return ReadinessResult(
        ready=ready,
        trackers=new_trackers,
        corrections=tuple(corrections),
        not_ready=tuple(not_ready),
        restored=restored,
        wake_at=tuple(wake_at),
    )
