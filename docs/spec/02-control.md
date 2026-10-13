# 02 Control

This document defines how a setpoint is calculated and written. Mode handling (boost, pause, safety, auto boosts) is in [03-modes.md](03-modes.md); here it appears only where it changes the calculation.

All functions in this document are part of the **pure engine** (no Home Assistant imports), see [05-architecture.md](05-architecture.md#engine-and-glue). They receive a snapshot of the inputs, the controller state, the options and `now`, and return a decision.

## Evaluation loop

An evaluation is triggered by:

| Trigger | Delay |
|---|---|
| state change of any configured input entity | debounced by `debounce` (default 10 s); further changes within the debounce window do not restart the timer |
| periodic timer | every `evaluation_interval` (default 60 s) |
| mode transition (command, timer expiry, warning, detector) | immediately |
| actuator or off-actuator state change | immediately (to update `current`, see [shaping](#output-shaping)); does not by itself cause a write |
| readiness entity state change | immediately |
| options or subentry change | entry reload, then as startup |

Evaluations never overlap: an `asyncio.Lock` serialises them. If an evaluation is requested while one is running, exactly one more evaluation runs afterwards. No evaluation waits for anything except the service calls it issues (with a timeout of 10 s).

Each evaluation:

1. Build the input snapshot ([input validity](#input-validity)).
2. Update detectors (shower, cooking) and safety state, resolve the mode ([03-modes.md](03-modes.md)).
3. Calculate the automatic demand ([demand](#automatic-demand)) and apply limits ([limits](#limits)).
4. Calculate the mode setpoint ([03-modes.md](03-modes.md#setpoint-per-mode)).
5. Apply output shaping ([shaping](#output-shaping)) and map to the actuator ([mapping](#actuator-mapping)).
6. Check readiness and availability ([readiness](#readiness-and-availability)), then write if needed ([writing](#writing)).
7. Publish the decision to the entities, append it to the decision log, persist state if changed.

## Startup

- The controller starts when HA has started (`EVENT_HOMEASSISTANT_STARTED`, or immediately if already running).
- During `startup_grace` (default 60 s) evaluations run and entities show values, but **nothing is written** and no "inputs lost" problem or notification is raised. Reason: many sensors become available a few seconds after start.
- Exception: an active safety shutdown is applied immediately, including during the grace period.
- After the grace period the first write ignores `min_write_interval`.

## Inputs

| Group | Entities | Use |
|---|---|---|
| CO₂ | `co2_sensors` | CO₂ ramp, maximum of valid values |
| living humidity | `humidity_sensors` | humidity-high ramp (maximum), dryness protection (mean), night override (maximum) |
| bathroom humidity | `bathroom_humidity_sensors` | shower detector only |
| cooking | `cooking_sensors`, `cooking_trigger` | cooking detector only |
| presence | `presence_entities` | away |
| actuator | actuator, off actuator | current value, availability |
| readiness | readiness entities | readiness |
| warnings | `warning_source` subentries | safety ([03-modes.md](03-modes.md#warning-sources)) |

Mixed extract-air quality values (IAQ, CO₂-equivalent, VOC from a sensor in the extract duct) are **not** a supported demand input. Nothing in the code prevents selecting such an entity as a CO₂ sensor, but the documentation says so and the CO₂ selector filters on device class `carbon_dioxide`.

## Input validity

A numeric input value is **valid** if all of the following hold:

- state is not `unavailable`, `unknown` or empty
- state parses as a finite float
- `now - state.last_reported ≤ stale_timeout` (default 30 min). `last_reported` is updated by HA even when the value does not change (unconfirmed for all HA versions, see [open question Q4](README.md#open-questions)). If `last_reported` is not available, `last_updated` is used.
- plausibility range: CO₂ 250–10000 ppm, humidity 0–100 %. Values outside are invalid.

Invalid values are **excluded** from every calculation. They are listed in the attribute `invalid_inputs` of the setpoint sensor and, after `problem_delay`, reported as problem `inputs_invalid`.

Presence: an entity counts as "home" if its state is `home` (person, device_tracker), `on` (binary_sensor, input_boolean) or a number > 0 (zone). `unavailable`/`unknown` count as "home" (fail-safe: no away reduction on missing data). Away = all presence entities not home.

## Automatic demand

Notation: `ramp(x, x0, x1, y0, y1)` = `y0` if `x ≤ x0`, `y1` if `x ≥ x1`, else linear between.

```text
co2_max   = max(valid CO₂ values)            or None
h_max     = max(valid living humidity values) or None
h_mean    = mean(valid living humidity values) or None

d_min     = min_flow
d_co2     = ramp(co2_max, co2_start, co2_end, min_flow, max_flow)        if co2_max  is not None
d_hum     = ramp(h_max, humidity_high_start, humidity_high_end, min_flow, max_flow) if h_max is not None
```

### Dryness protection (BHV-12)

State `dry` with hysteresis:

- becomes active when `h_mean < dryness_threshold`
- becomes inactive when `h_mean ≥ dryness_threshold + dryness_hysteresis`
- inactive when `h_mean` is None

While `dry` is active, `d_hum` is allowed only for `dryness_humidity_high_limit` minutes **continuously** (counted from the moment `d_hum > d_min` while dry). After that it is suppressed until `h_max < humidity_high_start` again (then the counter resets). The counter state is persisted.

`dry` only limits humidity-high. CO₂ demand is never limited by dryness (fresh air has priority over humidity).

### Combining

```text
fallback  = co2_sensors configured and co2_max is None
base      = fallback_flow if fallback else max(d_min, d_co2 or d_min)
demand    = max(base, d_hum if allowed else d_min)
```

`d_hum` is "allowed" if it is not None and not suppressed by dryness protection. If neither CO₂ nor humidity values are valid and no CO₂ sensors are configured, the demand is `d_min`.

### Reason

The reason of the automatic demand is determined in this order:

| Condition | Reason |
|---|---|
| `fallback` and `demand == fallback_flow` | `fallback` |
| `d_hum` allowed and `d_hum == demand` and `d_hum > d_min` | `humidity_high` |
| `d_co2 == demand` and `d_co2 > d_min` | `co2` |
| `dry` and `d_hum` suppressed and `d_hum > demand` | `dryness_protection` |
| otherwise | `minimum` |

## Limits

Applied to the automatic demand, in this order.

**Away (BHV-15):** if `away_enabled` and away:
`demand = max(d_min, d_hum if allowed else d_min)`; reason `away` unless reason is `humidity_high`.
CO₂ is ignored while away (nobody produces CO₂; pets are a documented limitation).

**Night (BHV-14):** if `night_enabled` and `now` (local time) is within the night window (start inclusive, end exclusive, may cross midnight) and `demand > night_max_flow` and not (`h_max is not None and h_max ≥ night_humidity_override`):
`demand = night_max_flow`; reason `night_limit`.

All active limits are listed in the attribute `limits` (`away`, `night`, `dry`), also when they did not change the value.

## Output shaping

Shaping turns the mode setpoint into the value actually written. Let:

- `target` = mode setpoint, quantised to `flow_quantum` (round half up) and clamped to the actuator range
- `current` = actuator's current value converted to flow if valid, otherwise the last written value, otherwise None
- `bypass` = the mode changed in this evaluation, or this is the first write after startup, or `current` is None

```text
if bypass:
    write target
elif now - last_write < min_write_interval:
    hold
elif target - current ≥ deadband_up:
    write current + min(target - current, max_step_up)
elif current - target ≥ deadband_down:
    write current - min(current - target, max_step_down)
else:
    hold
```

Then the written value is quantised again and mapped to the actuator.

Properties this guarantees (tested, see [06-testing.md](06-testing.md)):

- at most one write per `min_write_interval` in automatic mode
- no oscillation between two values when the demand is noisy within `deadband_down`
- mode changes (boost start/end, pause, resume) act immediately and in one step, both up and down

`last_write` and the last written value are persisted.

## Actuator mapping

Internally everything is flow in m³/h. Conversion to the native actuator value:

| Actuator | Flow → native | Native → flow (`current`) |
|---|---|---|
| `number`, unit flow | value, rounded to the entity `step`, clamped to `actuator_min`…`actuator_max` | value |
| `number`, unit percent | `flow / max_flow * 100`, rounded to `step`, clamped | `value / 100 * max_flow` |
| `fan` | `flow / max_flow * 100`, rounded to `percentage_step`, at least one step above 0 | `percentage / 100 * max_flow`; `off` → 0 |
| `select` | lowest mapped stage with `stage_flow ≥ flow`; if none, the highest stage. When decreasing, the stage is chosen for `flow + deadband_down` (hysteresis between stages) | flow of the current option; unmapped option → None |

## Off and on

- **Off** (modes `off`, `safety_off`): if an off actuator is configured, set it to `off_state` (`switch.turn_on/turn_off`, `input_boolean.turn_on/turn_off`, `fan.turn_off`). Otherwise write the lowest actuator value (`actuator_min`, 0 %, lowest stage) and set attribute `off_method: minimum`.
- **On** (leaving an off mode): first set the off actuator to the opposite state, then write the setpoint with `bypass`.
- Off and on are never rate limited.

## Readiness and availability

Before any write (including off/on):

1. **Actuator availability:** actuator (and off actuator, if used for this write) must not be `unavailable`/`unknown`. Otherwise: no write, problem `actuator_unavailable`.
2. **Readiness conditions:** each condition compares the entity state with `expected_state` (string compare; numeric compare with tolerance 0.5 for `number`).
   - Not met and `auto_correct`: issue the correction (`select.select_option`, `input_select.select_option`, `switch.turn_on/off`, `input_boolean.turn_on/off`, `number.set_value`) at most once per 60 s per condition, fire event `better_home_ventilation_readiness_corrected`, skip the write in this evaluation and re-evaluate after 5 s.
   - Not met after 3 correction attempts, or not met without `auto_correct`: no write, problem `not_ready` (with the entity id). Retries continue every 5 min.
3. Control switch off: no write (except safety, if `safety_when_disabled`).

Problems are cleared automatically once the condition is resolved.

Logging follows the quality rule `log-when-unavailable`: one `info` message when the actuator (or a readiness condition) becomes unavailable/not met, one when it is back. Nothing in between above `debug`.

## Writing

- Service calls use `blocking=True` with a timeout of 10 s and carry a context created by the integration (`Context()`), so the logbook attributes the change to Better Home Ventilation.
- **Verification:** 30 s after a write the actuator value is compared with the written value (tolerance: one actuator step or 5 m³/h, whichever is larger). On mismatch: one retry. On a second mismatch: problem `write_mismatch`, no further retries until the target changes.
- An external change of the actuator (state change with a context that is not ours) is accepted as the new `current`. The next write follows the normal shaping rules. Pausing control after manual changes is backlog BHV-94.
- Exceptions from service calls are caught and logged (`warning` once, then `debug` while the error persists), the evaluation continues and problem `write_failed` is raised.

## Decision record

Every evaluation produces a `Decision`:

```text
time, mode, reason, setpoint (after shaping, flow), target (before shaping), written (flow or None),
contributions {minimum, co2, humidity_high, fallback, boost, shower, cooking},
inputs {co2_max, co2_max_entity, h_max, h_max_entity, h_mean, presence_home},
limits [..], invalid_inputs [..], problems [..], ends_at, write_blocked_by (None | grace | interval | deadband | disabled | not_ready | unavailable)
```

The last 100 decisions are kept in memory for diagnostics. The current decision feeds the entities ([04-interface.md](04-interface.md)).
