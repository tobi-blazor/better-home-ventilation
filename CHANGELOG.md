# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Integration skeleton `better_home_ventilation` (helper, UI configuration only) that loads in Home Assistant 2026.4 and later; no entities or settings yet.
- Setup from **Settings → Devices & services → Helpers**: name, actuator (`number` in m³/h or %, `fan` or `select` with a flow per stage), max flow and room CO₂/humidity sensors; all settings are stored in the options with defaults calculated from the max flow.
- Options for actuator (off actuator, native range), inputs (room sensors, presence), control (CO₂ and humidity ramps, flows, quiet and away limits, output shaping, advanced timing) and manual boost defaults, with validation of every cross-field rule.
- Readiness conditions as sub-entries (entity, expected state, optional automatic correction).
- Every change of the settings or readiness conditions reloads the entry; English and German translations.
- Control engine for phase 0.1 (not yet connected to Home Assistant entities): CO₂ and humidity-high ramps, dryness protection, away and quiet limits, profiles, fallback flow, output shaping, actuator mapping for `number`, `fan` and `select`, readiness conditions, boost and pause, and the persisted controller state.
- HACS metadata, development tooling (ruff, mypy, pytest) and CI workflows (lint, tests on the minimum and latest Home Assistant version, hassfest and HACS validation, releases from `CHANGELOG.md`).
