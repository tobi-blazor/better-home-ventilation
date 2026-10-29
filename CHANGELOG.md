# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Warning sources: **Add warning source** on the integration page adds a hazard signal that switches the ventilation off (safety shutdown). A source is one entity (`binary_sensor`, `input_boolean`, `switch`, a `sensor` with a numeric threshold or a `sensor`/`select` with a list of states) or one device, whose binary sensors all count (for example a NINA region with several message slots; new slots are picked up automatically). Each source has its own name, trigger and filters and can be reconfigured or deleted.
- Warning details: for NINA the details come from the `nina.get_details` action (Home Assistant 2026.4 reads the attributes instead), for DWD Weather Warnings and Environment Canada from their attributes, for other integrations such as Meteoalarm from generic attributes. Filters per source: event keywords in the headline, advice keywords in the text, exclude keywords for all-clear messages and tests, and a minimum severity. German and English keyword suggestions are filled in for NINA. A source without details, or whose details cannot be fetched within 10 seconds, always counts as a warning.
- Entities **Safety warning** (on while any warning is active, with the list of warnings as attribute) and **Safety override** (keep ventilating despite the current warnings; turning it off shuts down again), created only when at least one warning source exists.
- Action `better_home_ventilation.override_safety`.
- Problems `warning_source_unavailable` and `warning_details_failed` on the problem sensor.
- Phone notifications through the Home Assistant Companion app: the safety shutdown with **Keep ventilating**, the restart question with **Restart now** and **Keep off** when the warning ends, a notice when the unit restarted by itself, reminders with **Resume** while the unit stays off, and problem notifications after the problem delay. Notifications are removed from the phones when their reason is gone. Safety notifications are time-sensitive on iOS and high priority on Android. Without a phone, persistent notifications are created in Home Assistant instead. Texts in English and German.
- Options **Safety** (restart confirmation timeout, safety while control is off) and **Notifications** (phones, which notifications they get, reminder times).
- Repair issue and problem `missing_notify_service` when a selected phone's notify service no longer exists.

### Changed

- The profile select shows a short description with each profile (e.g. "Quiet – limited all day"), so the choice is clear in the dropdown.
- Existing entries and their stored state are migrated for 0.2: the defaults of the new safety and notification settings are added (restart confirmation after 30 minutes, safety also while control is off, reminders after 8 hours of indefinite pause and after 24 hours of any other off state). Nothing changes in operation until warning sources and notifications are available.

## [0.1.0] - 2026-10-21

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
- The off actuator and the state that means "stopped" can be chosen during setup or later in the options; without one the unit is set to its lowest value, which some units do not take as a stop. An off actuator that does not exist or is the actuator itself is rejected.
- A unit that confirms a command only on its next poll (for example an ESPHome Modbus device) is handled: reaching the state or value the integration has just commanded within 60 s counts as the integration's own change, not as a stop, start or flow change at the unit.

[Unreleased]: https://github.com/tobi-blazor/better-home-ventilation/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/tobi-blazor/better-home-ventilation/releases/tag/v0.1.0
