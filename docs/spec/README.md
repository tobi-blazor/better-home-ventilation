# Specification: Better Home Ventilation

Status: **v0.1, ready for implementation of phase 0.1** · Domain: `better_home_ventilation`

This specification describes **what** the integration does and the binding design decisions for **how** it is built. Where behavior is described here, the implementation follows it. Gaps or contradictions found during implementation are resolved by updating this specification first (see [CLAUDE.md](../../CLAUDE.md)).

| Document | Content |
|---|---|
| [README.md](README.md) (this file) | purpose, goals, non-goals, requirements, phases, open questions |
| [01-configuration.md](01-configuration.md) | config flow, options flow, parameters with defaults and validation |
| [02-control.md](02-control.md) | evaluation loop, input validity, demand calculation, limits, output shaping, actuator mapping, readiness |
| [03-modes.md](03-modes.md) | operating modes and priorities, commands, safety shutdown, automatic boosts, reminders, persistence |
| [04-interface.md](04-interface.md) | device and entities, services, events, logbook, intents, notifications, repairs, diagnostics |
| [05-architecture.md](05-architecture.md) | module layout, pure engine vs. HA glue, HA APIs, tooling, CI, releases |
| [06-testing.md](06-testing.md) | test strategy, test scenarios, replay of recorded data, acceptance |

Requirement keywords: **must** = required for the phase it is assigned to, **should** = planned, may slip to a later phase, **could** = idea / backlog.

## Purpose

Central ventilation units with heat recovery are usually controlled in Home Assistant with home-made automations or blueprints. Typical problems:

- The setpoint is the maximum of several linear ramps, often fed by a single mixed sensor in the extract duct instead of the rooms where people are.
- Failed sensors are read as `0` (`float(0)`), so a failure silently drives the unit to minimum.
- Humidity can only increase the flow, never protect against over-drying in winter.
- Automations in `mode: single` with long delays block reactions; boost or timed off need extra helpers and automations.
- Nobody can tell afterwards **why** the unit ran at a given flow.

Better Home Ventilation replaces all of this with one config entry per ventilation unit. All logic lives in one place, all parameters are set in the options dialog, and the integration creates its own entities.

## Goals

| ID | Goal |
|---|---|
| G1 | Demand control based on **room** measurements: CO₂ per room, humidity per room |
| G2 | Healthy humidity: upper humidity limit **and** dryness protection |
| G3 | Calm, predictable output without flapping |
| G4 | Simple manual control: boost and pause with duration, dashboard and voice |
| G5 | Safety: shutdown on hazard warnings, never silent restart |
| G6 | Robustness: failed inputs are detected and handled, state survives restarts |
| G7 | Traceability: every setpoint has a reason |
| G8 | Generic: works with any unit exposed as `number`, `fan` or `select`, no vendor specifics in the code |

## Non-goals

- **Device drivers.** The integration only uses existing entities of the unit.
- **Humidifiers.** Use HA's Generic Hygrostat. May become a separate feature later.
- **Bypass / summer cooling logic** (dew point based bypass). Backlog.
- **Ramps on mixed extract-air quality sensors** (IAQ/VOC/CO₂-equivalent in the extract duct). Deliberately not supported as demand inputs, see [02-control.md](02-control.md#inputs).
- **Multi-zone systems** with motorised dampers. One config entry controls exactly one actuator.
- **Cloud services.** Everything runs locally (`iot_class: calculated`).

## Glossary

The domain terms (unit, flow, contribution, demand, target, setpoint, mode, pause, off state, …) are defined in [CONTEXT.md](../../CONTEXT.md), the single glossary of the project. This specification uses them with exactly that meaning.

## Requirements

### Actuator and control

| ID | Verb | Requirement | Phase |
|---|---|---|---|
| BHV-01 | must | One config entry controls one unit through one actuator: `number` (m³/h or %), `fan` (percentage) or `select` (stages mapped to flows) | 0.1 |
| BHV-02 | should | Optional off actuator: `switch`/`input_boolean` with configurable polarity, or `fan.turn_off`. Without off actuator, "off" means the lowest possible actuator value | 0.1 |
| BHV-03 | must | A **control switch** disables all writes (hands-off), including readiness corrections. Entities keep calculating | 0.1 |
| BHV-04 | must | **Readiness conditions:** entity + expected state, optionally auto-corrected before writing. If not met, writes are suspended and a problem is reported | 0.1 |
| BHV-05 | must | Actuator availability is monitored; unavailable actuator suspends writes and raises a problem | 0.1 |

### Automatic demand

| ID | Verb | Requirement | Phase |
|---|---|---|---|
| BHV-10 | must | **CO₂ ramp:** linear from min flow at `co2_start` to max flow at `co2_end`, using the highest valid room sensor | 0.1 |
| BHV-11 | must | **Minimum flow** in automatic mode (moisture protection) | 0.1 |
| BHV-12 | must | **Dryness protection:** if the mean of the living-area humidity sensors is below a threshold, the setpoint is limited to the CO₂ demand; humidity-high may exceed it only for a limited time | 0.1 |
| BHV-13 | must | **Humidity-high ramp:** linear from min flow at `humidity_high_start` to max flow at `humidity_high_end`, using the highest valid living-area sensor | 0.1 |
| BHV-14 | should | **Quiet limit:** within the night window (all day in the quiet profile) the setpoint is capped, except for user boosts and very high humidity | 0.1 |
| BHV-15 | should | **Away:** when nobody is home, minimum flow unless humidity-high applies | 0.1 |
| BHV-16 | must | **Output shaping:** minimum interval between writes, maximum step, asymmetric dead band, quantisation. No flapping | 0.1 |
| BHV-17 | must | Evaluation on input changes (debounced) **and** periodically. Never blocking | 0.1 |
| BHV-18 | must | Inputs that are `unavailable`, `unknown`, non-numeric or stale are **ignored**, never read as 0 | 0.1 |
| BHV-19 | must | **Fallback flow** when CO₂ sensors are configured but none is valid | 0.1 |
| BHV-26 | must | **Profiles:** a fixed set of strategies for the automatic demand (balanced, air quality, quiet, winter), selectable in daily use; the profile decides which limits apply and how CO₂ counts while the air is dry | 0.1 |

### Manual control

| ID | Verb | Requirement | Phase |
|---|---|---|---|
| BHV-20 | must | **Boost** with duration and level. Triggering again extends it. Cancel at any time, then immediately back to the previous mode | 0.1 |
| BHV-21 | must | **Pause** (unit off) with duration or indefinite | 0.1 |
| BHV-22 | must | Control via dashboard (buttons/selects), services and voice (intents) | 0.1 (voice 0.2) |
| BHV-23 | must | Remaining time and boost level are visible as entities | 0.1 |
| BHV-24 | must | Reminder for long off states: indefinite pause after a configurable time and then daily; any off state longer than 24 h | 0.2 |

### Automatic boosts

| ID | Verb | Requirement | Phase |
|---|---|---|---|
| BHV-30 | should | **Shower detection:** bathroom humidity rise within a window starts an auto boost until humidity is back near the baseline, with maximum duration | 0.3 |
| BHV-31 | should | **Cooking detection:** kitchen IAQ/VOC rise relative to its recent minimum starts an auto boost; optional trigger entity (hob power, hood) | 0.3 |
| BHV-32 | should | Auto boosts are capped by their own limit while the quiet limit applies; user boosts are not | 0.3 |

### Safety

| ID | Verb | Requirement | Phase |
|---|---|---|---|
| BHV-40 | must | **Safety shutdown:** any configured warning source stops the unit. Sources are subentries with their own trigger (`on_off`, `numeric` threshold, `state_list`) and filters (event keywords in the headline, advice keywords in the text, exclude keywords, severity). Works with NINA, DWD Weather Warnings, Meteoalarm, Météo-France, Environment Canada, smoke detectors and any helper (template, threshold, group, input_boolean) | 0.2 |
| BHV-41 | must | Safety shutdown sends a notification to the configured phones with the warning text and actions "Keep ventilating" and "OK" | 0.2 |
| BHV-42 | must | **Override** per warning: the user can lift the shutdown for the current warning; a new warning shuts down again. Every override is logged | 0.2 |
| BHV-43 | must | **No silent restart:** when the warning ends, the user is asked to restart; without answer the unit restarts after a configurable timeout | 0.2 |
| BHV-44 | should | Safety shutdown works even when the control switch is off (configurable) | 0.2 |
| BHV-45 | should | **Details providers** per integration (NINA service, DWD/Environment Canada indexed attributes, generic attributes), selected automatically from the entity platform, extensible by a registry; unknown integrations fall back to generic attributes or no details | 0.2 |

### Robustness and traceability

| ID | Verb | Requirement | Phase |
|---|---|---|---|
| BHV-50 | must | Mode, timers, overrides and detector states survive HA restarts | 0.1 |
| BHV-51 | must | **Setpoint sensor** with attributes mode, reason and all contributions; separate **reason** enum sensor for history | 0.1 |
| BHV-52 | must | Every mode change appears in the logbook with its trigger and is fired as an event | 0.1 |
| BHV-53 | must | Configuration follows HA best practices for helper integrations: everything in `entry.options`, editable in the options flow, readiness conditions and warning sources as subentries, changes applied by reload without HA restart ([01-configuration.md](01-configuration.md#principles)) | 0.1 |
| BHV-54 | should | Diagnostics download with configuration, runtime state and a decision log | 0.2 |
| BHV-55 | should | Repair issue when a configured entity no longer exists | 0.2 |
| BHV-56 | should | Replay tool: run recorded sensor history through the engine and report setpoints and reasons | 0.2 |

### Backlog (could)

| ID | Idea |
|---|---|
| BHV-90 | Bypass control with dew-point check (outside cooler **and** drier than inside) |
| BHV-91 | Filter and balance monitoring from actual supply/extract flow sensors |
| BHV-92 | Fireplace mode (no extract surplus, boost only after confirmation) |
| BHV-93 | Additional generic ramp inputs (e.g. PM2.5, room VOC) |
| BHV-94 | Detect manual changes at the unit and pause control for a while |
| BHV-95 | Humidifier coordination |
| BHV-96 | Multiple actuators / zones |
| BHV-97 | Heat recovery monitoring: optional outdoor, supply and extract temperature sensors give a calculated efficiency sensor, η = (supply − outdoor) / (extract − outdoor), valid only with a temperature difference of about 8 K or more and the preheater off; a problem when it stays low in the heating season (e.g. bypass open, fouled exchanger). Not a control input: at typical efficiencies (≈ 0.9) it changes the demand by less than the dead band, and the winter problem is moisture, not heat (analysis 2026-10-16) |

Outdoor air quality (smoke in the neighbourhood, PM above a threshold) is already covered by BHV-40: a threshold or template helper can be a warning source.

## Phases

| Version | Content | Usable for |
|---|---|---|
| 0.1 | actuator, demand control, profiles, shaping, validity, fallback, boost, pause, entities, services, persistence, logbook | test runs on a real installation (everyday operation needs the safety shutdown of 0.2) |
| 0.2 | safety shutdown with notifications, override, confirmed restart, reminders, voice intents, diagnostics, repairs, replay tool | full operation, replacing a blueprint/automation |
| 0.3 | shower and cooking detection | comfort |
| 1.0 | public repository, brands, HACS default listing, docs complete | other users |

### Scope of phase 0.1

Everything with phase 0.1 in the requirement tables and in [04-interface.md](04-interface.md#entities). In the configuration:

| Part | In 0.1 |
|---|---|
| config flow (`user`, `actuator`, `inputs`) | yes |
| options steps `actuator`, `inputs`, `control` | yes |
| options step `boosts` | section `manual` only (shower and cooking sections in 0.3) |
| options steps `safety`, `notifications` | no (0.2); problems are visible on the `problem` binary sensor only |
| subentries `readiness_condition` | yes |
| subentries `warning_source` | no (0.2) |

Unimplemented options must not appear in the UI. Their defaults are added by migrations when the phase lands ([01-configuration.md](01-configuration.md#principles), defaults).

### Before the first line of code

1. ~~Decide the minimum HA version (Q4)~~ Done: HA 2026.4, see [05-architecture.md](05-architecture.md#compatibility).
2. License (Q5) is the owner's decision; until then no LICENSE file.
3. Set up the project skeleton and CI ([05-architecture.md](05-architecture.md#ci-and-releases)), then the engine with tests (E1–E22, E38, E39, E41–E57), then the HA glue.

## Open questions

To be verified during implementation. Each answer goes into the respective document.

| # | Question | Affects |
|---|---|---|
| Q1 | ~~NINA attribute names and severity values~~ **Answered 2026-10-13:** attributes except `id` are deprecated and removed in HA 2026.11; details come from the service `nina.get_details`; severity values `extreme`/`severe`/`moderate`/`minor`/`unknown` | [03-modes.md](03-modes.md#warning-sources) |
| Q2 | Are intents registered by a custom integration offered as tools to LLM conversation agents via the Assist API? Which naming/description is needed? (unconfirmed) | [04-interface.md](04-interface.md#intents) |
| Q3 | Can a custom integration contribute sentences to the default conversation agent, or must users copy `custom_sentences`? (assumed: copy) | [04-interface.md](04-interface.md#intents) |
| Q4 | ~~Minimum supported HA version~~ **Answered 2026-10-14:** HA 2026.4 (Python 3.14); every used API exists from 2025.10 on; `import voluptuous as vol` works before and after the switch to `probatio` in 2026.9 | [05-architecture.md](05-architecture.md#compatibility) |
| Q5 | License | repository |
| Q6 | ~~Does a custom helper integration appear in the "Create helper" dialog?~~ **Answered 2026-10-14:** yes, if it has `config_flow: true` (core `loader.async_get_config_flows`, frontend `dialog-helper-detail.ts`, 2026.10) | [01-configuration.md](01-configuration.md#principles) |
| Q7 | ~~Can `async_get_translations(…, "common", {DOMAIN})` load the `common` category of a custom integration?~~ **Answered 2026-10-14:** yes; every top-level key of `translations/<lang>.json` becomes a category (`helpers/translation.py`, 2026.10.0; category exists since 2024.7) | [04-interface.md](04-interface.md#notifications) |
| Q8 | Exact attribute keys and `awareness_level` format of the Meteoalarm `binary_sensor` (expected from the meteoalert API: `headline`, `description`, `severity`, `event`, `awareness_level`; unconfirmed) | [03-modes.md](03-modes.md#details-providers) |
| Q9 | ~~Does adding/changing/removing a config subentry reload the entry by itself?~~ **Answered 2026-10-14:** no, it only fires update listeners; the integration reloads through one update listener ([ADR 0001](../adr/0001-update-listener-instead-of-options-flow-with-reload.md)) | [01-configuration.md](01-configuration.md#principles) |
| Q10 | Can HACS install from a **private** GitHub repository (needed for testing before the repo is public)? Otherwise copy `custom_components/` manually | [05-architecture.md](05-architecture.md#ci-and-releases) |
| Q11 | What does an external start of the unit (off actuator switched on by someone else) mean during `safety_off`: an override, or ignored and switched off again? Outside safety it ends the pause ([02-control.md](02-control.md#writing)). To decide in 0.2 | [03-modes.md](03-modes.md#safety-shutdown) |
