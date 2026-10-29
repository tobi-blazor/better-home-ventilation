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
3. Calculate the contributions and the demand ([demand](#automatic-demand)) and apply limits ([limits](#limits)).
4. Calculate the target of the active mode ([03-modes.md](03-modes.md#target-per-mode)).
5. Apply output shaping to get the setpoint ([shaping](#output-shaping)) and map it to the actuator ([mapping](#actuator-mapping)).
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
| living humidity | `humidity_sensors` | humidity-high ramp (maximum), dryness protection (mean), quiet limit override (maximum) |
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
- `now - state.last_reported ≤ stale_timeout` (default 30 min). `last_reported` is updated by HA even when the value does not change (since HA 2024.4, `States.async_set_internal`; checked on 2026.10.0).
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
co2_from  = (co2_start + co2_end) / 2 if profile is winter and dry else co2_start
d_co2     = ramp(co2_max, co2_from, co2_end, min_flow, max_flow)         if co2_max  is not None
d_hum     = ramp(h_max, humidity_high_start, humidity_high_end, min_flow, max_flow) if h_max is not None
```

`d_min`, `d_co2`, `d_hum` and `fallback_flow` are the **contributions**; `demand` below is their combination ([glossary](../../CONTEXT.md)).

### Dryness protection (BHV-12)

State `dry` with hysteresis:

- becomes active when `h_mean < dryness_threshold`
- becomes inactive when `h_mean ≥ dryness_threshold + dryness_hysteresis`
- inactive when `h_mean` is None

While `dry` is active, `d_hum` is allowed only for `dryness_humidity_high_limit` minutes **continuously** (counted from the moment `d_hum > d_min`, i.e. `h_max > humidity_high_start`, while dry; at exactly `humidity_high_start` the counter neither starts nor resets). After that it is suppressed until `h_max < humidity_high_start` again or `dry` ends (then the counter resets). The counter state is persisted.

In the profiles balanced, air quality and quiet, `dry` only limits humidity-high; the CO₂ demand is never limited by dryness (fresh air has priority over humidity). The winter profile additionally starts the CO₂ ramp at its midpoint while `dry` (`co2_from` above), so moderate CO₂ values no longer raise the flow while the air is too dry; high CO₂ values still reach full flow.

Below the humidity band the **mean** of the living rooms counts (dryness protection). Upwards every **single** room counts: the humidity-high ramp uses the most humid room, so it can raise the flow while the mean is still inside the band. This is intended (one damp bedroom matters even if the house mean is fine).

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
| `dry` and (`d_hum` suppressed and `d_hum > demand`, or `co2_from > co2_start` and the CO₂ ramp from `co2_start` would exceed `demand`) | `dryness_protection` |
| otherwise | `minimum` |

## Limits

Applied to the automatic demand, in this order.

Which limits apply depends on the [profile](#profiles).

**Away (BHV-15):** if the away limit applies and away:
`demand = max(fallback_flow if fallback else d_min, d_hum if allowed else d_min)`; the reason is determined again for the limited value: `fallback` or `humidity_high` if that contribution sets it, otherwise `away`.
CO₂ is ignored while away (nobody produces CO₂; pets are a documented limitation). The fallback flow is kept: without valid CO₂ values the integration cannot know that nobody produces CO₂.

**Quiet (BHV-14):** if the quiet limit applies and `demand > quiet_max_flow` and not (`h_max is not None and h_max ≥ quiet_humidity_override`):
`demand = quiet_max_flow`; reason `quiet_limit`. This also caps the fallback flow (noise is independent of sensor failures).
The demand before this limit is kept as `demand_unquiet` (with its reason); the `boost` mode uses it, so a user boost never gets less than the air quality asks for without noise protection ([03-modes.md](03-modes.md#target-per-mode)).

All active limits are listed in the attribute `limits` (`away`, `quiet`, `dry`), also when they did not change the value.

## Profiles

The profile (BHV-26) is a fixed, built-in strategy, chosen with the `profile` select entity and kept in the store. Default `balanced`. Profiles have no settings of their own; they decide which limits apply, using the options above.

| Profile | Quiet limit | Away limit | CO₂ while `dry` | Auto boost cap (0.3) |
|---|---|---|---|---|
| `balanced` | in the night window | yes | full ramp | in the night window |
| `air_quality` | never | never | full ramp | never |
| `quiet` | all day | yes | full ramp | all day |
| `winter` | in the night window | yes | ramp from its midpoint | in the night window |

- "In the night window" requires `night_enabled`; the window is local time, start inclusive, end exclusive, and may cross midnight.
- The away limit requires `away_enabled` and presence entities.
- Dryness protection of humidity-high applies in every profile.
- User boosts are never capped by the quiet limit.
- A profile change takes effect in the same evaluation and bypasses output shaping, like a command.

## Output shaping

Shaping turns the target into the setpoint, the value actually written. Let:

- `target` = the flow the active mode asks for ([03-modes.md](03-modes.md#target-per-mode)), quantised to `flow_quantum` (round half up) and clamped to the actuator range
- `current` = actuator's current value converted to flow if valid, otherwise the last written value, otherwise None
- `bypass` = the mode changed in this evaluation, or a boost command (start, extension, level change) or a profile change happened since the last evaluation, or this is the first write after startup, or `current` is None

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
| `select` | lowest mapped stage with `stage_flow ≥ flow`; if none, the highest stage. When decreasing, the stage is chosen for `flow + deadband_down` (hysteresis between stages); a flow at or below the lowest stage always selects the lowest stage, otherwise the hysteresis would never reach it. With step limits a shaped value could map back to the current stage forever; shaping therefore moves at least to the adjacent stage when the target maps to a different stage | flow of the current option; unmapped option → None |

## Off and on

- **Off** (modes `off`, `safety_off`): if an off actuator is configured, set it to `off_state` (`switch.turn_on/turn_off`, `input_boolean.turn_on/turn_off`, `fan.turn_off`); no flow value is written. Otherwise write the lowest actuator value (`actuator_min`, 0 %, lowest stage) and set attribute `off_method: minimum`.
- Not every unit stops at the lowest actuator value: some do not accept flows below their own minimum and keep running (seen in a test run: a `number` with `min` 0, a unit that ignores values below 50 m³/h). The integration cannot detect this. Such units need an off actuator; it can be chosen at setup or in the options ([01-configuration.md](01-configuration.md#step-user)), and the texts of both forms point this out.
- **On**: in every running mode (`automatic`, `fallback`, `boost`, `shower`, `cooking`), if the off actuator is in the stopped state **and the integration stopped it** (`stopped_by_integration` in the controller state), first set it to the running state, then write the setpoint with `bypass`. This covers leaving an off mode and also returning from `disabled` after a pause had stopped the unit. A unit stopped at the unit itself is a pause ([writing](#writing)) and is not switched on.
- `stopped_by_integration` is set when the integration switches the off actuator to the stopped state and cleared when it is running again (by the integration or externally).
- Off and on are never rate limited.

## Readiness and availability

Readiness and availability are checked in **every** evaluation, not only when a write is due. Before any write (including off/on) they act as a gate, in this order:

1. **Control switch off:** no write and **no readiness correction** (hands-off, BHV-03). Readiness and availability are still evaluated and shown as problems. Exception (0.2): the safety off/on of `safety_when_disabled` is written, without readiness correction.
2. **Actuator availability:** actuator (and off actuator, if used for this write) must not be `unavailable`/`unknown`. Otherwise: no write, problem `actuator_unavailable`.
3. **Readiness conditions:** each condition compares the entity state with `expected_state` (string compare; numeric compare with tolerance 0.5 for `number`).
   - Not met and `auto_correct`: issue the correction (`select.select_option`, `input_select.select_option`, `switch.turn_on/off`, `input_boolean.turn_on/off`, `number.set_value`) at most once per 60 s per condition, fire event `better_home_ventilation_readiness_corrected`, skip the write in this evaluation and re-evaluate after 5 s. The correction is issued even when shaping would hold the value (no write due), because the unit ignores the written value until readiness is restored. It is not issued while control is disabled or during the startup grace.
   - After a successful correction the next write uses `bypass`: the unit may have run at another flow in the meantime.
   - Not met after 3 correction attempts, or not met without `auto_correct`: no write, problem `not_ready` (with the entity id). Retries continue every 5 min. A condition that cannot be corrected right now (entity unavailable, no `auto_correct`, control disabled, startup grace) is reported as `not_ready` at once; the `problem` sensor still waits `problem_delay`.
   - Readiness gates every write, including TurnOff/TurnOn.
   - SPEC-GAP: this includes the safety TurnOff (implemented so in 0.2). An unmet readiness condition (e.g. a wrong control mode during the startup grace, when no correction is issued) therefore delays a safety shutdown; an off actuator may well work without the readiness condition. To decide whether the safety off should bypass the readiness gate.

Problems are cleared automatically once the condition is resolved.

Logging follows the quality rule `log-when-unavailable`: one `info` message when the actuator (or a readiness condition) becomes unavailable/not met, one when it is back. Nothing in between above `debug`.

## Writing

- Service calls use `blocking=True` with a timeout of 10 s and carry a context created by the integration (`Context()`), so the logbook attributes the change to Better Home Ventilation.
- **Verification:** 30 s after a write the actuator value is compared with the written value (tolerance: one actuator step or 5 m³/h, whichever is larger; a `select` must show the written option). On mismatch: one retry. On a second mismatch: problem `write_mismatch`, no further retries until the target changes. The problem clears when the actuator shows the written value or a write with a different value is verified; a later write of the same value is sent but not retried.
- **Own and external changes:** a state change of the actuator or the off actuator is the integration's **own** change if
  1. it carries a context created by the integration, or
  2. it is a **late confirmation**: the entity reaches the state the integration last commanded for it within the **confirmation window** of 60 s after the command (`CONFIRM_WINDOW`), whatever its context. Off actuator: the commanded stopped or running state; actuator: the written value within the verification tolerance (a `select` the written option).

  Rule 2 covers devices that report a command only on their next poll with a new context, e.g. an ESPHome Modbus device polled every 30 s. The expectation is set with every command (also the verification retry), a newer command for the same entity replaces it, and it is not set when the entity already shows the commanded state. It ends with the first state change that reaches the commanded state (own context or not) or when the window ends. Every other change is **external**: another value or state inside the window, the commanded state after the window or a second time. `unavailable`/`unknown` never confirm a command. The window is the verification plus the retry verification (2 × 30 s): a write confirmed later than 30 s is sent once more with the same value, and the retry verification at 60 s finds it, so a confirmation inside the window never ends in `write_mismatch`.
- An external change of the actuator is accepted as the new `current`. The next write follows the normal shaping rules. Pausing control after manual changes is backlog BHV-94.
- An external change of the **off actuator** between the stopped and the running state (e.g. standby set at the unit for a filter change) is a command with source `external`: a stop becomes `pause()` without duration, a start becomes `resume()`. The same applies to a `fan` actuator without off actuator: an external `off` is a stop, an external `on` is a start. Changes from or to `unavailable`/`unknown` are not commands (e.g. a restart of the device). While control is disabled external changes are ignored. During `safety_off` an external start is an **override** of the current warnings (source `external`, trigger `override`), also while control is disabled; a new warning shuts down again ([Q11](README.md#open-questions), owner decision 2026-10-29, [03-modes.md](03-modes.md#flow)).
- Exceptions from service calls are caught and logged (`warning` once, then `debug` while the error persists), the evaluation continues and problem `write_failed` is raised. This covers every service call of the integration (actuator, off actuator, readiness corrections); the next successful call clears it.

## Decision record

Every evaluation produces a `Decision`:

```text
time, mode, profile, reason, setpoint (after shaping, flow), target (before shaping), written (flow or None),
contributions {minimum, co2, humidity_high, fallback, boost, shower, cooking},
inputs {co2_max, co2_max_entity, h_max, h_max_entity, h_mean, presence_home},
limits [..], invalid_inputs [..], problems [..], ends_at, write_blocked_by (None | grace | interval | deadband | disabled | not_ready | unavailable)
```

The last 100 decisions are kept in memory for diagnostics. The current decision feeds the entities ([04-interface.md](04-interface.md)).
