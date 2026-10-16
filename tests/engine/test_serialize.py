"""Persistence format of the controller state (BHV-50, 03-modes.md#persistence)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest

from engine.models import (
    Boost,
    ControllerState,
    FireEvent,
    Mode,
    Pause,
    Profile,
    Source,
    Trigger,
)
from engine.serialize import STORE_VERSION, state_from_dict, state_to_dict

from .helpers import T0, Sim, of_type


def _persisted(**overrides: Any) -> ControllerState:
    values: dict[str, Any] = {
        "control_enabled": False,
        "profile": Profile.WINTER,
        "manual": Boost(
            until=T0 + timedelta(minutes=30),
            level=300,
            started=T0,
            source=Source.BUTTON,
        ),
        "dry_active": True,
        "dry_humidity_high_since": T0 - timedelta(minutes=5),
        "stopped_by_integration": True,
        "last_write": T0 - timedelta(minutes=1),
        "last_written_flow": 250.0,
        "last_mode": Mode.BOOST,
    }
    values.update(overrides)
    return ControllerState(**values)


def test_e38_bhv50_round_trip_is_identical() -> None:
    """E38: serialise -> deserialise gives the same persisted state."""
    state = _persisted()
    data = state_to_dict(state)
    assert data["manual"]["until"] == "2026-01-15T12:30:00+00:00"
    result = state_from_dict(data, T0)
    assert result.state == state
    assert not result.reset
    assert result.expired is None


def test_e38_round_trip_indefinite_pause_and_local_time() -> None:
    """Indefinite pause and datetimes in another zone (stored as UTC)."""
    local = T0.astimezone(timezone(timedelta(hours=1)))
    state = _persisted(
        manual=Pause(until=None, started=local, source=Source.EXTERNAL),
        last_write=None,
        last_written_flow=None,
        last_mode=None,
    )
    data = state_to_dict(state)
    assert data["manual"]["started"].endswith("+00:00")
    assert state_from_dict(data, T0).state == state
    empty = state_to_dict(ControllerState())
    assert state_from_dict(empty, T0).state == ControllerState()


def test_e38_expired_boost_ends_with_timeout_while_offline() -> None:
    """E38: an expired boost ends on load; the first evaluation reports it."""
    data = state_to_dict(_persisted(control_enabled=True))
    result = state_from_dict(data, T0 + timedelta(hours=1))
    assert result.state.manual is None
    assert result.expired == "boost"
    assert result.state.pending_trigger is Trigger.TIMEOUT_WHILE_OFFLINE

    sim = Sim(now=T0 + timedelta(hours=1), state=result.state)
    decision, effects = sim.run()
    assert decision.mode is Mode.AUTOMATIC
    assert of_type(effects, FireEvent)[0].data["trigger"] == "timeout_while_offline"


def test_expired_pause() -> None:
    """A timed pause that ended while offline is ended as well."""
    pause = Pause(until=T0, started=T0 - timedelta(hours=1), source=Source.SERVICE)
    result = state_from_dict(state_to_dict(_persisted(manual=pause)), T0)
    assert result.state.manual is None
    assert result.expired == "pause"


def test_missing_store_is_defaults_without_reset() -> None:
    """First start: defaults, no repair."""
    result = state_from_dict(None, T0)
    assert result.state == ControllerState()
    assert not result.reset


def test_missing_keys_get_defaults() -> None:
    """Missing keys (older minor version) get their defaults."""
    result = state_from_dict({"profile": "quiet"}, T0)
    assert result.state == ControllerState(profile=Profile.QUIET)
    assert not result.reset


@pytest.mark.parametrize(
    "data",
    [
        "garbage",
        [1, 2],
        {"profile": "bogus"},
        {"control_enabled": "yes"},
        {"last_written_flow": True},
        {"last_written_flow": "80"},
        {"last_write": 12},
        {"last_write": "2026-01-15T12:00:00"},
        {"last_write": "not a date"},
        {"last_mode": "nonsense"},
        {"manual": "boost"},
        {
            "manual": {
                "type": "turbo",
                "source": "button",
                "started": "2026-01-15T12:00:00+00:00",
            }
        },
        {
            "manual": {
                "type": "boost",
                "source": "button",
                "started": "2026-01-15T12:00:00+00:00",
            }
        },
        {
            "manual": {
                "type": "boost",
                "source": "button",
                "level": 300,
                "until": None,
                "started": "2026-01-15T12:00:00+00:00",
            }
        },
        {"manual": {"type": "pause", "source": "button"}},
        {"manual": {"type": "pause", "source": "nobody", "started": None}},
    ],
)
def test_corrupt_store_resets(data: object) -> None:
    """Corrupt data -> defaults and the ``state_reset`` flag."""
    result = state_from_dict(data, T0)
    assert result.state == ControllerState()
    assert result.reset


def test_future_version_resets() -> None:
    """An unknown future version -> defaults and the ``state_reset`` flag."""
    data = state_to_dict(_persisted())
    result = state_from_dict(data, T0, version=STORE_VERSION + 1)
    assert result.reset
    assert result.state == ControllerState()


def test_runtime_fields_are_not_persisted() -> None:
    """Runtime bookkeeping starts fresh after a load."""
    state = _persisted()
    data = state_to_dict(state)
    assert set(data) == {
        "control_enabled",
        "profile",
        "manual",
        "dry_active",
        "dry_humidity_high_since",
        "stopped_by_integration",
        "last_write",
        "last_written_flow",
        "last_mode",
    }
    assert datetime.fromisoformat(data["last_write"]).tzinfo == UTC
