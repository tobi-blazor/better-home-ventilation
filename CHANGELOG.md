# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

First version, for test runs on a real installation. The safety shutdown on hazard warnings is not included yet (planned for 0.2), so this version is not meant for unattended everyday operation.

### Added

- Helper integration `better_home_ventilation` for Home Assistant 2026.4 and later, set up from **Settings → Devices & services → Helpers**; no YAML configuration. Installation through HACS (custom repository) or by copying the folder.
- Setup in three short steps: name, actuator and max flow; the actuator mapping (unit of a `number`, flow per stage of a `select`) only when needed; room CO₂ and humidity sensors. All other settings start with defaults calculated from the max flow.
- Actuators: `number` in m³/h or %, `fan` (percentage) and `select` (stages with hysteresis), with an optional off actuator (`switch`, `input_boolean` or `fan`) and an optional override of the native range.
- Demand control from room sensors: CO₂ ramp (the room with the highest CO₂ wins), humidity-high ramp, minimum flow, dryness protection with hysteresis and a time limit for humidity-high while the air is dry, and a fallback flow when no CO₂ sensor is valid.
- Quiet limit in a night window (with a humidity override) and away limit from presence entities.
- Profiles balanced, air quality, quiet and winter, switched with the **Profile** entity.
- Calm output: minimum write interval, maximum step up and down, asymmetric dead band and a flow step; boosts, pauses and profile changes act at once.
- Inputs that are unavailable, non-numeric, stale or implausible are ignored, never read as 0.
- Readiness conditions (for example a control mode the unit must be in) with optional automatic correction and the event `better_home_ventilation_readiness_corrected`.
- Options for actuator, inputs, control (CO₂, flows, humidity, quiet, away, output shaping, advanced timing) and manual boost defaults, with validation of every cross-field rule. Saving applies the change at once.
- Entities on the controller device: setpoint (with the full decision as attributes), reason, mode and end time sensors; boost, pause and resume buttons; control switch; profile select; boost duration, boost level and pause duration settings; problem sensor. English and German names, states and icons.
- Actions `better_home_ventilation.boost`, `pause` and `resume`, with an optional entry for setups with more than one unit and translated errors.
- Stopping or starting the unit at the unit itself is taken as a pause or resume.
- Every write is checked after 30 s and retried once; an unavailable actuator, an unmet readiness condition, invalid inputs and failed or ignored writes are shown on the problem sensor.
- Boost, pause, profile and control switch are kept across restarts; a boost or pause that ended while Home Assistant was offline ends on startup. Nothing is written during the first minute after startup.
- Mode changes fire `better_home_ventilation_mode_changed` and appear in the logbook of the device (for example "switched to Boost (button, until 18:30)").
- Renamed sensors and actuators are followed in the settings and readiness conditions automatically.
- User documentation in the README (setup, every option, profiles, calculation, entities, actions, limitations, troubleshooting, removal) and an example dashboard in `examples/dashboard.yaml`.

### Changed

- The off actuator and the state that means "stopped" can be chosen during setup (collapsed section **Off actuator** in the first step), not only later in the options.
- The setup and option texts of the off actuator explain that without one the unit is set to its lowest value, which some units do not take as a stop, and that such units need an off actuator.

### Fixed

- Pause did not stop units that keep running at their lowest value, because the off actuator could not be set during setup. An off actuator that does not exist or is the actuator itself is now rejected in the setup and in the options.
