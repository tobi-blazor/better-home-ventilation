"""Persistence format of the controller state (BHV-50, 03-modes.md#persistence)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest

from engine.models import (
    AutoBoost,
    AutoBoostKind,
    Boost,
    ControllerState,
    FireEvent,
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
from engine.serialize import (
    STORE_MINOR_VERSION,
    STORE_VERSION,
    migrate_store_data,
    state_from_dict,
    state_to_dict,
)

from .helpers import T0, Sim, of_type

_WARNING = WarningInfo(
    source_id="nina",
    source_name="NINA region",
    entity_id="binary_sensor.warning_region_1",
    incident_key="nina:großbrand - musterstadt",
    headline="Großbrand - Musterstadt",
    text="Text",
    recommended_actions="Fenster schließen",
    severity=Severity.SEVERE,
    sender="Leitstelle",
    since=T0 - timedelta(minutes=10),
)
_SAFETY = SafetyState(
    active={
        "nina:1": _WARNING,
        "smoke:binary_sensor.smoke:t": WarningInfo(
            source_id="smoke",
            source_name="Smoke",
            entity_id="binary_sensor.smoke",
            incident_key="smoke:binary_sensor.smoke:t",
            headline=None,
            text=None,
            recommended_actions=None,
            severity=None,
            sender=None,
            since=T0,
            details=False,
        ),
    },
    overridden=frozenset({"nina:großbrand - musterstadt"}),
    restart_pending=RestartPending(deadline=T0 + timedelta(minutes=20)),
)


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


def test_round_trip_of_the_02_fields() -> None:
    """Safety, restore, ``off_since`` and ``last_reminder`` survive a round trip.

    Loaded warnings are unconfirmed (``active`` is not trusted after a restart).
    """
    state = _persisted(
        safety=_SAFETY,
        safety_restore=SafetyRestore(turn_on=False, flow=230.0),
        off_since=T0 - timedelta(hours=9),
        last_reminder=T0 - timedelta(hours=1),
    )
    data = state_to_dict(state)
    assert data["safety"]["overridden"] == ["nina:großbrand - musterstadt"]
    assert data["safety"]["active"]["nina:1"]["severity"] == "severe"
    result = state_from_dict(data, T0)
    assert result.state == replace(state, safety_unconfirmed=frozenset(_SAFETY.active))
    assert result.expired is None


def test_expired_restart_wait_and_boost_end_while_offline() -> None:
    """An ended restart wait sends "restarted"; both expiries are reported."""
    state = _persisted(control_enabled=True, safety=_SAFETY)
    result = state_from_dict(state_to_dict(state), T0 + timedelta(hours=1))
    assert result.expired == "boost and restart wait"
    assert result.state.safety.restart_pending is None
    assert result.state.restarted_pending
    assert result.state.pending_trigger is Trigger.TIMEOUT_WHILE_OFFLINE


def test_store_migration_1_1_to_1_2() -> None:
    """1.1 -> 1.2: safety keys added, ``off_since`` of a running pause = its start."""
    old = state_to_dict(
        _persisted(manual=Pause(until=None, started=T0, source=Source.BUTTON))
    )
    for key in ("safety", "safety_restore", "off_since", "last_reminder"):
        del old[key]
    migrated = migrate_store_data(1, 1, old)
    assert migrated["off_since"] == T0.isoformat()
    assert migrated["safety"] == {
        "active": {},
        "overridden": [],
        "restart_pending": None,
    }
    assert migrated["last_reminder"] is None
    assert migrated["safety_restore"] is None
    loaded = state_from_dict(migrated, T0).state
    assert loaded.off_since == T0
    assert loaded.manual == Pause(until=None, started=T0, source=Source.BUTTON)

    boost = state_to_dict(_persisted())
    del boost["off_since"]
    assert migrate_store_data(1, 1, boost)["off_since"] is None
    assert old != migrated  # the input is not changed in place
    assert "off_since" not in old


_AUTO = {
    "shower:sensor.bathroom_humidity": AutoBoost(
        kind=AutoBoostKind.SHOWER,
        entity_id="sensor.bathroom_humidity",
        baseline=55.5,
        started=T0 - timedelta(minutes=5),
        deadline=T0 + timedelta(minutes=55),
    ),
    "cooking_trigger:sensor.hob_power": AutoBoost(
        kind=AutoBoostKind.COOKING,
        entity_id="sensor.hob_power",
        baseline=None,
        started=T0 - timedelta(minutes=5),
        deadline=T0 + timedelta(minutes=10),
    ),
}


def test_round_trip_of_the_03_fields() -> None:
    """Active auto boosts and cooldowns survive a round trip (03-modes.md)."""
    cooldown = {"cooking:sensor.kitchen_voc": T0 + timedelta(minutes=3)}
    state = _persisted(auto_boosts=_AUTO, detector_cooldown=cooldown)
    data = state_to_dict(state)
    assert data["auto_boosts"]["shower:sensor.bathroom_humidity"] == {
        "kind": "shower",
        "entity_id": "sensor.bathroom_humidity",
        "baseline": 55.5,
        "started": "2026-01-15T11:55:00+00:00",
        "deadline": "2026-01-15T12:55:00+00:00",
    }
    assert data["detector_cooldown"] == {
        "cooking:sensor.kitchen_voc": "2026-01-15T12:03:00+00:00"
    }
    result = state_from_dict(data, T0)
    assert result.state == state
    assert result.expired is None


def test_expired_auto_boost_ends_while_offline() -> None:
    """Only the expired auto boost ends; the other keeps its deadline."""
    state = _persisted(control_enabled=True, manual=None, auto_boosts=_AUTO)
    result = state_from_dict(state_to_dict(state), T0 + timedelta(minutes=20))
    assert result.expired == "auto boost"
    assert list(result.state.auto_boosts) == ["shower:sensor.bathroom_humidity"]
    assert result.state.pending_trigger is Trigger.TIMEOUT_WHILE_OFFLINE


def test_store_migration_1_2_to_1_3() -> None:
    """1.2 -> 1.3: empty auto boosts and cooldowns; everything else kept."""
    old = state_to_dict(_persisted())
    del old["auto_boosts"]
    del old["detector_cooldown"]
    migrated = migrate_store_data(1, 2, old)
    assert migrated == {**old, "auto_boosts": {}, "detector_cooldown": {}}
    assert "auto_boosts" not in old
    assert state_from_dict(migrated, T0).state == _persisted()
    # From 1.1 both steps run.
    assert migrate_store_data(1, 1, old)["detector_cooldown"] == {}


@pytest.mark.parametrize(
    "data",
    [
        {"auto_boosts": "x"},
        {"auto_boosts": {"k": "x"}},
        {"auto_boosts": {"k": {"kind": "bath", "entity_id": "sensor.x"}}},
        {
            "auto_boosts": {
                "k": {
                    "kind": "shower",
                    "entity_id": "sensor.x",
                    "baseline": "55",
                    "started": "2026-01-15T12:00:00+00:00",
                    "deadline": "2026-01-15T13:00:00+00:00",
                }
            }
        },
        {
            "auto_boosts": {
                "k": {
                    "kind": "shower",
                    "entity_id": "sensor.x",
                    "started": "2026-01-15T12:00:00+00:00",
                }
            }
        },
        {"detector_cooldown": ["x"]},
        {"detector_cooldown": {"k": None}},
    ],
)
def test_corrupt_auto_boosts_reset(data: object) -> None:
    """Unreadable auto boost data -> defaults and the ``state_reset`` flag."""
    result = state_from_dict(data, T0)
    assert result.reset
    assert result.state == ControllerState()


def test_store_migration_leaves_other_versions_and_bad_data() -> None:
    """Current or newer minor versions and non-mapping data are returned as is."""
    data = {"profile": "quiet"}
    assert migrate_store_data(1, STORE_MINOR_VERSION, data) is data
    assert migrate_store_data(1, STORE_MINOR_VERSION + 1, data) is data
    assert migrate_store_data(STORE_VERSION + 1, 1, data) is data
    assert migrate_store_data(1, 1, ["x"]) == ["x"]


@pytest.mark.parametrize(
    "safety",
    [
        "x",
        {"active": ["x"], "overridden": []},
        {"active": {}, "overridden": "x"},
        {"active": {}, "overridden": [1]},
        {"active": {}, "overridden": [], "restart_pending": "soon"},
        {"active": {}, "overridden": [], "restart_pending": {"deadline": None}},
        {"active": {"k": "x"}},
        {"active": {"k": {"source_id": "s"}}},
        {
            "active": {
                "k": {
                    "source_id": "s",
                    "entity_id": "binary_sensor.x",
                    "incident_key": "s:k",
                    "since": "2026-01-15T12:00:00+00:00",
                    "severity": "bogus",
                }
            }
        },
    ],
)
def test_corrupt_safety_resets(safety: object) -> None:
    """Unreadable safety data -> defaults and the ``state_reset`` flag."""
    result = state_from_dict({"safety": safety}, T0)
    assert result.reset
    assert result.state == ControllerState()


def test_corrupt_restore_resets() -> None:
    """An unreadable ``safety_restore`` -> reset."""
    assert state_from_dict({"safety_restore": "on"}, T0).reset
    assert state_from_dict({"safety_restore": {"flow": "x"}}, T0).reset


def test_runtime_fields_are_not_persisted() -> None:
    """Runtime bookkeeping starts fresh after a load."""
    state = _persisted()
    data = state_to_dict(state)
    assert set(data) == {
        "control_enabled",
        "profile",
        "manual",
        "safety",
        "safety_restore",
        "auto_boosts",
        "detector_cooldown",
        "dry_active",
        "dry_humidity_high_since",
        "off_since",
        "last_reminder",
        "stopped_by_integration",
        "last_write",
        "last_written_flow",
        "last_mode",
    }
    assert datetime.fromisoformat(data["last_write"]).tzinfo == UTC
