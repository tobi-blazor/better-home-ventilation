# Implementation plan

Milestones and tasks for implementing the [specification](spec/README.md). The specification defines **what** is built; this plan only defines the **order**. If the plan and the specification disagree, the specification wins.

- One branch per milestone (`m1-skeleton`, `m2-engine`, …), PR into `main`.
- Every milestone ends with green `ruff format`, `ruff check`, `mypy`, `pytest` and an updated `CHANGELOG.md` (if user-visible).
- Engine tasks are test-first: write the scenario from [06-testing.md](spec/06-testing.md) as a failing test, then implement.
- Estimates are person-days (PD) for a developer familiar with Home Assistant integrations.

## Overview

| Milestone | Phase | Content | Depends on | PD |
|---|---|---|---|---|
| [M0](#m0-decisions-before-code) | – | spec gaps, minimum HA version, initial commit | – | 0.5–1 |
| [M1](#m1-skeleton-and-ci) | 0.1 | project skeleton, tooling, CI | M0 | 1.5–2 |
| [M2](#m2-engine-core) | 0.1 | pure engine: validity, demand, limits, modes, shaping, mapping, persistence format | M1 | 5–7 |
| [M3](#m3-config-and-options-flow) | 0.1 | config flow, options flow, readiness subentries, translations | M1 | 5–7 |
| [M4](#m4-controller-and-actuator) | 0.1 | HA glue: controller, timers, store, actuator adapters, readiness | M2, M3 | 7–9 |
| [M5](#m5-entities-services-logbook) | 0.1 | entities, services, events, logbook | M4 | 4–5 |
| [M6](#m6-release-010) | 0.1 | docs, shadow operation, acceptance, release 0.1.0 | M5 | 1.5–2 |
| [M7](#m7-safety-engine) | 0.2 | safety and reminders in the engine, migrations | M6 | 4–5 |
| [M8](#m8-warning-sources) | 0.2 | warning source subentries, details providers, safety entities | M7 | 5–6 |
| [M9](#m9-notifications) | 0.2 | phone notifications with actions, options steps `safety`/`notifications` | M7 | 3–4 |
| [M10](#m10-diagnostics-repairs-voice-replay-release-020) | 0.2 | diagnostics, repairs, intents, replay tool, release 0.2.0 | M8, M9 | 3–5 |
| [M11](#m11-automatic-boosts-release-030) | 0.3 | shower and cooking detection, release 0.3.0 | M10 | 4–6 |
| [M12](#m12-release-10) | 1.0 | public repository, license, brands, HACS default | M11 | 2–4 |
| | | | **total** | **≈ 45–60** |

```text
M0 → M1 ─┬─ M2 ─┬─ M4 → M5 → M6 → M7 ─┬─ M8 ─┬─ M10 → M11 → M12
         └─ M3 ─┘                      └─ M9 ─┘
```

M2/M3 and M8/M9 can be worked on in parallel.

## M0 Decisions before code

Goal: no open question blocks phase 0.1.

- [ ] Resolve the spec contradictions found during planning (owner decision, then update the spec):
  - [ ] `pause_reminder_after` and `off_notice_after` are in the `boosts.manual` section shown in 0.1, but reminders (BHV-24) are 0.2. Move them to 0.2 or to the `notifications` step?
  - [ ] `actual_flow_sensor` (`inputs.monitoring`) has no defined behavior (only backlog BHV-91). Remove from 0.1?
  - [ ] `problem_delay` is needed by the `problem` binary sensor in 0.1, but lives in the 0.2 step `notifications`. Store its default from 0.1 on, without UI?
  - [ ] E39 (0.1 test set) requires "safety TurnOff still issued", safety is 0.2. Split E39 into a 0.1 part and a 0.2 part?
- [ ] Q4: decide the minimum HA version from HA core sources (config subentries, `OptionsFlowWithReload`, `State.last_reported`, `probatio` vs. `voluptuous`). Record it in [05-architecture.md](spec/05-architecture.md#compatibility)
- [ ] Decide whether a schema compatibility module is needed (only if both schema libraries must be supported)
- [ ] Initial commit of the specification (`docs: add specification`), create the private GitHub repository
- [ ] Optional: create GitHub milestones M1–M12 and one issue per task group from this plan

Done when: the spec has no known contradiction for 0.1, Q4 is answered with sources.

## M1 Skeleton and CI

Goal: an empty integration that loads, passes all checks and has CI.

- [ ] `pyproject.toml`: ruff (format + lint, `flake8-tidy-imports` banning `homeassistant` in `engine/`), mypy (`--strict` for `engine/`), pytest, coverage
- [ ] `requirements_test.txt` with `pytest-homeassistant-custom-component` pinned to the development HA version
- [ ] `custom_components/better_home_ventilation/`: `manifest.json` (version `0.1.0`), `__init__.py` (`CONFIG_SCHEMA` config-entry-only, empty setup), `const.py` (`DOMAIN`), `strings.json`, `translations/en.json`, `translations/de.json`
- [ ] `quality_scale.yaml` with every rule as `todo` or `exempt` with comment
- [ ] `hacs.json` with the minimum HA version
- [ ] `engine/__init__.py` (empty) and test C15 (engine imports without `homeassistant`)
- [ ] GitHub Actions: `lint.yml`, `test.yml` (minimum and latest HA), `validate.yml` (hassfest, HACS; checks that need a public repository ignored for now), `release.yml`
- [ ] `CHANGELOG.md` (Keep a Changelog, section `Unreleased`)

Done when: CI is green on `main`, hassfest passes.

## M2 Engine core

Goal: the complete 0.1 control logic as a pure, tested engine. Spec: [02-control.md](spec/02-control.md), [03-modes.md](spec/03-modes.md).

- [ ] `models.py`: `Options` (from the options dict), `InputValue`, `Snapshot`, `ControllerState` (0.1 fields only), `Decision`, effects (`WriteFlow`, `TurnOff`, `TurnOn`, `CorrectReadiness`, `FireEvent`, `ScheduleAt`) as frozen dataclasses
- [ ] `validity.py`: unavailable/unknown/empty, non-finite, `last_reported` staleness, plausibility ranges, presence evaluation (fail-safe home) – BHV-18, E3, E5
- [ ] `demand.py`: `ramp`, CO₂ and humidity ramps, dryness protection with hysteresis and time limit, combining, fallback, reason order – BHV-10…13, 19, E1, E2, E4, E6–E8
- [ ] `demand.py`: limits away and night (window across midnight, humidity override), `limits` list – BHV-14, 15, E9–E12
- [ ] `modes.py`: mode resolution for `disabled`, `off`, `boost`, `fallback`, `automatic`; setpoint per mode; `apply_command` for `boost`, `pause`, `resume`, control switch; timer expiry; `ScheduleAt` effects – BHV-20, 21, 03, E16–E22, E32
- [ ] `shaping.py`: quantisation, bypass, `min_write_interval`, step limits, asymmetric dead band, `write_blocked_by` – BHV-16, E13–E15
- [ ] `mapping.py`: number (flow/percent), fan, select with stage hysteresis, off as lowest value – BHV-01, E41, E42
- [ ] `evaluate()`: startup grace, off/on sequencing, `Decision` with contributions and inputs, `mode_changed` event effect with trigger
- [ ] `serialize.py`: `ControllerState` ↔ dict, ISO UTC datetimes, expired timers ended with `timeout_while_offline` – BHV-50, E38
- [ ] E39 (0.1 part, see M0)

Done when: all 0.1 engine scenarios pass, ≥ 95 % coverage of `engine/`, `mypy --strict` clean.

## M3 Config and options flow

Goal: the entry can be created and every 0.1 option can be changed. Spec: [01-configuration.md](spec/01-configuration.md).

- [ ] `const.py`: all 0.1 option keys, defaults, defaults relative to max flow (rounded to 5 m³/h)
- [ ] Schema compatibility module (if decided in M0)
- [ ] Config flow `user` → `actuator` (only when needed) → `inputs`; `data={}`, all defaults in `options`; `_async_abort_entries_match` on the actuator
- [ ] Config flow validation: `actuator_not_found`, `select_options`, `select_mapping`, `no_inputs`, `already_configured`
- [ ] Options flow (`OptionsFlowWithReload`): menu, steps `actuator` (+ `actuator_mapping`), `inputs`, `control`, `boosts` (section `manual` only); sections, collapsed sections, `output` only in advanced mode, flattening, duration ↔ seconds, `add_suggested_values_to_schema`, max-flow hint
- [ ] Cross-field validation from the [validation rules](spec/01-configuration.md#validation-rules) that apply to 0.1
- [ ] `ReadinessConditionFlow`: `user`, `state` (selector depends on domain), `reconfigure`, title, `unique_id` = entity id, `not_correctable`
- [ ] Update listener that reloads on subentry changes; verify Q9 and adjust the spec
- [ ] `async_migrate_entry` (`VERSION 1`, `MINOR_VERSION 1`, rejects unknown future versions)
- [ ] `strings.json`, `en`, `de`: every field with `data_description`, sections, menu options, selector options, errors
- [ ] Tests C1, C2, C3, C4 (flow part), C4b, C4c; 100 % coverage of `config_flow.py`
- [ ] Verify Q6 (custom helper in the "Create helper" dialog) and record it in the spec

Done when: all flow tests pass, the entry can be created and edited in a dev instance in `en` and `de`.

## M4 Controller and actuator

Goal: the engine runs in Home Assistant and controls a unit. Spec: [02-control.md](spec/02-control.md), [05-architecture.md](spec/05-architecture.md).

- [ ] `__init__.py`: `async_setup_entry` with `runtime_data`, `async_unload_entry`, `async_remove_entry` (delete store); no `ConfigEntryNotReady` for missing sources
- [ ] `controller.py`: state subscriptions for all configured entities, `Debouncer`, periodic timer, `asyncio.Lock` with exactly one follow-up run, start via `async_at_started` – BHV-17
- [ ] Snapshot building from the state machine (including `last_reported` fallback)
- [ ] Effect execution and timer scheduling with `async_track_point_in_utc_time`
- [ ] `Store` load/save (`async_delay_save`), expiry on load, corrupt or future store → defaults and warning – BHV-50
- [ ] `actuator.py`: adapters number/fan/select and off actuator, `blocking=True`, 10 s timeout, own `Context`; verification after 30 s, one retry, `write_mismatch`; `write_failed`; external changes accepted as `current` – BHV-01, 02
- [ ] Availability check and readiness: comparison, auto-correct (max. once per 60 s, 3 attempts, retry every 5 min), event `readiness_corrected` effect, problems `actuator_unavailable`/`not_ready`, `log-when-unavailable` – BHV-04, 05
- [ ] Problem tracking with `problem_delay`, problems cleared automatically
- [ ] Entity renames followed in `options` and subentries, then reload (removal repair follows in M10)
- [ ] Decision log (last 100 decisions in memory)
- [ ] Tests C4 (auto-correct, problem after 3 failures), C4a (rename), C6, C8, C9

Done when: in a dev instance with template/`input_number` stand-ins the unit is controlled according to CO₂ and humidity, and survives restarts.

## M5 Entities, services, logbook

Goal: everything from [04-interface.md](spec/04-interface.md) marked 0.1.

- [ ] `entity.py`: base class, device info (`DeviceEntryType.SERVICE`), `has_entity_name`, `translation_key`, `unique_id`, push updates
- [ ] `sensor.py`: `setpoint` (all attributes, `_unrecorded_attributes`), `reason`, `mode`, `ends_at` – BHV-51, 23
- [ ] `button.py`: `boost`, `pause`, `resume`
- [ ] `switch.py`: `control` (state in the store) – BHV-03
- [ ] `select.py`: `boost_duration`, `pause_duration` (`RestoreEntity`); `number.py`: `boost_level` (`RestoreNumber`)
- [ ] `binary_sensor.py`: `problem` (diagnostic)
- [ ] `PARALLEL_UPDATES = 0` everywhere; `icons.json` (default and per state); entity translations `en`/`de`
- [ ] Removing entities of unconfigured features from the entity registry during setup
- [ ] Services `boost`, `pause`, `resume` in `async_setup`, `services.yaml`, entry resolution, translated `ServiceValidationError`s – BHV-22
- [ ] Events `mode_changed` and `readiness_corrected` fired on the bus – BHV-52
- [ ] `logbook.py`: translated messages for `mode_changed`, linked to the `mode` sensor
- [ ] Tests C5, C7, C12

Done when: all 0.1 entities appear with correct names in `en` and `de`, services and logbook work, ≥ 90 % overall coverage.

## M6 Release 0.1.0

Goal: 0.1 replaces the existing blueprint/automation on a real installation.

- [ ] `quality_scale.yaml` updated
- [ ] README: installation (manual copy or HACS custom repository, verify Q10), configuration walkthrough, every field, limitations (no extract-duct IAQ, pets and away)
- [ ] `examples/dashboard.yaml` with built-in cards only
- [ ] **Shadow operation:** install with the control switch off next to the existing automation for about a week; compare setpoint and reason with the automation's behavior
- [ ] Switch over: control switch on, disable the old automation
- [ ] Acceptance items for 0.1 from [06-testing.md](spec/06-testing.md#acceptance-checklist-real-installation): boost via button, pause and restart, CO₂ sensor disconnected, readiness correction, history and logbook
- [ ] `CHANGELOG.md`, tag `v0.1.0`, GitHub release

Done when: the unit has run on the integration for at least a week without manual intervention.

## M7 Safety engine

Goal: safety shutdown and reminders as pure engine logic. Spec: [03-modes.md](spec/03-modes.md#safety-shutdown).

- [ ] `ControllerState` extended (safety, `off_since`, `last_reminder`), store version bump with migration and test
- [ ] Options migration (`MINOR_VERSION` 2) adding the defaults of the 0.2 options
- [ ] `safety.py`: triggers `on_off`/`numeric`/`state_list`, `WarningDetails`, filters (exclude first, headline/advice, regex, severity), severity normalisation, HTML to text, warning key and incident key
- [ ] `modes.py`: `safety_off` with priority, `override_safety`, `keep_off`, `restart_pending` with timeout, new warning during restart wait, `safety_when_disabled`, load rules (`active` rebuilt, `overridden` kept only with a matching incident) – BHV-40, 42, 43, 44
- [ ] Reminders for indefinite pause and long off states – BHV-24
- [ ] `tests/fixtures/warning_texts_de.json` with the 25 cases
- [ ] Tests E23–E31 (including E30a–E30i), E39 (safety part), E40

Done when: all safety and reminder scenarios pass, migrations tested.

## M8 Warning sources

Goal: warning sources can be configured and deliver warnings into the engine.

- [ ] `WarningSourceFlow`: `user` (entity or device), `trigger` (only for sensor/select), `filters` with suggested keywords per provider and language, `reconfigure`; validation `source_required`, `invalid_regex`, `keyword_conflict`, `numeric_state`, `already_configured`
- [ ] `warning_providers/`: registry, `nina` (`nina.get_details`, cache per `id`, 10 s timeout, fail-safe), `dwd`, `environment_canada`, `attributes`, `none`
- [ ] Controller: subscriptions for sources, device sources expanded to all `binary_sensor`s (and updated on registry changes), problems `warning_source_unavailable`, `warning_details_failed`
- [ ] Entities `safety_warning` and `safety_override`, created only with warning sources
- [ ] Service `override_safety` with `no_active_warning`
- [ ] Verify Q8 (Meteoalarm attributes) and record it in the spec
- [ ] Tests C11a, C11b, C11c

Done when: a template `binary_sensor` and a NINA entity work as sources in a dev instance.

## M9 Notifications

Goal: actionable notifications for safety, restart, reminders and problems. Spec: [04-interface.md](spec/04-interface.md#notifications).

- [ ] Options steps `safety` and `notifications` (list of `notify.mobile_app_*` services), translations
- [ ] `notify.py`: send with actions, tags and priority data, clear with `clear_notification`, `persistent_notification` fallback
- [ ] Listener for `mobile_app_notification_action`, mapping action id → entry and command (`source: notification`)
- [ ] Texts in the `common` translation category; verify Q7, otherwise adjust the spec
- [ ] Problem notifications after `problem_delay`, reminder notifications with "Resume"
- [ ] Tests C10, C11

Done when: on a phone, "Keep ventilating", "Restart now", "Keep off" and "Resume" trigger the right commands.

## M10 Diagnostics, repairs, voice, replay, release 0.2.0

- [ ] `diagnostics.py` with decision log, presence redacted – BHV-54, C13
- [ ] Repair issues `missing_entity`, `missing_notify_service`, `state_reset` – BHV-55, C4a (removal part), C14
- [ ] `intent.py`: four intents with `description` and `slot_schema`; verify Q2 and Q3; `examples/custom_sentences/{en,de}/` – BHV-22 (voice), C16
- [ ] `tools/replay.py` (standard library only), CSV input, report output – BHV-56
- [ ] Replay a winter month of recorded data, check reason shares and writes per day
- [ ] README: warning sources and NINA setup, gas detectors are not a suitable source, provider contract
- [ ] Acceptance items for safety from [06-testing.md](spec/06-testing.md#acceptance-checklist-real-installation) with a template `binary_sensor` as test warning
- [ ] `quality_scale.yaml`, `CHANGELOG.md`, tag `v0.2.0`

Done when: all 0.2 acceptance items are checked on the real installation.

## M11 Automatic boosts, release 0.3.0

Spec: [03-modes.md](spec/03-modes.md#automatic-boosts).

- [ ] Options migration and sections `shower`, `cooking`, `common` in the `boosts` step; validation `duplicate_sensor`, `level_range`
- [ ] `detector.py`: rise detector with window, baseline, end offset, maximum duration, cooldown, `higher_is_worse` – BHV-30, 31
- [ ] Cooking trigger entity with threshold and run-on
- [ ] Modes `shower`/`cooking`, night cap for auto boosts, priority between them, persistence of active boosts and cooldowns – BHV-32
- [ ] Entities `shower_detected`, `cooking_detected`, created only when configured
- [ ] Tests E33–E37 and component tests for the new options and entities
- [ ] Real installation: tune `shower_rise` and `cooking_rise` with replay data, `CHANGELOG.md`, tag `v0.3.0`

## M12 Release 1.0

- [ ] License (Q5, owner decision), `LICENSE` file
- [ ] Repository public, description and topics, HACS validation without ignored checks
- [ ] Brand images
- [ ] README complete: screenshots, FAQ, every option documented
- [ ] Four weeks of heating season checked (writes per day, reason shares, humidity percentiles)
- [ ] `quality_scale.yaml` final (Bronze and Silver complete, Gold where applicable)
- [ ] Tag `v1.0.0`, request inclusion in the HACS default repositories
