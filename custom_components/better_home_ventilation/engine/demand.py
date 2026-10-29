"""Automatic demand: contributions, dryness protection, limits, profiles.

Implements docs/spec/02-control.md#automatic-demand, #limits and #profiles
(BHV-10…15, BHV-19, BHV-26).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time

from .models import Contributions, Limit, Options, Profile, Reason
from .shaping import quantise
from .validity import Readings


def ramp(x: float, x0: float, x1: float, y0: float, y1: float) -> float:
    """``y0`` if ``x ≤ x0``, ``y1`` if ``x ≥ x1``, else linear between."""
    if x <= x0:
        return y0
    if x >= x1:
        return y1
    return y0 + (x - x0) * (y1 - y0) / (x1 - x0)


def in_window(moment: time, start: time, end: time) -> bool:
    """Return True if ``moment`` is in ``[start, end)``; the window may cross midnight.

    ``start == end`` is an empty window (the options flow rejects it).
    """
    if start < end:
        return start <= moment < end
    if start > end:
        return moment >= start or moment < end
    return False


def quiet_limit_applies(profile: Profile, options: Options, now: datetime) -> bool:
    """Return True if the profile applies the quiet limit at ``now`` (local time)."""
    if profile is Profile.AIR_QUALITY:
        return False
    if profile is Profile.QUIET:
        return True
    return options.night_enabled and in_window(
        now.time().replace(tzinfo=None), options.night_start, options.night_end
    )


def away_limit_applies(profile: Profile, options: Options) -> bool:
    """Return True if the profile applies the away limit (needs presence entities)."""
    return (
        profile is not Profile.AIR_QUALITY
        and options.away_enabled
        and bool(options.presence_entities)
    )


@dataclass(frozen=True, kw_only=True)
class Dryness:
    """Dryness state and the humidity-high counter (both persisted)."""

    active: bool
    humidity_high_since: datetime | None


def update_dryness(
    *,
    active: bool,
    humidity_high_since: datetime | None,
    h_mean: float | None,
    h_max: float | None,
    options: Options,
    now: datetime,
) -> Dryness:
    """Update the dryness state with hysteresis and the humidity-high counter (BHV-12).

    ``dry`` turns on below ``dryness_threshold`` and off at
    ``dryness_threshold + dryness_hysteresis`` or without a valid mean. The
    counter starts when humidity-high asks for more than the minimum while dry
    (``h_max > humidity_high_start``) and resets when ``h_max`` drops below
    ``humidity_high_start`` or ``dry`` ends.

    At exactly ``humidity_high_start`` the counter neither starts nor resets
    (02-control.md, dryness protection).
    """
    if h_mean is None:
        dry = False
    elif active:
        dry = h_mean < options.dryness_threshold + options.dryness_hysteresis
    else:
        dry = h_mean < options.dryness_threshold

    since = humidity_high_since
    if not dry or h_max is None or h_max < options.humidity_high_start:
        since = None
    elif h_max > options.humidity_high_start and since is None:
        since = now
    return Dryness(active=dry, humidity_high_since=since)


def humidity_high_suppressed(dryness: Dryness, options: Options, now: datetime) -> bool:
    """Return True once humidity-high has run ``dryness_humidity_high_limit`` while dry."""
    return (
        dryness.active
        and dryness.humidity_high_since is not None
        and now - dryness.humidity_high_since >= options.dryness_humidity_high_limit
    )


def _co2_and_humidity(
    d_co2: float | None, d_hum: float | None, options: Options
) -> bool:
    """Return True if CO₂ and humidity-high ask for about the same flow.

    02-control.md#reason: both allowed and above the minimum (also after
    quantisation), and their quantised values differ by at most one
    ``flow_quantum``. Without fallback the larger one is then the demand.
    A quantised value changes only when its contribution crosses a
    quantisation step, not with every sensor reading, so the reason stays
    stable at the edge of the band.
    """
    if d_co2 is None or d_hum is None:
        return False
    quantum = options.flow_quantum
    q_co2, q_hum = quantise(d_co2, quantum), quantise(d_hum, quantum)
    d_min = options.min_flow
    return min(d_co2, d_hum, q_co2, q_hum) > d_min and abs(q_co2 - q_hum) <= quantum


@dataclass(frozen=True, kw_only=True)
class DemandResult:
    """Automatic demand after the limits, plus everything the decision shows."""

    contributions: Contributions
    fallback: bool
    humidity_allowed: bool
    demand: float
    reason: Reason
    demand_unquiet: float
    reason_unquiet: Reason
    limits: tuple[Limit, ...]


def compute_demand(
    readings: Readings,
    dryness: Dryness,
    profile: Profile,
    options: Options,
    now: datetime,
) -> DemandResult:
    """Calculate contributions, combine them and apply the away and quiet limits."""
    summary = readings.summary()
    co2_max, h_max = summary.co2_max, summary.h_max
    d_min = options.min_flow
    dry = dryness.active

    co2_from = (
        (options.co2_start + options.co2_end) / 2
        if profile is Profile.WINTER and dry
        else options.co2_start
    )
    d_co2 = (
        ramp(co2_max, co2_from, options.co2_end, d_min, options.max_flow)
        if co2_max is not None
        else None
    )
    d_hum = (
        ramp(
            h_max,
            options.humidity_high_start,
            options.humidity_high_end,
            d_min,
            options.max_flow,
        )
        if h_max is not None
        else None
    )
    suppressed = d_hum is not None and humidity_high_suppressed(dryness, options, now)
    hum_allowed = d_hum is not None and not suppressed
    hum_term = d_hum if hum_allowed and d_hum is not None else d_min

    fallback = bool(options.co2_sensors) and co2_max is None
    base = (
        options.fallback_flow
        if fallback
        else max(d_min, d_co2 if d_co2 is not None else d_min)
    )
    demand = max(base, hum_term)

    # Reason of the automatic demand, in the order of the spec table.
    if fallback and demand == options.fallback_flow:
        reason = Reason.FALLBACK
    elif not fallback and _co2_and_humidity(
        d_co2, d_hum if hum_allowed else None, options
    ):
        reason = Reason.CO2_AND_HUMIDITY_HIGH
    elif hum_allowed and d_hum == demand and hum_term > d_min:
        reason = Reason.HUMIDITY_HIGH
    elif d_co2 is not None and d_co2 == demand and d_co2 > d_min:
        reason = Reason.CO2
    elif dry and (
        (suppressed and d_hum is not None and d_hum > demand)
        or (
            co2_max is not None
            and co2_from > options.co2_start
            and ramp(
                co2_max, options.co2_start, options.co2_end, d_min, options.max_flow
            )
            > demand
        )
    ):
        reason = Reason.DRYNESS_PROTECTION
    else:
        reason = Reason.MINIMUM

    limits: list[Limit] = []

    # Away (BHV-15): CO₂ is ignored, the fallback flow is kept.
    if away_limit_applies(profile, options) and readings.presence_home is False:
        limits.append(Limit.AWAY)
        demand = max(options.fallback_flow if fallback else d_min, hum_term)
        # The reason is determined again for the limited value (02-control.md,
        # limits), e.g. humidity-high taking over from CO₂.
        if fallback and demand == options.fallback_flow:
            reason = Reason.FALLBACK
        elif hum_allowed and hum_term == demand and hum_term > d_min:
            reason = Reason.HUMIDITY_HIGH
        else:
            reason = Reason.AWAY

    demand_unquiet, reason_unquiet = demand, reason

    # Quiet (BHV-14): not applied above the humidity override.
    if quiet_limit_applies(profile, options, now) and not (
        h_max is not None and h_max >= options.quiet_humidity_override
    ):
        limits.append(Limit.QUIET)
        if demand > options.quiet_max_flow:
            demand = options.quiet_max_flow
            reason = Reason.QUIET_LIMIT

    if dry:
        limits.append(Limit.DRY)

    return DemandResult(
        contributions=Contributions(
            minimum=d_min,
            co2=d_co2,
            humidity_high=d_hum,
            fallback=options.fallback_flow if fallback else None,
        ),
        fallback=fallback,
        humidity_allowed=hum_allowed,
        demand=demand,
        reason=reason,
        demand_unquiet=demand_unquiet,
        reason_unquiet=reason_unquiet,
        limits=tuple(limits),
    )
