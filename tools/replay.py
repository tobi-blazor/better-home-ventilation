#!/usr/bin/env python3
"""Replay recorded sensor history through the engine (BHV-56).

docs/spec/06-testing.md#replay. Runs the same pure engine as the integration
on a CSV of recorded states and reports what the integration would have done:
setpoint percentiles, the share of time per reason and per mode, the writes
per day and the longest time at max flow. Optionally writes one CSV row per
evaluation. Standard library only; Home Assistant is not needed.

Usage::

    python tools/replay.py history.csv options.json [--decisions out.csv]

``history.csv`` has a header with the columns ``time``, ``entity_id`` and
``state`` (``last_changed``/``last_updated``/``_time`` and ``value``/``_value``
are accepted as well, e.g. from the history download of Home Assistant or an
InfluxDB export). Times are ISO 8601; times without an offset are taken in
the time zone of ``--tz``. ``options.json`` is the ``options`` of an entry or
a diagnostics download of a running entry.

Simplifications: the unit follows every write at once (no verification), the
actuator and off actuator rows of the CSV are ignored, readiness conditions
and warning sources are left out, there are no manual commands, and
``last_reported`` is unknown unless ``--stale`` is given (recordings usually
hold only changes, so an unchanged sensor would look stale).
"""

from __future__ import annotations

import argparse
from collections.abc import Iterable, Mapping, Sequence
import csv
from dataclasses import dataclass
from datetime import date, datetime, timedelta, tzinfo
import json
from pathlib import Path
import sys
from typing import Any, TextIO
from zoneinfo import ZoneInfo

INTEGRATION_DIR = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "better_home_ventilation"
)
if str(INTEGRATION_DIR) not in sys.path:
    # Import the engine as the top-level package `engine` (without Home
    # Assistant, like the engine tests).
    sys.path.insert(0, str(INTEGRATION_DIR))

from engine import evaluate  # noqa: E402
from engine.mapping import flow_to_native  # noqa: E402
from engine.models import (  # noqa: E402
    ActuatorDomain,
    ActuatorSnapshot,
    ControllerState,
    Decision,
    InputValue,
    OffActuatorSnapshot,
    Options,
    Profile,
    ScheduleAt,
    Snapshot,
    TurnOff,
    TurnOn,
    WriteFlow,
)
from engine.options import (  # noqa: E402
    CONF_DEBOUNCE,
    CONF_EVALUATION_INTERVAL,
    DEFAULTS,
)

TIME_COLUMNS = ("time", "last_changed", "last_updated", "_time")
STATE_COLUMNS = ("state", "value", "_value")
PERCENTILES = (5, 25, 50, 75, 95)
DECISION_COLUMNS = (
    "time",
    "mode",
    "reason",
    "setpoint",
    "target",
    "written",
    "co2_max",
    "co2_max_entity",
    "humidity_max",
    "humidity_mean",
    "limits",
    "write_blocked_by",
    "invalid_inputs",
    "problems",
)


class ReplayError(Exception):
    """An input file cannot be used."""


@dataclass(frozen=True, kw_only=True)
class Row:
    """One recorded state."""

    time: datetime
    entity_id: str
    state: str


@dataclass(frozen=True, kw_only=True)
class Step:
    """One evaluation of the replay."""

    decision: Decision
    wrote: bool


@dataclass(frozen=True, kw_only=True)
class Report:
    """Result of a replay."""

    start: datetime
    end: datetime
    steps: tuple[Step, ...]
    max_flow: float
    profile: Profile


# --- Input --------------------------------------------------------------------


def parse_time(value: str, zone: tzinfo | None) -> datetime:
    """Parse an ISO 8601 time and convert it to ``zone`` (or the local time zone).

    A time without offset is taken in ``zone``. The engine evaluates the night
    window on the wall clock of the time it gets, like the glue does with
    local time.
    """
    parsed = datetime.fromisoformat(value.strip())
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=zone) if zone else parsed.astimezone()
    return parsed.astimezone(zone) if zone else parsed.astimezone()


def _column(header: Sequence[str], names: Sequence[str]) -> str:
    for name in names:
        if name in header:
            return name
    raise ReplayError(f"the CSV has none of the columns {', '.join(names)}")


def read_history(lines: Iterable[str], zone: tzinfo | None) -> list[Row]:
    """Read the recorded states, sorted by time (stable for equal times)."""
    reader = csv.DictReader(lines)
    header = [name.strip() for name in reader.fieldnames or ()]
    reader.fieldnames = header
    time_column = _column(header, TIME_COLUMNS)
    entity_column = _column(header, ("entity_id",))
    state_column = _column(header, STATE_COLUMNS)
    rows: list[Row] = []
    for number, record in enumerate(reader, start=2):
        try:
            when = parse_time(record[time_column] or "", zone)
        except ValueError as err:
            raise ReplayError(f"line {number}: invalid time: {err}") from err
        entity_id = (record[entity_column] or "").strip()
        if entity_id:
            rows.append(
                Row(
                    time=when,
                    entity_id=entity_id,
                    state=(record[state_column] or "").strip(),
                )
            )
    if not rows:
        raise ReplayError("the CSV has no states")
    rows.sort(key=lambda row: row.time)
    return rows


def read_options(data: Any) -> dict[str, Any]:
    """Return the entry options from an options dict or a diagnostics download."""
    if isinstance(data, Mapping):
        for path in (("data", "entry", "options"), ("entry", "options"), ("options",)):
            node: Any = data
            for key in path:
                node = node.get(key) if isinstance(node, Mapping) else None
            if isinstance(node, Mapping):
                return dict(node)
        if "actuator" in data:
            return dict(data)
    raise ReplayError(
        "no entry options found (expected an options or diagnostics file)"
    )


# --- Simulation -------------------------------------------------------------------


class Unit:
    """The simulated unit: takes every write and every off/on at once."""

    def __init__(self, options: Options) -> None:
        """Start running at the minimum flow."""
        self.options = options
        self.native: float | str = flow_to_native(
            options.min_flow, options, ActuatorSnapshot(state=None)
        )
        stopped = "off" if options.off_domain == "fan" else options.off_state
        self._stopped = stopped
        self._running = "on" if stopped == "off" else "off"
        self.off_state: str | None = self._running if options.off_entity else None

    def actuator(self) -> ActuatorSnapshot:
        """Return the engine view of the actuator."""
        native = self.native
        if self.options.actuator_domain == ActuatorDomain.FAN:
            percentage = float(native)
            return ActuatorSnapshot(
                state="on" if percentage > 0 else "off", percentage=percentage
            )
        return ActuatorSnapshot(state=str(native))

    def off_actuator(self) -> OffActuatorSnapshot | None:
        """Return the engine view of the off actuator, if one is configured."""
        if self.off_state is None:
            return None
        return OffActuatorSnapshot(state=self.off_state)

    def apply(self, effect: object) -> bool:
        """Apply one effect; True for a write to the actuator."""
        if isinstance(effect, WriteFlow):
            self.native = effect.native
            return True
        if isinstance(effect, TurnOff) and self.off_state is not None:
            self.off_state = self._stopped
        elif isinstance(effect, TurnOn) and self.off_state is not None:
            self.off_state = self._running
        return False


def replay(
    rows: Sequence[Row],
    raw_options: Mapping[str, Any],
    *,
    profile: Profile = Profile.BALANCED,
    stale: bool = False,
    interval: timedelta | None = None,
) -> Report:
    """Run the engine over the recorded states.

    Evaluations happen ``debounce`` after every recorded change, every
    ``evaluation_interval`` and at the times the engine schedules, like in
    Home Assistant.
    """
    options = Options.from_mapping(raw_options)
    ignored = {options.actuator, options.off_entity}
    period = (
        interval
        if interval is not None
        else timedelta(
            seconds=float(
                raw_options.get(CONF_EVALUATION_INTERVAL)
                or DEFAULTS[CONF_EVALUATION_INTERVAL]
            )
        )
    )
    if period <= timedelta(0):
        raise ReplayError("the evaluation interval must be positive")
    debounce = timedelta(
        seconds=float(raw_options.get(CONF_DEBOUNCE) or DEFAULTS[CONF_DEBOUNCE])
    )
    start, end = rows[0].time, rows[-1].time
    change_times = sorted({row.time + debounce for row in rows if row.time > start})
    unit = Unit(options)
    state = ControllerState(profile=profile)
    states: dict[str, InputValue] = {}
    steps: list[Step] = []
    index = 0
    change_index = 0
    periodic = start
    scheduled: datetime | None = None
    now = start
    while now <= end:
        while index < len(rows) and rows[index].time <= now:
            row = rows[index]
            if row.entity_id not in ignored:
                states[row.entity_id] = InputValue(
                    state=row.state, last_reported=row.time if stale else None
                )
            index += 1
        snapshot = Snapshot(
            now=now,
            started_at=start,
            states=states,
            actuator=unit.actuator(),
            off_actuator=unit.off_actuator(),
        )
        decision, state, effects = evaluate(snapshot, state, options)
        wrote = False
        for effect in effects:
            if isinstance(effect, ScheduleAt):
                scheduled = effect.when
            elif unit.apply(effect):
                wrote = True
        steps.append(Step(decision=decision, wrote=wrote))
        while periodic <= now:
            periodic += period
        while change_index < len(change_times) and change_times[change_index] <= now:
            change_index += 1
        upcoming = [periodic]
        if change_index < len(change_times):
            upcoming.append(change_times[change_index])
        if scheduled is not None and scheduled > now:
            upcoming.append(scheduled)
        now = min(upcoming)
    return Report(
        start=start,
        end=end,
        steps=tuple(steps),
        max_flow=options.max_flow,
        profile=profile,
    )


# --- Statistics -----------------------------------------------------------------------


def _durations(report: Report) -> list[timedelta]:
    """Time each evaluation's decision held (until the next one or the end)."""
    times = [step.decision.time for step in report.steps]
    return [
        (times[i + 1] if i + 1 < len(times) else report.end) - times[i]
        for i in range(len(times))
    ]


def weighted_percentiles(
    values: Sequence[float], weights: Sequence[float], percentiles: Iterable[int]
) -> dict[int, float]:
    """Time-weighted percentiles (the lowest value covering the share)."""
    pairs = sorted(zip(values, weights, strict=True))
    total = sum(weight for _, weight in pairs)
    result: dict[int, float] = {}
    for percentile in percentiles:
        if total <= 0:
            result[percentile] = pairs[len(pairs) // 2][0] if pairs else 0.0
            continue
        goal = total * percentile / 100
        running = 0.0
        for value, weight in pairs:
            running += weight
            if running >= goal:
                result[percentile] = value
                break
        else:  # pragma: no cover - rounding at 100 %
            result[percentile] = pairs[-1][0]
    return result


def shares(report: Report, key: str) -> dict[str, float]:
    """Return the share of time (0…1) per value of ``reason`` or ``mode``."""
    totals: dict[str, float] = {}
    for step, duration in zip(report.steps, _durations(report), strict=True):
        name = str(getattr(step.decision, key))
        totals[name] = totals.get(name, 0.0) + duration.total_seconds()
    whole = sum(totals.values())
    if whole <= 0:
        return {name: 1 / len(totals) for name in totals} if totals else {}
    return {
        name: seconds / whole
        for name, seconds in sorted(totals.items(), key=lambda item: -item[1])
    }


def writes_per_day(report: Report, zone: tzinfo | None) -> dict[date, int]:
    """Count the writes to the actuator per local day (days without writes: 0)."""

    def local(when: datetime) -> date:
        return (when.astimezone(zone) if zone else when.astimezone()).date()

    first, last = local(report.start), local(report.end)
    days = {
        first + timedelta(days=offset): 0 for offset in range((last - first).days + 1)
    }
    for step in report.steps:
        if step.wrote:
            days[local(step.decision.time)] += 1
    return days


def longest_at_max(report: Report) -> tuple[timedelta, datetime | None]:
    """Longest continuous time with the setpoint at max flow, and its start."""
    best, best_start = timedelta(0), None
    run_start: datetime | None = None
    run = timedelta(0)
    for step, duration in zip(report.steps, _durations(report), strict=True):
        if step.decision.setpoint >= report.max_flow - 0.5:
            if run_start is None:
                run_start, run = step.decision.time, timedelta(0)
            run += duration
            if run > best:
                best, best_start = run, run_start
        else:
            run_start = None
    return best, best_start


def _format_duration(value: timedelta) -> str:
    minutes = round(value.total_seconds() / 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes:02d} min" if hours else f"{minutes} min"


def _format_time(when: datetime, zone: tzinfo | None) -> str:
    local = when.astimezone(zone) if zone else when.astimezone()
    return local.strftime("%Y-%m-%d %H:%M")


def format_report(report: Report, zone: tzinfo | None) -> str:
    """Return the text report of a replay."""
    durations = [duration.total_seconds() for duration in _durations(report)]
    setpoints = [step.decision.setpoint for step in report.steps]
    span = report.end - report.start
    lines = [
        f"Replay {_format_time(report.start, zone)} - {_format_time(report.end, zone)}"
        f" ({span.total_seconds() / 86400:.1f} days), {len(report.steps)} evaluations,"
        f" profile {report.profile}",
    ]
    percentiles = weighted_percentiles(setpoints, durations, PERCENTILES)
    total = sum(durations)
    mean = (
        sum(value * weight for value, weight in zip(setpoints, durations, strict=True))
        / total
        if total > 0
        else sum(setpoints) / len(setpoints)
    )
    lines.append(
        "Setpoint (m³/h, time-weighted): "
        f"min {min(setpoints):.0f}, "
        + ", ".join(f"p{p} {value:.0f}" for p, value in percentiles.items())
        + f", max {max(setpoints):.0f}, mean {mean:.0f}"
    )
    for title, key in (("Time per reason:", "reason"), ("Time per mode:", "mode")):
        lines.append(title)
        lines.extend(
            f"  {name:<20} {share * 100:5.1f} %"
            for name, share in shares(report, key).items()
        )
    days = writes_per_day(report, zone)
    counts = list(days.values())
    busiest = max(days, key=lambda day: days[day])
    lines.append(
        f"Writes per day: mean {sum(counts) / len(counts):.1f},"
        f" max {days[busiest]} ({busiest.isoformat()}), total {sum(counts)}"
    )
    lines.extend(f"  {day.isoformat()}  {count}" for day, count in days.items())
    longest, since = longest_at_max(report)
    lines.append(
        f"Longest at max flow ({report.max_flow:.0f} m³/h): "
        + (
            f"{_format_duration(longest)} from {_format_time(since, zone)}"
            if since is not None
            else "never"
        )
    )
    return "\n".join(lines) + "\n"


def _number(value: float | None) -> str:
    return "" if value is None else f"{value:g}"


def write_decisions(report: Report, out: TextIO) -> None:
    """Write one CSV row per evaluation."""
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(DECISION_COLUMNS)
    for step in report.steps:
        decision = step.decision
        inputs = decision.inputs
        writer.writerow(
            [
                decision.time.isoformat(),
                decision.mode,
                decision.reason,
                _number(decision.setpoint),
                _number(decision.target),
                _number(decision.written),
                _number(inputs.co2_max),
                inputs.co2_max_entity or "",
                _number(inputs.h_max),
                _number(inputs.h_mean),
                " ".join(decision.limits),
                decision.write_blocked_by or "",
                " ".join(decision.invalid_inputs),
                " ".join(sorted({str(problem.key) for problem in decision.problems})),
            ]
        )


# --- Command line ---------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Return the command line parser."""
    parser = argparse.ArgumentParser(
        prog="replay.py",
        description=(
            "Replay recorded sensor history through the Better Home Ventilation "
            "engine and report setpoints, reasons and writes."
        ),
    )
    parser.add_argument("history", type=Path, help="CSV with time, entity_id, state")
    parser.add_argument(
        "options", type=Path, help="entry options as JSON, or a diagnostics download"
    )
    parser.add_argument(
        "--decisions", type=Path, help="write one CSV row per evaluation to this file"
    )
    parser.add_argument(
        "--profile",
        choices=[profile.value for profile in Profile],
        default=Profile.BALANCED.value,
        help="profile of the automatic demand (default: balanced)",
    )
    parser.add_argument(
        "--tz",
        help="time zone of the night window and the report, e.g. Europe/Berlin "
        "(default: the local time zone)",
    )
    parser.add_argument(
        "--interval",
        type=float,
        help="periodic evaluation interval in seconds (default: from the options)",
    )
    parser.add_argument(
        "--stale",
        action="store_true",
        help="treat a sensor as stale after stale_timeout without a recorded state",
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run the replay from the command line; returns the exit code."""
    out = stdout or sys.stdout
    err = stderr or sys.stderr
    args = build_parser().parse_args(argv)
    try:
        zone: tzinfo | None = ZoneInfo(args.tz) if args.tz else None
    except (KeyError, ValueError) as error:
        err.write(f"replay: unknown time zone {args.tz}: {error}\n")
        return 2
    try:
        with args.history.open(encoding="utf-8", newline="") as file:
            rows = read_history(file, zone)
        options = read_options(json.loads(args.options.read_text(encoding="utf-8")))
        report = replay(
            rows,
            options,
            profile=Profile(args.profile),
            stale=args.stale,
            interval=timedelta(seconds=args.interval) if args.interval else None,
        )
    except (OSError, ValueError, KeyError, ReplayError) as error:
        err.write(f"replay: {error}\n")
        return 2
    out.write(format_report(report, zone))
    if args.decisions is not None:
        with args.decisions.open("w", encoding="utf-8", newline="") as file:
            write_decisions(report, file)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
