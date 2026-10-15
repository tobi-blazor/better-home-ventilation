# Better Home Ventilation

Demand control for one central ventilation unit in Home Assistant: room measurements and manual commands become one flow the unit runs at, with a reason for every value. Behavior is defined in the [specification](docs/spec/README.md); this file defines the words.

## Language

### Unit and actuator

**Unit**:
The ventilation unit being controlled; one config entry controls exactly one unit.
_Avoid_: device, system, MVHR

**Flow**:
Volume flow in m³/h, the single quantity all calculations use.
_Avoid_: speed, level (except for a boost level), percentage

**Max flow**:
The nominal maximum flow of the unit, configured once.

**Actuator**:
The entity that sets the unit's flow (a `number`, `fan` or `select`).
_Avoid_: fan entity, control entity

**Off actuator**:
An optional entity that stops the unit (e.g. a standby switch).

**Readiness condition**:
An entity state the unit needs before writes to the actuator have an effect (e.g. a control mode).

### From measurement to written value

**Valid input**:
An input value that is numeric, available, not stale and plausible; anything else is ignored, never read as 0.

**Contribution**:
The flow one rule asks for (minimum, CO₂, humidity-high, fallback, boost, shower, cooking).
_Avoid_: demand (for a single rule), partial demand

**Demand**:
The combined flow the automatic rules ask for, after the limits.
_Avoid_: auto, automatic setpoint

**Limit**:
A rule that caps the demand: away, quiet or dryness protection.
_Avoid_: cap, restriction

**Quiet limit**:
The maximum flow for low noise; applies in the night window, or all day in the quiet profile.
_Avoid_: night limit, night max

**Humidity band**:
The healthy range of the mean living-area humidity, from the dryness threshold to the start of the humidity-high ramp.
_Avoid_: comfort zone, optimal range

**Target**:
The flow the active mode asks for, before output shaping.

**Setpoint**:
The flow the unit should run at, after output shaping; what the setpoint sensor shows.
_Avoid_: target (for this), output

**Written value**:
The flow last sent to the actuator.

**Reason**:
The rule or mode that determined the setpoint.
_Avoid_: cause, source

### Modes and commands

**Mode**:
The single operating state active at any time, resolved from the controller state by priority.

**Profile**:
The selected strategy of the automatic demand (balanced, air quality, quiet, winter); it decides which limits apply and how CO₂ counts below the humidity band. A fixed, built-in set.
_Avoid_: mode, operating mode, preset, strategy

**Manual command**:
A boost or pause issued by a user through a button, service, voice, notification action or at the unit itself.

**Boost**:
A manual command that raises the flow to a level for a duration.
_Avoid_: turbo, party mode

**Auto boost**:
A boost started by a detector (shower, cooking), not by a user.

**Pause**:
The manual command that stops the unit for a duration or indefinitely; stopping the unit at the unit itself counts as an indefinite pause.
_Avoid_: off command, standby

**Off**:
The mode a pause produces.

**Off state**:
Either of the modes `off` and `safety_off`.

**Problem**:
A condition that blocks or degrades control (e.g. actuator unavailable, invalid inputs), shown on the problem sensor; not a repair issue (a configuration the user must fix) and not a notification.
_Avoid_: error, fault, alarm

### Safety

**Warning source**:
An entity or device configured as a hazard signal, with its own trigger and filters.
_Avoid_: alarm, warning sensor
