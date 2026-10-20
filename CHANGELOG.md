# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Integration skeleton `better_home_ventilation` (helper, UI configuration only) that loads in Home Assistant 2026.4 and later; no entities or settings yet.
- Control engine for phase 0.1 (not yet connected to Home Assistant entities): CO₂ and humidity-high ramps, dryness protection, away and quiet limits, profiles, fallback flow, output shaping, actuator mapping for `number`, `fan` and `select`, readiness conditions, boost and pause, and the persisted controller state.
- Setup from **Settings → Devices & services → Helpers**: name, actuator (`number` in m³/h or %, `fan` or `select` with a flow per stage), max flow and room CO₂/humidity sensors; all settings are stored in the options with defaults calculated from the max flow.
- Options for actuator (off actuator, native range), inputs (room sensors, presence), control (CO₂ and humidity ramps, flows, quiet and away limits, output shaping, advanced timing) and manual boost defaults, with validation of every cross-field rule.
- Readiness conditions as sub-entries (entity, expected state, optional automatic correction).
- Every change of the settings or readiness conditions reloads the entry; English and German translations.
- The control now runs in Home Assistant (no entities yet): it starts once Home Assistant has started, writes nothing during the startup grace, evaluates sensor and presence changes after the debounce time, every evaluation interval and at once when the unit or a readiness entity changes, and writes the flow to a `number`, `fan` or `select` actuator. Every write is checked after 30 s and retried once; failed and ignored writes are reported as problems.
- The off actuator stops and starts the unit for a pause; a stop or start at the unit itself is taken as a pause or resume.
- Readiness conditions with automatic correction are corrected before writing (event `better_home_ventilation_readiness_corrected`); a condition that stays unmet after three attempts is reported as not ready.
- Boost, pause, control switch and profile are kept across restarts; a boost or pause that ended while Home Assistant was offline ends on startup. Mode changes fire `better_home_ventilation_mode_changed`.
- Renamed entities are followed in the settings and readiness conditions automatically.
- HACS metadata, development tooling (ruff, mypy, pytest) and CI workflows (lint, tests on the minimum and latest Home Assistant version, hassfest and HACS validation, releases from `CHANGELOG.md`).
