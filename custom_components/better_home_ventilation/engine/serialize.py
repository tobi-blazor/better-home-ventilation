"""ControllerState <-> dict for the store (BHV-50, docs/spec/03-modes.md#persistence).

Only the persisted fields of :class:`~.models.ControllerState` are stored;
runtime bookkeeping starts fresh after a load. Datetimes are ISO strings in
UTC.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Final

from .models import Boost, ControllerState, Mode, Pause, Profile, Source, Trigger

#: Store version (``Store(version=...)``); a format change bumps it.
STORE_VERSION: Final = 1
#: Store minor version (backwards compatible changes).
STORE_MINOR_VERSION: Final = 1


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
    return {
        "control_enabled": state.control_enabled,
        "profile": str(state.profile),
        "manual": manual,
        "dry_active": state.dry_active,
        "dry_humidity_high_since": _dt_to_str(state.dry_humidity_high_since),
        "stopped_by_integration": state.stopped_by_integration,
        "last_write": _dt_to_str(state.last_write),
        "last_written_flow": state.last_written_flow,
        "last_mode": str(state.last_mode) if state.last_mode is not None else None,
    }


@dataclass(frozen=True, kw_only=True)
class LoadResult:
    """Loaded state; ``reset`` asks the glue for the ``state_reset`` repair.

    ``expired`` names the manual command (``boost``/``pause``) that ended while
    Home Assistant was offline, for one log message.
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


def _state_from(data: Mapping[str, Any]) -> ControllerState:
    last_mode = data.get("last_mode")
    return ControllerState(
        control_enabled=_bool(data.get("control_enabled"), default=True),
        profile=Profile(data.get("profile", Profile.BALANCED)),
        manual=_manual(data.get("manual")),
        dry_active=_bool(data.get("dry_active"), default=False),
        dry_humidity_high_since=_dt_from(data.get("dry_humidity_high_since")),
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
    - corrupt data or a future ``version``: defaults and ``reset``.
    - an expired boost or timed pause is ended with trigger
      ``timeout_while_offline``.

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

    manual = state.manual
    if manual is not None and manual.until is not None and manual.until <= now:
        expired = "boost" if isinstance(manual, Boost) else "pause"
        state = replace(
            state, manual=None, pending_trigger=Trigger.TIMEOUT_WHILE_OFFLINE
        )
        return LoadResult(state=state, expired=expired)
    return LoadResult(state=state)
