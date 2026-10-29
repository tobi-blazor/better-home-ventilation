"""Replay tool (BHV-56, docs/spec/06-testing.md#replay); runs without Home Assistant."""

from __future__ import annotations

import csv
from datetime import UTC, date, datetime, timedelta
import io
import json
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from tools.replay import (
    Profile,
    ReplayError,
    Row,
    main,
    parse_time,
    read_history,
    read_options,
    replay,
    shares,
    weighted_percentiles,
    writes_per_day,
)

FIXTURES = Path(__file__).parents[1] / "fixtures"
HISTORY = FIXTURES / "replay_history.csv"
OPTIONS = FIXTURES / "replay_options.json"
BERLIN = ZoneInfo("Europe/Berlin")


def run(*args: str) -> tuple[int, str, str]:
    """Run the command line tool; return exit code, stdout and stderr."""
    out, err = io.StringIO(), io.StringIO()
    code = main(list(args), stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def options() -> dict[str, Any]:
    """Return the options of the fixture."""
    data = json.loads(OPTIONS.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def test_bhv56_report_of_the_fixture(tmp_path: Path) -> None:
    """Two recorded days give a plausible report and one CSV row per evaluation."""
    decisions = tmp_path / "decisions.csv"
    code, out, err = run(
        str(HISTORY),
        str(OPTIONS),
        "--tz",
        "Europe/Berlin",
        "--decisions",
        str(decisions),
    )
    assert code == 0, err
    lines = out.splitlines()
    assert lines[0].startswith("Replay 2026-01-12 01:00 - 2026-01-14 00:50 (2.0 days)")
    assert lines[0].endswith("profile balanced")
    assert lines[1].startswith("Setpoint (m³/h, time-weighted): min 80, p5 80")
    assert "Time per reason:" in lines
    assert "Time per mode:" in lines
    reasons = out.split("Time per reason:")[1].split("Time per mode:")[0]
    for reason in (
        "co2",
        "minimum",
        "away",
        "quiet_limit",
        "humidity_high",
        "fallback",
    ):
        assert f"  {reason} " in reasons, reason
    assert "  fallback " in out.split("Time per mode:")[1]
    assert any(line.startswith("Writes per day: mean ") for line in lines)
    assert "  2026-01-12  " in out
    assert lines[-1] == "Longest at max flow (400 m³/h): never"

    with decisions.open(encoding="utf-8", newline="") as file:
        rows = list(csv.DictReader(file))
    evaluations = int(lines[0].split(", ")[1].split(" ")[0])
    assert len(rows) == evaluations
    assert rows[0]["write_blocked_by"] == "grace"
    assert any(row["written"] for row in rows)
    failed = [row for row in rows if row["mode"] == "fallback"]
    assert failed
    assert "no_valid_co2" in failed[-1]["problems"]
    assert "sensor.bedroom_co2" in failed[-1]["invalid_inputs"]


def test_statistics_of_a_replay() -> None:
    """Shares add up, percentiles are ordered, writes are counted per day."""
    with HISTORY.open(encoding="utf-8", newline="") as file:
        rows = read_history(file, BERLIN)
    report = replay(rows, options())
    for key in ("reason", "mode"):
        assert sum(shares(report, key).values()) == pytest.approx(1)
    days = writes_per_day(report, BERLIN)
    assert list(days) == [date(2026, 1, 12), date(2026, 1, 13), date(2026, 1, 14)]
    assert sum(days.values()) == sum(step.wrote for step in report.steps)
    # The recorded actuator (another controller) is ignored.
    assert report.steps[0].decision.actuator_value == 80
    # Evaluations: periodic every minute plus the recorded changes.
    assert len(report.steps) > 2 * 24 * 60


def test_profile_and_stale_options() -> None:
    """The profile changes the result; with --stale unchanged sensors expire."""
    with HISTORY.open(encoding="utf-8", newline="") as file:
        rows = read_history(file, BERLIN)
    quiet = replay(rows, options(), profile=Profile.QUIET)
    assert "quiet_limit" in shares(quiet, "reason")

    sparse = [
        Row(time=datetime(2026, 1, 12, 12, tzinfo=UTC), entity_id=entity, state=value)
        for entity, value in (
            ("sensor.living_room_co2", "1200"),
            ("sensor.bedroom_co2", "1200"),
        )
    ] + [Row(time=datetime(2026, 1, 12, 14, tzinfo=UTC), entity_id="x.y", state="1")]
    fresh = replay(sparse, options(), interval=timedelta(minutes=5))
    assert {str(step.decision.mode) for step in fresh.steps} == {"automatic"}
    stale = replay(sparse, options(), stale=True, interval=timedelta(minutes=5))
    assert str(stale.steps[-1].decision.mode) == "fallback"


def test_select_and_fan_actuators_and_off_actuator() -> None:
    """Every actuator kind is simulated (writes change the simulated unit)."""
    rows = [
        Row(time=datetime(2026, 1, 12, 12, tzinfo=UTC), entity_id=entity, state="1300")
        for entity in ("sensor.living_room_co2", "sensor.bedroom_co2")
    ] + [Row(time=datetime(2026, 1, 12, 13, tzinfo=UTC), entity_id="x.y", state="1")]
    select = {
        **options(),
        "actuator": "select.unit_stage",
        "select_mapping": {"Stage 1": 80, "Stage 2": 200, "Stage 3": 400},
    }
    report = replay(rows, select)
    assert report.steps[-1].decision.actuator_value == "Stage 3"
    fan = {**options(), "actuator": "fan.unit", "off_entity": "switch.unit_standby"}
    report = replay(rows, fan)
    assert report.steps[-1].decision.actuator_value == 85.0


def test_input_formats(tmp_path: Path) -> None:
    """HA history columns, a diagnostics download and naive times are accepted."""
    history = tmp_path / "history.csv"
    history.write_text(
        "entity_id,state,last_changed\n"
        "sensor.living_room_co2,900,2026-01-12T12:00:00\n"
        "sensor.living_room_co2,950,2026-01-12T12:30:00\n"
        ",ignored,2026-01-12T12:40:00\n",
        encoding="utf-8",
    )
    diagnostics = tmp_path / "diagnostics.json"
    diagnostics.write_text(
        json.dumps({"home_assistant": {}, "data": {"entry": {"options": options()}}}),
        encoding="utf-8",
    )
    code, out, err = run(str(history), str(diagnostics), "--tz", "Europe/Berlin")
    assert code == 0, err
    assert out.startswith("Replay 2026-01-12 12:00 - 2026-01-12 12:30 (0.0 days)")

    assert read_options({"options": {"actuator": "a"}}) == {"actuator": "a"}
    assert read_options({"actuator": "a", "max_flow": 1}) == {
        "actuator": "a",
        "max_flow": 1,
    }
    naive = parse_time("2026-01-12T12:00:00", None)
    assert naive.tzinfo is not None
    assert parse_time("2026-07-01T12:00:00Z", BERLIN).hour == 14


@pytest.mark.parametrize(
    ("history", "options_text", "message"),
    [
        ("when,entity_id,state\n", "{}", "none of the columns time"),
        ("time,entity_id,state\n", '{"actuator": "a"}', "no states"),
        ("time,entity_id,state\nnot a time,a.b,1\n", "{}", "line 2: invalid time"),
        (
            "time,entity_id,state\n2026-01-12T12:00:00Z,a.b,1\n",
            "[]",
            "no entry options",
        ),
    ],
)
def test_errors(tmp_path: Path, history: str, options_text: str, message: str) -> None:
    """Unusable files end with exit code 2 and a message."""
    history_file = tmp_path / "history.csv"
    history_file.write_text(history, encoding="utf-8")
    options_file = tmp_path / "options.json"
    options_file.write_text(options_text, encoding="utf-8")
    code, out, err = run(str(history_file), str(options_file))
    assert code == 2
    assert out == ""
    assert message in err


def test_bad_time_zone_and_interval(tmp_path: Path) -> None:
    """An unknown time zone and a non-positive interval are rejected."""
    code, _, err = run(str(HISTORY), str(OPTIONS), "--tz", "Mars/Olympus")
    assert code == 2
    assert "unknown time zone" in err
    rows = [Row(time=datetime(2026, 1, 12, tzinfo=UTC), entity_id="a.b", state="1")]
    with pytest.raises(ReplayError, match="interval"):
        replay(rows, options(), interval=timedelta(0))
    code, _, err = run(str(HISTORY), str(tmp_path / "missing.json"))
    assert code == 2


def test_weighted_percentiles() -> None:
    """Percentiles weigh each value with the time it held."""
    result = weighted_percentiles([100, 200, 300], [1, 1, 8], (5, 50, 95))
    assert result == {5: 100, 50: 300, 95: 300}
    assert weighted_percentiles([100, 200], [0, 0], (50,)) == {50: 200}
