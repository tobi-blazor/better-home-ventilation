"""Flow <-> native actuator value (BHV-01, docs/spec/02-control.md#actuator-mapping).

Internally everything is flow in m³/h. Native values are floats (``number``
value, ``fan`` percentage) or strings (``select`` option).
"""

from __future__ import annotations

import math

from .models import ActuatorDomain, ActuatorSnapshot, Options, is_unavailable
from .options import ACTUATOR_UNIT_PERCENT
from .validity import parse_float

type Native = float | str

_DEFAULT_STEP = 1.0
_FLOAT_TOLERANCE = 1e-6


def round_to_step(value: float, step: float) -> float:
    """Round ``value`` to a multiple of ``step``; halves round up."""
    if step <= 0:
        return value
    return round(math.floor(value / step + 0.5) * step, 6)


def _step(actuator: ActuatorSnapshot) -> float:
    return (
        actuator.step
        if actuator.step is not None and actuator.step > 0
        else _DEFAULT_STEP
    )


def native_bounds(
    options: Options, actuator: ActuatorSnapshot
) -> tuple[float | None, float | None]:
    """Native range of a ``number``: options ``actuator_min/max`` win over attributes."""
    low = (
        options.actuator_min if options.actuator_min is not None else actuator.min_value
    )
    high = (
        options.actuator_max if options.actuator_max is not None else actuator.max_value
    )
    return low, high


def stages(options: Options) -> list[tuple[str, float]]:
    """Return the mapped ``select`` stages sorted by flow (then option text)."""
    return sorted(options.select_mapping.items(), key=lambda item: (item[1], item[0]))


def flow_range(options: Options, actuator: ActuatorSnapshot) -> tuple[float, float]:
    """Range of flows the actuator can produce, for clamping the target."""
    domain = options.actuator_domain
    if domain == ActuatorDomain.SELECT and options.select_mapping:
        flows = [flow for _, flow in stages(options)]
        return flows[0], flows[-1]
    if domain == ActuatorDomain.NUMBER:
        low, high = native_bounds(options, actuator)
        if options.actuator_unit == ACTUATOR_UNIT_PERCENT:
            return (
                (low or 0.0) / 100 * options.max_flow,
                (high if high is not None else 100.0) / 100 * options.max_flow,
            )
        return low or 0.0, high if high is not None else options.max_flow
    return 0.0, options.max_flow


def current_native(options: Options, actuator: ActuatorSnapshot) -> Native | None:
    """Return the current native value of the actuator, or None if it has none."""
    state = actuator.state
    if state is None or is_unavailable(state):
        return None
    domain = options.actuator_domain
    if domain == ActuatorDomain.SELECT:
        return state
    if domain == ActuatorDomain.FAN:
        if state == "off":
            return 0.0
        if state == "on":
            return actuator.percentage
        return None
    return parse_float(state)


def native_to_flow(options: Options, native: Native | None) -> float | None:
    """Convert a native value to flow; None for an unmapped option or no value."""
    if native is None:
        return None
    if isinstance(native, str):
        return options.select_mapping.get(native)
    if options.actuator_domain == ActuatorDomain.FAN or (
        options.actuator_unit == ACTUATOR_UNIT_PERCENT
    ):
        return native / 100 * options.max_flow
    return native


def flow_to_native(
    flow: float,
    options: Options,
    actuator: ActuatorSnapshot,
    current_flow: float | None = None,
) -> Native:
    """Map a flow of a running mode to the native actuator value.

    - ``number``: value (or percent of max flow), rounded to ``step``, clamped.
    - ``fan``: percent of max flow rounded to ``percentage_step``, at least one
      step above 0.
    - ``select``: lowest stage with ``stage_flow ≥ flow``, else the highest
      stage; when decreasing (``flow < current_flow``) the stage is chosen for
      ``flow + deadband_down`` (hysteresis between stages).
    """
    domain = options.actuator_domain
    if domain == ActuatorDomain.SELECT:
        return select_stage(flow, options, current_flow)
    step = _step(actuator)
    if domain == ActuatorDomain.FAN:
        percent = round_to_step(flow / options.max_flow * 100, step)
        return min(max(percent, step), 100.0)
    value = (
        flow / options.max_flow * 100
        if options.actuator_unit == ACTUATOR_UNIT_PERCENT
        else flow
    )
    value = round_to_step(value, step)
    low, high = native_bounds(options, actuator)
    if low is not None:
        value = max(value, low)
    if high is not None:
        value = min(value, high)
    return value


def select_stage(flow: float, options: Options, current_flow: float | None) -> str:
    """Choose the ``select`` stage for a flow, with hysteresis when decreasing.

    A flow at or below the lowest stage always maps to the lowest stage: the
    target is clamped to the lowest stage flow, so with the hysteresis lookup
    (``flow + deadband_down``) the unit could never reach it.
    """
    ordered = stages(options)
    if flow <= ordered[0][1]:
        return ordered[0][0]
    lookup = flow
    if current_flow is not None and flow < current_flow:
        lookup = flow + options.deadband_down
    for option, stage_flow in ordered:
        if stage_flow >= lookup:
            return option
    return ordered[-1][0]


def adjacent_stage(options: Options, current: str, toward: str) -> str:
    """Return the stage next to ``current`` in the direction of ``toward``.

    SPEC-GAP: with a ``select`` actuator a step-limited value can map back to
    the current stage forever (e.g. 300 -> 250 -> stage 300 with the
    hysteresis). Shaping therefore moves at least one stage when the target
    maps to another stage.
    """
    names = [name for name, _ in stages(options)]
    if current not in names or toward not in names:
        return toward
    index, goal = names.index(current), names.index(toward)
    if goal > index:
        return names[index + 1]
    if goal < index:
        return names[index - 1]
    return current


def off_native(options: Options, actuator: ActuatorSnapshot) -> Native:
    """Lowest actuator value, used as "off" without an off actuator."""
    domain = options.actuator_domain
    if domain == ActuatorDomain.SELECT:
        return stages(options)[0][0]
    if domain == ActuatorDomain.FAN:
        return 0.0
    low, _ = native_bounds(options, actuator)
    return low if low is not None else 0.0


def same_native(first: Native | None, second: Native | None) -> bool:
    """Compare native values (floats with a small tolerance)."""
    if first is None or second is None:
        return False
    if isinstance(first, str) or isinstance(second, str):
        return first == second
    return abs(first - second) < _FLOAT_TOLERANCE
