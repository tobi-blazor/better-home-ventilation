"""Output shaping: target -> setpoint (BHV-16, docs/spec/02-control.md#output-shaping)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math

from .models import Options, WriteBlock


def quantise(value: float, quantum: float) -> float:
    """Round ``value`` to a multiple of ``quantum``; halves round up."""
    if quantum <= 0:
        return value
    return round(math.floor(value / quantum + 0.5) * quantum, 6)


def clamp(value: float, low: float, high: float) -> float:
    """Clamp ``value`` to ``[low, high]``."""
    return max(low, min(high, value))


@dataclass(frozen=True, kw_only=True)
class ShapingResult:
    """Value to write (None = hold) and why nothing is written on a hold."""

    value: float | None
    blocked_by: WriteBlock | None = None


def shape(
    target: float,
    current: float | None,
    *,
    bypass: bool,
    now: datetime,
    last_write: datetime | None,
    options: Options,
) -> ShapingResult:
    """Apply bypass, minimum write interval, step limits and the asymmetric dead band.

    ``target`` is already quantised and clamped. A hold where target and
    current are equal reports no ``blocked_by`` (nothing to write).
    """
    if bypass or current is None:
        return ShapingResult(value=target)
    difference = target - current
    if difference == 0:
        return ShapingResult(value=None)
    if last_write is not None and now - last_write < options.min_write_interval:
        return ShapingResult(value=None, blocked_by=WriteBlock.INTERVAL)
    if difference > 0 and difference >= options.deadband_up:
        value = current + min(difference, options.max_step_up)
    elif difference < 0 and -difference >= options.deadband_down:
        value = current - min(-difference, options.max_step_down)
    else:
        return ShapingResult(value=None, blocked_by=WriteBlock.DEADBAND)
    value = quantise(value, options.flow_quantum)
    if value == current:
        return ShapingResult(value=None, blocked_by=WriteBlock.DEADBAND)
    return ShapingResult(value=value)
