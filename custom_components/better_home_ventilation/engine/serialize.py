"""ControllerState <-> dict for the store (BHV-50, docs/spec/03-modes.md#persistence).

Only the persisted fields of :class:`~.models.ControllerState` are stored;
runtime bookkeeping starts fresh after a load. Datetimes are ISO strings in
UTC.

Versions: 1.1 = phase 0.1; 1.2 = phase 0.2 (safety, ``safety_restore``,
``off_since``, ``last_reminder``); 1.3 = phase 0.3 (``auto_boosts``,
``detector_cooldown``). The minor bumps are backwards compatible: an older
version reads newer data and ignores the new keys.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Final

from .models import (
    AutoBoost,
    AutoBoostKind,
    Boost,
    ControllerState,
    Mode,
    Pause,
    Profile,
    RestartPending,
    SafetyRestore,
    SafetyState,
    Severity,
    Source,
    Trigger,
    WarningInfo,
)

#: Store version (``Store(version=...)``); an incompatible change bumps it.
STORE_VERSION: Final = 1
#: Store minor version (backwards compatible changes, see :func:`migrate_store_data`).
STORE_MINOR_VERSION: Final = 3


#: Store minor version of phase 0.2 (safety keys, ``off_since``).
_MINOR_SAFETY: Final = 2


class _CorruptError(ValueError):
    """The stored data cannot be read."""


def _dt_to_str(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value is not None else None


def _dt_from(value: Any) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise _CorruptError("datetime")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise _CorruptError("naive datetime")
    return parsed.astimezone(UTC)


def _required_dt(value: Any) -> datetime:
    parsed = _dt_from(value)
    if parsed is None:
        raise _CorruptError("missing datetime")
    return parsed


def _bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise _CorruptError("bool")
    return value


def _float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise _CorruptError("number")
    return float(value)


def _str(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise _CorruptError("string")
    return value


def _required_str(value: Any) -> str:
    text = _str(value)
    if text is None:
        raise _CorruptError("missing string")
    return text


def _warning_to_dict(warning: WarningInfo) -> dict[str, Any]:
    return {
        "source_id": warning.source_id,
        "source_name": warning.source_name,
        "entity_id": warning.entity_id,
        "incident_key": warning.incident_key,
        "headline": warning.headline,
        "text": warning.text,
        "recommended_actions": warning.recommended_actions,
        "severity": str(warning.severity) if warning.severity is not None else None,
        "sender": warning.sender,
        "since": _dt_to_str(warning.since),
        "details": warning.details,
    }


def _safety_to_dict(safety: SafetyState) -> dict[str, Any]:
    restart = safety.restart_pending
    return {
        "active": {
            key: _warning_to_dict(warning) for key, warning in safety.active.items()
        },
        "overridden": sorted(safety.overridden),
        "restart_pending": (
            {"deadline": _dt_to_str(restart.deadline)} if restart is not None else None
        ),
    }


def _auto_boost_to_dict(boost: AutoBoost) -> dict[str, Any]:
    return {
        "kind": str(boost.kind),
        "entity_id": boost.entity_id,
        "baseline": boost.baseline,
        "started": _dt_to_str(boost.started),
        "deadline": _dt_to_str(boost.deadline),
    }


def state_to_dict(state: ControllerState) -> dict[str, Any]:
    """Serialise the persisted fields of the controller state."""
    manual: dict[str, Any] | None = None
    if isinstance(state.manual, Boost):
        manual = {
            "type": "boost",
            "until": _dt_to_str(state.manual.until),
            "level": state.manual.level,
            "started": _dt_to_str(state.manual.started),
            "source": str(state.manual.source),
        }
    elif isinstance(state.manual, Pause):
        manual = {
            "type": "pause",
            "until": _dt_to_str(state.manual.until),
            "started": _dt_to_str(state.manual.started),
            "source": str(state.manual.source),
        }
    restore = state.safety_restore
    return {
        "control_enabled": state.control_enabled,
        "profile": str(state.profile),
        "manual": manual,
        "safety": _safety_to_dict(state.safety),
        "safety_restore": (
            {"turn_on": restore.turn_on, "flow": restore.flow}
            if restore is not None
            else None
        ),
        "auto_boosts": {
            key: _auto_boost_to_dict(boost)
            for key, boost in sorted(state.auto_boosts.items())
        },
        "detector_cooldown": {
            key: _dt_to_str(until)
            for key, until in sorted(state.detector_cooldown.items())
        },
        "dry_active": state.dry_active,
        "dry_humidity_high_since": _dt_to_str(state.dry_humidity_high_since),
        "off_since": _dt_to_str(state.off_since),
        "last_reminder": _dt_to_str(state.last_reminder),
        "stopped_by_integration": state.stopped_by_integration,
        "last_write": _dt_to_str(state.last_write),
        "last_written_flow": state.last_written_flow,
        "last_mode": str(state.last_mode) if state.last_mode is not None else None,
    }


def migrate_store_data(major: int, minor: int, data: Any) -> Any:
    """Migrate stored data from version ``major.minor`` to the current version.

    Called by the glue's ``Store._async_migrate_func``. 1.1 -> 1.2 adds the
    safety keys and sets ``off_since`` of a running pause to its start, so the
    reminder of a pause that began under 0.1 counts from its real start.
    1.2 -> 1.3 adds the empty ``auto_boosts`` and ``detector_cooldown``.
    Data of a newer minor version (a downgrade) is returned unchanged; data
    that is not a mapping is left to :func:`state_from_dict` (reset).
    """
    if major != STORE_VERSION or minor >= STORE_MINOR_VERSION:
        return data
    if not isinstance(data, Mapping):
        return data
    result = dict(data)
    if minor < _MINOR_SAFETY:
        manual = result.get("manual")
        if (
            result.get("off_since") is None
            and isinstance(manual, Mapping)
            and manual.get("type") == "pause"
        ):
            result["off_since"] = manual.get("started")
        result.setdefault(
            "safety", {"active": {}, "overridden": [], "restart_pending": None}
        )
        result.setdefault("safety_restore", None)
        result.setdefault("off_since", None)
        result.setdefault("last_reminder", None)
    # 1.2 -> 1.3
    result.setdefault("auto_boosts", {})
    result.setdefault("detector_cooldown", {})
    return result


@dataclass(frozen=True, kw_only=True)
class LoadResult:
    """Loaded state; ``reset`` asks the glue for the ``state_reset`` repair.

    ``expired`` names what ended while Home Assistant was offline (``boost``,
    ``pause``, ``auto boost``, ``restart wait``, joined with " and "), for one
    log message.
    """

    state: ControllerState
    reset: bool = False
    expired: str | None = None


def _manual(data: Any) -> Boost | Pause | None:
    if data is None:
        return None
    if not isinstance(data, Mapping):
        raise _CorruptError("manual")
    kind = data.get("type")
    source = Source(data["source"])
    started = _required_dt(data.get("started"))
    if kind == "boost":
        level = _float(data.get("level"))
        if level is None:
            raise _CorruptError("boost level")
        return Boost(
            until=_required_dt(data.get("until")),
            level=level,
            started=started,
            source=source,
        )
    if kind == "pause":
        return Pause(until=_dt_from(data.get("until")), started=started, source=source)
    raise _CorruptError("manual type")


def _warning(data: Any) -> WarningInfo:
    if not isinstance(data, Mapping):
        raise _CorruptError("warning")
    severity = _str(data.get("severity"))
    return WarningInfo(
        source_id=_required_str(data.get("source_id")),
        source_name=_str(data.get("source_name")) or "",
        entity_id=_required_str(data.get("entity_id")),
        incident_key=_required_str(data.get("incident_key")),
        headline=_str(data.get("headline")),
        text=_str(data.get("text")),
        recommended_actions=_str(data.get("recommended_actions")),
        severity=Severity(severity) if severity is not None else None,
        sender=_str(data.get("sender")),
        since=_required_dt(data.get("since")),
        details=_bool(data.get("details"), default=True),
    )


def _safety(data: Any) -> SafetyState:
    if data is None:
        return SafetyState()
    if not isinstance(data, Mapping):
        raise _CorruptError("safety")
    active = data.get("active") or {}
    overridden = data.get("overridden") or []
    restart = data.get("restart_pending")
    if not isinstance(active, Mapping) or not isinstance(overridden, list):
        raise _CorruptError("safety content")
    if restart is not None and not isinstance(restart, Mapping):
        raise _CorruptError("restart_pending")
    return SafetyState(
        active={str(key): _warning(value) for key, value in active.items()},
        overridden=frozenset(_required_str(item) for item in overridden),
        restart_pending=(
            RestartPending(deadline=_required_dt(restart.get("deadline")))
            if restart is not None
            else None
        ),
    )


def _restore(data: Any) -> SafetyRestore | None:
    if data is None:
        return None
    if not isinstance(data, Mapping):
        raise _CorruptError("safety_restore")
    return SafetyRestore(
        turn_on=_bool(data.get("turn_on"), default=False),
        flow=_float(data.get("flow")),
    )


def _auto_boost(data: Any) -> AutoBoost:
    if not isinstance(data, Mapping):
        raise _CorruptError("auto boost")
    return AutoBoost(
        kind=AutoBoostKind(_required_str(data.get("kind"))),
        entity_id=_required_str(data.get("entity_id")),
        baseline=_float(data.get("baseline")),
        started=_required_dt(data.get("started")),
        deadline=_required_dt(data.get("deadline")),
    )


def _auto_boosts(data: Any) -> dict[str, AutoBoost]:
    if data is None:
        return {}
    if not isinstance(data, Mapping):
        raise _CorruptError("auto_boosts")
    return {str(key): _auto_boost(value) for key, value in data.items()}


def _cooldowns(data: Any) -> dict[str, datetime]:
    if data is None:
        return {}
    if not isinstance(data, Mapping):
        raise _CorruptError("detector_cooldown")
    return {str(key): _required_dt(value) for key, value in data.items()}


def _state_from(data: Mapping[str, Any]) -> ControllerState:
    last_mode = data.get("last_mode")
    return ControllerState(
        control_enabled=_bool(data.get("control_enabled"), default=True),
        profile=Profile(data.get("profile", Profile.BALANCED)),
        manual=_manual(data.get("manual")),
        safety=_safety(data.get("safety")),
        safety_restore=_restore(data.get("safety_restore")),
        auto_boosts=_auto_boosts(data.get("auto_boosts")),
        detector_cooldown=_cooldowns(data.get("detector_cooldown")),
        dry_active=_bool(data.get("dry_active"), default=False),
        dry_humidity_high_since=_dt_from(data.get("dry_humidity_high_since")),
        off_since=_dt_from(data.get("off_since")),
        last_reminder=_dt_from(data.get("last_reminder")),
        stopped_by_integration=_bool(data.get("stopped_by_integration"), default=False),
        last_write=_dt_from(data.get("last_write")),
        last_written_flow=_float(data.get("last_written_flow")),
        last_mode=Mode(last_mode) if last_mode is not None else None,
    )


def state_from_dict(
    data: object,
    now: datetime,
    *,
    version: int = STORE_VERSION,
) -> LoadResult:
    """Load the controller state from the store.

    - ``data`` None (no store yet): defaults, no reset.
    - corrupt data or a future major ``version``: defaults and ``reset``.
    - an expired boost, timed pause, auto boost or restart wait is ended
      with trigger ``timeout_while_offline`` (an ended restart wait also sends
      "restarted"); an active auto boost keeps its deadline.
    - ``safety.active`` is not trusted (03-modes.md#persistence): every loaded
      warning is unconfirmed and counts only until the startup grace ends,
      unless a source reports it again.

    Missing keys get their defaults (forward compatible minor versions).
    """
    if data is None:
        return LoadResult(state=ControllerState())
    if version > STORE_VERSION or not isinstance(data, Mapping):
        return LoadResult(state=ControllerState(), reset=True)
    try:
        state = _state_from(data)
    except KeyError, TypeError, ValueError:
        return LoadResult(state=ControllerState(), reset=True)

    state = replace(state, safety_unconfirmed=frozenset(state.safety.active))
    expired: list[str] = []
    manual = state.manual
    if manual is not None and manual.until is not None and manual.until <= now:
        expired.append("boost" if isinstance(manual, Boost) else "pause")
        state = replace(
            state, manual=None, pending_trigger=Trigger.TIMEOUT_WHILE_OFFLINE
        )
    if any(boost.deadline <= now for boost in state.auto_boosts.values()):
        expired.append("auto boost")
        state = replace(
            state,
            auto_boosts={
                key: boost
                for key, boost in state.auto_boosts.items()
                if boost.deadline > now
            },
            pending_trigger=Trigger.TIMEOUT_WHILE_OFFLINE,
        )
    restart = state.safety.restart_pending
    if restart is not None and restart.deadline <= now:
        expired.append("restart wait")
        state = replace(
            state,
            safety=replace(state.safety, restart_pending=None),
            pending_trigger=Trigger.TIMEOUT_WHILE_OFFLINE,
            restarted_pending=True,
        )
    return LoadResult(state=state, expired=" and ".join(expired) or None)
