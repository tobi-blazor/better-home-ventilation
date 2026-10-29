# 06 Testing

## Levels

| Level | Tooling | Scope | Target |
|---|---|---|---|
| engine | plain `pytest`, no HA | every rule in [02-control.md](02-control.md) and [03-modes.md](03-modes.md) | ≥ 95 % line coverage of `engine/` |
| components | `pytest-homeassistant-custom-component` | config/options flow, setup/unload, entities, services, persistence, notifications, actuator adapters | ≥ 90 % overall |
| replay | `tools/replay.py` | recorded history through the engine | plausibility before going live |
| acceptance | real installation, manual | checklist below | before each minor release |

Every requirement ID (BHV-xx) is referenced in at least one test name or docstring, e.g. `test_bhv18_unavailable_co2_is_ignored`.

Time is always injected (`now` parameter in the engine, `freezer` fixture in component tests). No `sleep` in tests.

## Engine scenarios (minimum set)

| # | Scenario | Expected |
|---|---|---|
| E1 | CO₂ 700, 850, 1125, 1400, 1600 ppm, defaults, max 400 | 80, 80, 240, 400, 400 m³/h (before shaping) |
| E2 | two CO₂ sensors 900 / 1200 | ramp uses 1200, `co2_max_entity` = second |
| E3 | one CO₂ sensor `unavailable`, other 900 | uses 900, entity listed in `invalid_inputs` |
| E3a | CO₂ sensors 189 / 249 ppm (miscalibrated, at night); then both 99 ppm | valid: automatic, minimum, no `invalid_inputs`; 99 ppm: invalid, fallback |
| E4 | all CO₂ sensors invalid | mode `fallback`, setpoint 150, reason `fallback` |
| E5 | CO₂ value unchanged but `last_reported` older than 30 min | invalid |
| E6 | humidity max 62 % | humidity ramp ≈ 229 m³/h (quantised 230), reason `humidity_high` if above CO₂ demand |
| E6a | CO₂ and humidity-high (raw): 230/230, 230/226, 233/229.3, 226/233, 240/229.3, 230/250, 230/80, 84/80.3 | `co2_and_humidity_high` (tie, near-tie within one quantum after quantisation), `humidity_high` (225/235), `co2` (clear lead), `humidity_high`, `co2` (humidity at the minimum), `co2` (humidity only rounds to the minimum); `reasons` `[co2, humidity_high]` for the combined reason, otherwise `[reason]` |
| E6b | CO₂ demand 230, humidity 61.9 → 62.0 → 62.1 % and back (227.2 / 229.3 / 231.5) | always `co2_and_humidity_high` (before: flipped between `co2` and `humidity_high`) |
| E6c | CO₂ 230, humidity-high 233.9 … 236.4 (raw edge 5 crossed back and forth) | always `co2_and_humidity_high` (quantised 230 / 235); 237.6 (240) → `humidity_high` |
| E6d | both 260 at night; both 230 while away; both 230 while humidity-high is suppressed by dryness; profile quiet, both 350, boost 300 | `quiet_limit` (`reasons` `[quiet_limit]`); `humidity_high`; `co2`; boost 350 with `co2_and_humidity_high` |
| E7 | humidity mean 38 %, max 58 % | `dry` active, humidity-high allowed for 60 min, then suppressed, reason `dryness_protection` |
| E8 | mean 40.5 % after `dry` | stays `dry` (hysteresis until 42 %) |
| E9 | night 23:00, demand 350 | 250, reason `quiet_limit` |
| E9a | night, quiet limit 250 active, current above it: 260 / 255 with demand 350; 400 with demand 350; 260 with demand 245; quiet max 252 (target 250) at 252; 260 in the evening, night window starts | 250 / 250 written despite the dead band down; 350 (step 50); 245; 250; 250 at 22:00. Interval still holds. Not strict: both below the limit (245 → 230 held), at the limit (250 → 240 held), in the day, in profile `air_quality`, above the humidity override |
| E10 | night, humidity max 66 % | not limited |
| E11 | night window 22:00–07:00 at 06:59 and 07:00 | inside / outside |
| E12 | away, CO₂ 1300 | 80, reason `away` |
| E13 | shaping: current 80, target 400 | 130, then after 5 min 180 … (steps of 50, one per interval) |
| E14 | shaping: current 200, target alternating 185/195 | no write (dead band down 20) |
| E15 | shaping: write 4 min after last write in automatic | held, `write_blocked_by = interval` |
| E16 | boost from automatic 120 | immediate 300, no step limit |
| E17 | boost while CO₂ demands 400 | 400, reason `co2` |
| E18 | boost twice (30 + 30 min) | `until` = start + 60 min |
| E19 | boost extended beyond `boost_max_duration` | capped |
| E20 | pause during boost, then boost during pause | last command wins both times |
| E21 | resume during boost | immediate automatic value |
| E22 | boost expires | back to automatic, trigger `timeout`, no shaping on the step down |
| E23 | warning on | `safety_off`, effects: TurnOff, Notify(safety) |
| E24 | warning on, override, NINA moves the same `id` from slot 2 to slot 1 | stays overridden (key = id) |
| E25 | override, then a warning with a different incident | `safety_off` again |
| E26 | warning ends | `restart_pending`, Notify(restart); after 30 min automatic, trigger `timeout` |
| E27 | warning ends, `keep_off` | mode `off`, indefinite pause |
| E28 | de defaults on the [warning text fixture](#warning-text-fixture) | every case as expected |
| E29 | severity filter `severe`, severity "Moderate" | no warning; "Extreme" → warning; "Bogus" → warning |
| E30 | smoke detector (no attributes) on | warning regardless of filters |
| E30a | "Entwarnung: Feuerwehreinsatz - X" with instruction "Schließen Sie vorsorglich Fenster und Türen" | no warning (exclude) |
| E30b | override incident "Großbrand - X", then "1. AKTUALISIERUNG: Großbrand - X" with new id | stays overridden (same incident key) |
| E30c | details call fails | warning (fail-safe), problem `warning_details_failed` |
| E30d | HTML in description (`<br/>`, `&quot;`) | stripped before matching and in notification text |
| E30e | keyword `/rauch\w*entwicklung/` | regex match |
| E30f | trigger `numeric` threshold 3, DWD level 2 / 3 | inactive / active |
| E30g | trigger `state_list` [`Orange`, `Rouge`], state `rouge` | active (case-insensitive) |
| E30h | two sources: smoke detector (no details) and NINA with filters; only smoke on | warning, filters of NINA not applied to the smoke detector |
| E30i | severity normalisation: `Severe`, `3`, `"3; orange; Severe"`, `bogus` | severe, severe, severe, None (counts as extreme) |
| E31 | control disabled, warning on, `safety_when_disabled` | `safety_off` |
| E32 | control disabled, CO₂ 1400 | mode `disabled`, setpoint shown 400, no WriteFlow effect |
| E33 | shower: 55 → 64 % within 6 min | shower auto boost, baseline 55; ends at ≤ 60 %; cooldown 10 min |
| E34 | shower at night | level capped at 200 |
| E35 | shower sensor goes unavailable for > 30 min | shower ends, trigger `inputs_lost` |
| E36 | cooking `higher_is_worse=false` | detection on falling value |
| E37 | cooking trigger power 1500 W > 100 | cooking boost until run-on after power drops |
| E38 | state serialise → deserialise | identical; expired timers ended with `timeout_while_offline` |
| E39 | startup grace | no WriteFlow effect during grace |
| E39a | startup grace with an active warning (0.2); then overridden during the grace | safety TurnOff (or the lowest value) still issued during grace, setpoint 0; after the override the setpoint stays 0 (unit stopped by the integration, switching on is held by the grace) until TurnOn and the write after the grace |
| E39b | restart: unit at the last written 190, target 85 then 240 during the grace; after the grace an evaluation before the actuator confirms the write | setpoint 190 during the grace (target 85, 240); then 240 written and shown, the evaluation before the confirmation holds 240 (not 190); never confirmed: after the window the actuator value 190 counts again. Unavailable actuator: last written value; not ready: actuator value; stopped by the integration and blocked from switching on: 0 |
| E40 | indefinite pause | reminder after 8 h, then every 24 h |
| E41 | select actuator stages 50/100/200/300/400, target 230 | stage 300; decreasing to 190 stays at 300 until target ≤ 180 (hysteresis) |
| E41a | select stages as E41, decreasing from 100 to 50 (or below) | lowest stage `50`; 51 stays at `100` |
| E42 | percent actuator, max 400, flow 150 | 37.5 → rounded to step |
| E43 | profile `air_quality`, night 23:00, demand 350 | 350, no quiet limit |
| E44 | profile `quiet`, 14:00, demand 350; then user boost 300 | 250, reason `quiet_limit`; boost → 350, reason `co2` (boost uses the demand before the quiet limit) |
| E45 | profile `winter`, `dry`, CO₂ 1300 / 1000 | 285 reason `co2` / 80 reason `dryness_protection` (balanced: 340 / 165) |
| E46 | profile `air_quality`, away, CO₂ 1300 | 340, no away limit |
| E47 | profile change `balanced` → `quiet` while 400 is written | 250 written in the same evaluation, no step limit |
| E48 | away and all CO₂ sensors invalid | 150, reason `fallback` (fallback wins over away) |
| E49 | humidity-high suppressed by dryness, then `dry` ends while `h_max` > 55 % | humidity-high allowed again, counter reset |
| E50 | boost 300 running, boost again with level 400 | 400 written immediately, `until` extended |
| E51 | control switch off during boost; boost while off | boost ended; command rejected (`control_disabled`) |
| E52 | off actuator stopped externally; started externally; `unavailable` → stopped | mode `off` (pause, source `external`); back to `automatic`; no command |
| E53 | night, all CO₂ invalid, `quiet_max_flow` 120 < `fallback_flow` 150 | 120, reason `quiet_limit` |
| E54 | profile `quiet`, CO₂ 1400, user boost 300 | 400, reason `co2` (boost uses the demand before the quiet limit) |
| E55 | pause (unit stopped by the integration), control switch off, control switch on | TurnOn effect, then WriteFlow with bypass; with an external stop instead: no TurnOn |
| E56 | shaping holds (inside dead band), readiness condition becomes unmet with `auto_correct` | CorrectReadiness effect without WriteFlow; after it is met again the next write uses bypass. Control disabled or startup grace: no CorrectReadiness |
| E57 | `fan` actuator without off actuator, turned off externally, then on externally | mode `off` (pause, source `external`), then `automatic` |

## Warning text fixture

`tests/fixtures/warning_texts_de.json`: list of `{headline, text, expected}` used by E28. Initial content (25 cases):

| Expected | Cases |
|---|---|
| shut down | NRW templates "Geruchsbelästigung", "Schadstofffreisetzung", "Rauchgase"; "Starke Geruchsentwicklung nach Brand"; headlines "Großbrand", "Brand mit starker Rauchentwicklung", "Gefahrstoffaustritt", "Gasaustritt", "Austritt von Gefahrstoffen …", "Explosion in Chemiewerk", "Unfall im Kernkraftwerk", "1. AKTUALISIERUNG: Großbrand" |
| keep running | NRW templates "Kampfmittelfund", "Sirenenfehlauslösung", "Probealarm", "Ausfall Stromversorgung", "Hochwasser/Überflutung", "Entwarnung"; live "Entwarnung: Feuerwehreinsatz …", "Amtliche WARNUNG vor STURMBÖEN", drinking water "… Chlorung …"; "Amtliche WARNUNG vor HOHER UV-INTENSITÄT"; "Sperrung der Gasse … – Brandenburg an der Havel"; "Sie können Fenster und Türen wieder öffnen"; "Trinkwasser mit auffälligem Geruch" |

Constructed headlines are marked as such in the fixture. Real messages that were decided wrongly in operation are added with their date.

## Component tests (minimum set)

| # | Test |
|---|---|
| C1 | config flow happy path for `number` (flow), `number` (%), `number` (unknown unit), `fan`, `select`; entry has `data == {}` and all defaults in `options`; with an off actuator in the collapsed section `off` (`off_entity`, `off_state` stored flat, also through the mapping step) and without one (section omitted or empty: `off_entity` absent, `off_state` default) |
| C1a | end to end: an entry created by the config flow with an off switch (`off_state` = `on`) pauses by switching it on and writes no minimum flow; without an off actuator a pause writes the lowest actuator value |
| C2 | config flow errors: `actuator_not_found`, `select_options`, `select_mapping`, `no_inputs`, `off_entity_not_found`, `off_entity_is_actuator` (on the section `off`, shown together with actuator errors); abort `already_configured` for an actuator used by another entry |
| C3 | options flow: each menu step, every validation error key from [01-configuration.md](01-configuration.md#validation-rules) (errors of fields inside a section under the section key), section input flattened, cleared optional fields removed, suggested values shown, `shaping` and `advanced` sections always shown and collapsed (no advanced mode: `show_advanced_options` is deprecated since HA 2026.10, breaks in 2027.6), `away_enabled` without presence entities accepted, entry reloaded after save |
| C4 | readiness subentries: add (select entity → options dropdown), reconfigure, delete, duplicate entity `already_configured`, `not_correctable`; auto-correct calls `select.select_option`; problem after 3 failures |
| C4a | entity rename of a configured sensor updates `options` and reloads; removal raises `missing_entity` repair |
| C4b | `async_migrate_entry`: every migration step, unknown future version fails cleanly |
| C4c | `strings.json`: every field has `data` and `data_description`, every section and menu option is translated (test parses the schemas and the strings file) |
| C5 | setup creates all phase entities with expected entity ids and translations (en, de) |
| C6 | unload removes listeners and timers (no lingering timers: `hass` fixture check) |
| C7 | services: entry resolution with one and two entries, error translations |
| C8 | actuator write with context, verification retry, `write_mismatch` |
| C8a | late confirmation with a new context (device confirms after 20 s and 45 s): a timed pause stays timed, a late start is no command, a late write is own without `write_mismatch`; a stop at the unit after the confirmation, another value, `unavailable` and the commanded state after the window stay external |
| C8b | restart with a stored last written value and a unit that confirms after 20 s: the setpoint sensor shows the last written value during the grace (target changing), then the written value exactly once per evaluation, never the old value again; a write awaits its confirmation until the actuator changes state or the window ends |
| C9 | restart: boost active → reload entry → `ends_at` unchanged, boost continues |
| C10 | notifications: `notify.mobile_app_x` called with actions and tag; firing `mobile_app_notification_action` with `BHV_<entry8>_KEEP_VENTILATING` overrides |
| C11 | no notify services → `persistent_notification` created |
| C11a | NINA: `nina.get_details` mocked with a response; device source expands to all slot binary_sensors; adding a slot (registry change) is picked up |
| C11b | warning source subentries: add entity / device, trigger step only for sensors, suggested filters per provider and language, reconfigure, duplicate `already_configured`, `invalid_regex`, `keyword_conflict` |
| C11c | providers with recorded states: DWD (`warning_<i>_*`), Environment Canada (`alert_<i>`), generic attributes (Meteoalarm-like), template binary_sensor without attributes |
| C12 | logbook describes `mode_changed` |
| C13 | diagnostics content, presence redacted |
| C14 | repair issue for a removed entity |
| C15 | engine import guard: `engine/` imports without `homeassistant` installed in `sys.modules` |
| C16 | intents: boost/pause/resume/status handlers (0.2) |

## Replay

`tools/replay.py` (BHV-56, phase 0.2) runs recorded data through the same engine:

- Input: a CSV with columns `time, entity_id, state` (e.g. exported from InfluxDB or the HA history API) and an options JSON (or the diagnostics download of a running entry).
- Output to stdout: setpoint percentiles, share of time per reason and mode, number of writes per day, longest time at max flow. Optionally a CSV with the decision per evaluation to a given file.
- No HA needed. Standard library only.

Use before going live: replay a winter month and compare the shares of `co2` vs. `minimum` and the number of writes with expectations.

Details (decided 2026-10-29 for M10):

- **Usage:** `python tools/replay.py history.csv options.json [--decisions out.csv] [--profile quiet] [--tz Europe/Berlin] [--interval 60] [--stale]`. Exit code 2 with a message for an unusable file.
- **CSV:** a header row; the time column may be called `time`, `last_changed`, `last_updated` or `_time`, the state column `state`, `value` or `_value` (the history download of Home Assistant, InfluxDB exports). ISO 8601 times; times without offset are in `--tz` (default: the local time zone). All times are converted to that zone, because the engine reads the night window from the wall clock of `now`.
- **Options:** the `options` of an entry, an object with `options`, or a diagnostics download (`data.entry.options`).
- **Evaluations** as in Home Assistant: `debounce` after every recorded change, every `evaluation_interval` (or `--interval`) and at every `ScheduleAt`; the startup grace starts with the first row.
- **Simplifications:** the simulated unit takes every write and every off/on at once (starting at `min_flow`); rows of the actuator and the off actuator are ignored (they show what another controller did); readiness conditions and warning sources are left out; no manual commands. `last_reported` is unknown (never stale) unless `--stale` is given, because recordings usually hold only changes.
- **Report:** setpoint minimum, p5/p25/p50/p75/p95, maximum and mean, each weighted with the time the decision held; share of time per reason and per mode; writes per local day (mean, maximum, every day); the longest continuous time with the setpoint at `max_flow`. `--decisions` writes one row per evaluation (`time, mode, reason, setpoint, target, written, co2_max, co2_max_entity, humidity_max, humidity_mean, limits, write_blocked_by, invalid_inputs, problems`).
- **Tests:** `tests/tools/` with a synthetic two-day fixture (`tests/fixtures/replay_history.csv`); they run without Home Assistant, like the engine tests, and the replay tool is type checked with them (`mypy tests/engine tools tests/tools`).

## Acceptance checklist (real installation)

- [ ] Boost via button and via voice; extend by pressing again; cancel; after expiry back to automatic
- [ ] Pause 1 h; restart HA during the pause; remaining time unchanged after restart
- [ ] Test warning (a template `binary_sensor` toggled by hand as warning source): unit off, notification with actions arrives, "Keep ventilating" lifts it, a second test warning (toggled again, so a new incident) shuts down again
- [ ] Warning ends: restart question arrives; without answer the unit restarts after the timeout
- [ ] Disconnect a CO₂ sensor: it is ignored; with no valid CO₂ sensor, fallback flow and problem notification after `problem_delay`
- [ ] Set the unit's control mode to a wrong value by hand: readiness auto-correction restores it (if configured), the `better_home_ventilation_readiness_corrected` event fires and the logbook shows the state change of the readiness entity
- [ ] Setpoint, reason and mode are recorded in history; logbook shows every mode change with its trigger
- [ ] Four weeks of heating season: writes per day, reason shares and humidity percentiles are plausible (replay or history)
