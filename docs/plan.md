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
| [M2](#m2-engine-core) | 0.1 | pure engine: validity, demand, limits, profiles, modes, shaping, mapping, persistence format | M1 | 5.5–7.5 |
| [M3](#m3-config-and-options-flow) | 0.1 | config flow, options flow, readiness subentries, translations | M1 | 5–7 |
| [M4](#m4-controller-and-actuator) | 0.1 | HA glue: controller, timers, store, actuator adapters, readiness | M2, M3 | 7–9 |
| [M5](#m5-entities-services-logbook) | 0.1 | entities, services, events, logbook | M4 | 4–5 |
| [M6](#m6-release-010) | 0.1 | docs, test run on the real installation, release 0.1.0 | M5 | 1.5–2 |
| [M7](#m7-safety-engine) | 0.2 | safety and reminders in the engine, migrations | M6 | 4–5 |
| [M8](#m8-warning-sources) | 0.2 | warning source subentries, details providers, safety entities | M7 | 5–6 |
| [M9](#m9-notifications) | 0.2 | phone notifications with actions, options steps `safety`/`notifications` | M7 | 3–4 |
| [M10](#m10-diagnostics-repairs-voice-replay-release-020) | 0.2 | diagnostics, repairs, intents, replay tool, release 0.2.0 | M8, M9 | 3–5 |
| [M11](#m11-automatic-boosts-release-030) | 0.3 | shower and cooking detection, release 0.3.0 | M10 | 4–6 |
| [M12](#m12-release-10) | 1.0 | public repository, license, brands, HACS default | M11 | 2–4 |
| | | | **total** | **≈ 46–64** |

```text
M0 → M1 ─┬─ M2 ─┬─ M4 → M5 → M6 → M7 ─┬─ M8 ─┬─ M10 → M11 → M12
         └─ M3 ─┘                      └─ M9 ─┘
```

M2/M3 and M8/M9 can be worked on in parallel.

## M0 Decisions before code

Goal: no open question blocks phase 0.1.

- [x] Resolve the spec contradictions found during planning (decided 2026-10-14, spec updated):
  - [x] `pause_reminder_after` and `off_notice_after` moved to the `notifications` step (0.2)
  - [x] `actual_flow_sensor` removed (no behavior until BHV-91)
  - [x] `problem_delay` moved to the `control` step, section `advanced` (0.1)
  - [x] Shaping parameters always visible in the options flow (section `shaping`), technical timing parameters in `advanced` (collapsed, always shown since HA deprecated advanced mode in flows), decided 2026-10-16
  - [x] E39 split into E39 (0.1) and E39a (0.2)
  - [x] Control switch off also blocks readiness corrections (BHV-03)
- [x] Q4: minimum HA version 2026.4, Python 3.14, `import voluptuous as vol`, no schema compatibility module ([05-architecture.md](spec/05-architecture.md#compatibility))
- [x] Q6, Q7, Q9 answered from HA core sources; reload through one update listener ([ADR 0001](adr/0001-update-listener-instead-of-options-flow-with-reload.md))
- [x] Glossary moved to [CONTEXT.md](../CONTEXT.md)
- [x] Inconsistencies U1–U6 from the control overview fixed 2026-10-16 (boost vs. quiet limit, switching on after `disabled`, readiness in every evaluation, fan without off actuator, humidity band wording, plan total)
- [x] Edge cases and profiles decided 2026-10-16: profiles (BHV-26), quiet limit replaces night limit, fallback wins over away, boost commands and profile changes bypass shaping, control switch off rejects and ends boost/pause, external stop of the unit = pause
- [ ] Initial commit of the specification (`docs: add specification`), push to the private GitHub repository
- [ ] Optional: create GitHub milestones M1–M12 and one issue per task group from this plan

Done when: the spec has no known contradiction for 0.1, Q4 is answered with sources, the spec is committed.

## M1 Skeleton and CI

Goal: an empty integration that loads, passes all checks and has CI.

- [x] `pyproject.toml`: ruff (format + lint, `flake8-tidy-imports` banning `homeassistant` in `engine/`), mypy (`--strict` for `engine/`), pytest, coverage
- [x] Dev environment in WSL (Ubuntu): `uv`, Python 3.14, virtual environment; documented in the README (development section)
- [x] `requirements_test.txt` with `pytest-homeassistant-custom-component` pinned to the development HA version (`0.13.372` for HA 2026.10.1; CI minimum job `0.13.321` for HA 2026.4.0)
- [x] `custom_components/better_home_ventilation/`: `manifest.json` (version `0.1.0`), `__init__.py` (`CONFIG_SCHEMA` config-entry-only, empty setup), `const.py` (`DOMAIN`), `strings.json`, `translations/en.json`, `translations/de.json`
- [x] `quality_scale.yaml` with every rule as `todo` or `exempt` with comment
- [x] `hacs.json` with the minimum HA version
- [x] `engine/__init__.py` (empty) and test C15 (engine imports without `homeassistant`)
- [x] GitHub Actions: `lint.yml`, `test.yml` (HA 2026.4 and latest), `validate.yml` (hassfest, HACS; checks that need a public repository ignored for now), `release.yml`
- [x] `CHANGELOG.md` (Keep a Changelog, section `Unreleased`)

Done when: CI is green on `main`, hassfest passes.

## M2 Engine core

Goal: the complete 0.1 control logic as a pure, tested engine. Spec: [02-control.md](spec/02-control.md), [03-modes.md](spec/03-modes.md).

- [x] `models.py`: `Options` (from the options dict), `InputValue`, `Snapshot`, `ControllerState` (0.1 fields only), `Decision`, effects (`WriteFlow`, `TurnOff`, `TurnOn`, `CorrectReadiness`, `FireEvent`, `ScheduleAt`) as frozen dataclasses
- [x] `validity.py`: unavailable/unknown/empty, non-finite, `last_reported` staleness, plausibility ranges, presence evaluation (fail-safe home) – BHV-18, E3, E5
- [x] `demand.py`: `ramp`, CO₂ and humidity ramps, dryness protection with hysteresis and time limit, combining, fallback, reason order – BHV-10…13, 19, E1, E2, E4, E6–E8
- [x] `demand.py`: limits away and quiet (night window across midnight, humidity override), fallback wins over away, `limits` list – BHV-14, 15, E9–E12, E48, E49, E53
- [x] `demand.py`: profiles `balanced`, `air_quality`, `quiet`, `winter` (which limits apply, CO₂ ramp from its midpoint while dry) – BHV-26, E43–E46
- [x] `modes.py`: mode resolution for `disabled`, `off`, `boost`, `fallback`, `automatic`; setpoint per mode; `apply_command` for `boost`, `pause`, `resume`, control switch; timer expiry; `ScheduleAt` effects – BHV-20, 21, 03, E16–E22, E32
- [x] `shaping.py`: quantisation, bypass, `min_write_interval`, step limits, asymmetric dead band, `write_blocked_by` – BHV-16, E13–E15
- [x] `mapping.py`: number (flow/percent), fan, select with stage hysteresis, off as lowest value – BHV-01, E41, E42
- [x] `evaluate()`: startup grace, off/on sequencing, `Decision` with contributions and inputs, `mode_changed` event effect with trigger
- [x] `serialize.py`: `ControllerState` ↔ dict, ISO UTC datetimes, expired timers ended with `timeout_while_offline` – BHV-50, E38
- [x] E39 (no WriteFlow during startup grace); E47, E50 (profile change and boost commands bypass shaping); E51 (control switch rejects and ends boost/pause); E52, E57 (external stop/start of the off actuator or fan); E54 (boost uses the demand before the quiet limit); E55 (switching the unit on again after `disabled`); E56 (readiness corrected in every evaluation)

Done when: all 0.1 engine scenarios pass, ≥ 95 % coverage of `engine/`, `mypy --strict` clean.

## M3 Config and options flow

Goal: the entry can be created and every 0.1 option can be changed. Spec: [01-configuration.md](spec/01-configuration.md).

- [x] `const.py`: all 0.1 option keys, defaults, defaults relative to max flow (rounded to 5 m³/h)
- [x] Config flow `user` → `actuator` (only when needed) → `inputs`; `data={}`, all defaults in `options`; `_async_abort_entries_match` on the actuator
- [x] Config flow validation: `actuator_not_found`, `select_options`, `select_mapping`, `no_inputs`, `already_configured`
- [x] Options flow (plain `OptionsFlow`): menu, steps `actuator` (+ `actuator_mapping`), `inputs`, `control` (sections `shaping` and `advanced` with `problem_delay` always shown and collapsed, `quiet` with `quiet_max_flow`), `boosts` (section `manual` only); sections, collapsed sections, no advanced mode (deprecated in HA 2026.10, see [01-configuration.md](spec/01-configuration.md#principles)), flattening, duration ↔ seconds, `add_suggested_values_to_schema`, max-flow hint
- [x] Cross-field validation from the [validation rules](spec/01-configuration.md#validation-rules) that apply to 0.1
- [x] `ReadinessConditionFlow`: `user`, `state` (selector depends on domain), `reconfigure`, title, `unique_id` = entity id, `not_correctable`
- [x] One update listener that reloads on every change of options or subentries; subentry flows use `async_update_and_abort`, never `async_update_reload_and_abort` ([ADR 0001](adr/0001-update-listener-instead-of-options-flow-with-reload.md))
- [x] `async_migrate_entry` (`VERSION 1`, `MINOR_VERSION 1`, rejects unknown future versions)
- [x] `strings.json`, `en`, `de`: every field with `data_description`, sections, menu options, selector options, errors
- [x] Tests C1, C2, C3, C4 (flow part), C4b, C4c; 100 % coverage of `config_flow.py`

Done when: all flow tests pass, the entry can be created and edited in a dev instance in `en` and `de`.

## M4 Controller and actuator

Goal: the engine runs in Home Assistant and controls a unit. Spec: [02-control.md](spec/02-control.md), [05-architecture.md](spec/05-architecture.md).

- [x] `__init__.py`: `async_setup_entry` with `runtime_data`, `async_unload_entry`, `async_remove_entry` (delete store); no `ConfigEntryNotReady` for missing sources
- [x] `controller.py`: state subscriptions for all configured entities, `Debouncer`, periodic timer, `asyncio.Lock` with exactly one follow-up run, start via `async_at_started` – BHV-17
- [x] Snapshot building from the state machine (staleness from `last_reported`)
- [x] Effect execution and timer scheduling with `async_track_point_in_utc_time`
- [x] `Store` load/save (`async_delay_save`), expiry on load, corrupt or future store → defaults and warning – BHV-50
- [x] `actuator.py`: adapters number/fan/select and off actuator, `blocking=True`, 10 s timeout, own `Context`; verification after 30 s, one retry, `write_mismatch`; `write_failed`; external changes accepted as `current` – BHV-01, 02
- [x] Write gate in the order control switch → availability → readiness; control switch off blocks writes and readiness corrections – BHV-03
- [x] External stop/start of the off actuator (foreign context, valid states only) passed to the engine as `pause`/`resume` with source `external`
- [x] Availability check and readiness: comparison, auto-correct (max. once per 60 s, 3 attempts, retry every 5 min), event `readiness_corrected` effect, problems `actuator_unavailable`/`not_ready`, `log-when-unavailable` – BHV-04, 05
- [x] Problem tracking with `problem_delay`, problems cleared automatically
- [x] Entity renames followed in `options` and subentries; the update listener reloads (removal repair follows in M10)
- [x] Decision log (last 100 decisions in memory)
- [x] Tests C4 (auto-correct, problem after 3 failures), C4a (rename), C6, C8, C9

Done when: in a dev instance with template/`input_number` stand-ins the unit is controlled according to CO₂ and humidity, and survives restarts.

## M5 Entities, services, logbook

Goal: everything from [04-interface.md](spec/04-interface.md) marked 0.1.

- [x] `entity.py`: base class, device info (`DeviceEntryType.SERVICE`), `has_entity_name`, `translation_key`, `unique_id`, push updates
- [x] `sensor.py`: `setpoint` (all attributes, `_unrecorded_attributes`), `reason`, `mode`, `ends_at` – BHV-51, 23
- [x] `button.py`: `boost`, `pause`, `resume`
- [x] `switch.py`: `control` (state in the store) – BHV-03
- [x] `select.py`: `boost_duration`, `pause_duration` (`RestoreEntity`); `number.py`: `boost_level` (`RestoreNumber`)
- [x] `binary_sensor.py`: `problem` (diagnostic)
- [x] `select.py`: `profile` (state in the store, translated options and state icons) – BHV-26
- [x] `ServiceValidationError` `control_disabled` for boost/pause from services and buttons while control is off
- [x] `PARALLEL_UPDATES = 0` everywhere; `icons.json` (default and per state); entity translations `en`/`de`
- [x] Removing entities of unconfigured features from the entity registry during setup
- [x] Services `boost`, `pause`, `resume` in `async_setup`, `services.yaml`, entry resolution, translated `ServiceValidationError`s – BHV-22
- [x] Events `mode_changed` and `readiness_corrected` fired on the bus – BHV-52
- [x] `logbook.py`: translated messages for `mode_changed`, linked to the `mode` sensor
- [x] Tests C5, C7, C12

Done when: all 0.1 entities appear with correct names in `en` and `de`, services and logbook work, ≥ 90 % overall coverage.

## M6 Release 0.1.0

Goal: 0.1 works on a real installation in test runs. Everyday operation stays with the existing automation until 0.2, because the safety shutdown is required before going live.

- [x] `quality_scale.yaml` updated (docs rules)
- [x] README: installation (manual copy or HACS custom repository), configuration walkthrough, every field, limitations (no extract-duct IAQ, pets and away)
- [ ] Verify Q10 (HACS install from the private repository) during the test run; record the answer in the spec and the README
- [x] `examples/dashboard.yaml` with built-in cards only
- [ ] Test run: install, disable the old automation for the duration of the test (otherwise two controllers write), the integration writes to the unit directly; re-enable the automation afterwards
- [ ] Acceptance items for 0.1 from [06-testing.md](spec/06-testing.md#acceptance-checklist-real-installation): boost via button, pause and restart, CO₂ sensor disconnected, readiness correction, history and logbook
- [x] `CHANGELOG.md`: user-facing list of the 0.1 changes under `Unreleased`
- [x] Version header `[0.1.0]` in `CHANGELOG.md`, tag `v0.1.0`, GitHub release (2026-10-21)

Done when: the 0.1 acceptance items pass in a test run of several days, including every profile.

## M7 Safety engine

Goal: safety shutdown and reminders as pure engine logic. Spec: [03-modes.md](spec/03-modes.md#safety-shutdown).

- [ ] `ControllerState` extended (safety, `off_since`, `last_reminder`), store version bump with migration and test
- [ ] Options migration (`MINOR_VERSION` 2) adding the defaults of the 0.2 options (`safety`, `notifications` including `pause_reminder_after`, `off_notice_after`)
- [ ] `safety.py`: triggers `on_off`/`numeric`/`state_list`, `WarningDetails`, filters (exclude first, headline/advice, regex, severity), severity normalisation, HTML to text, warning key and incident key
- [ ] `modes.py`: `safety_off` with priority, `override_safety`, `keep_off`, `restart_pending` with timeout, new warning during restart wait, `safety_when_disabled`, load rules (`active` rebuilt, `overridden` kept only with a matching incident) – BHV-40, 42, 43, 44
- [ ] Reminders for indefinite pause and long off states (including external stops) – BHV-24
- [ ] Decide Q11 (external start of the unit during `safety_off`) and update the spec
- [ ] `tests/fixtures/warning_texts_de.json` with the 25 cases
- [ ] Tests E23–E31 (including E30a–E30i), E39a, E40

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
- [ ] Texts in the `common` translation category (loading confirmed, Q7)
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
- [ ] Switch over for everyday operation: disable the old automation permanently
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
