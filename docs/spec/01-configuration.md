# 01 Configuration

All configuration happens in the UI. There is no YAML configuration (`CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)`, required because the integration has an `async_setup` for services).

The design follows the Home Assistant developer documentation and the patterns of the core helper integrations (Generic Hygrostat, Generic Thermostat, Derivative), checked against HA core `main` on 2026-10-13. The rule-by-rule check is in [Best-practice checklist](#best-practice-checklist).

## Principles

| Principle | Decision | Source |
|---|---|---|
| Integration type | `helper`: the integration builds entities on top of other entities, like Generic Hygrostat. It is created from **Settings → Devices & services → Helpers → Create helper** | manifest docs: *helper* = "provides an entity to help the user with automations"; `generic_hygrostat/manifest.json` |
| Where settings live | **Everything in `entry.options`**, nothing in `entry.data` except an empty dict. The title is the name | quality rule `config-flow`: only connection data in `data`, all other settings in `options`. There is no connection here. Core helpers (`SchemaConfigFlowHandler`) store all input in `options` |
| Changing settings | Everything that can be set up can be changed in the options flow, including the actuator. No reconfigure flow needed, because `data` holds nothing | rule `reconfiguration-flow` is about `data`; helpers follow this pattern |
| Reload | Options flow subclasses `OptionsFlowWithReload`. Subentry changes are picked up by an update listener that reloads only when the set of subentries or their data changed ([Q9](README.md#open-questions)) | options flow docs |
| Repeated items | **Readiness conditions and warning sources are config subentries** (`ConfigSubentryFlow`, types `readiness_condition` and `warning_source`), each with its own add/reconfigure/delete in the UI | config flow docs, config subentries |
| Unique entry | No `unique_id` on the entry (entity ids are user-changeable and therefore unsuitable). Instead `_async_abort_entries_match({"actuator": …})`, which checks options and data | rule `unique-config-entry`; `config_entries._async_abort_entries_match` |
| Field help | Every field has a `data_description`, every section a name and description, every menu option a `menu_option_descriptions` entry | rule `config-flow` |
| UI text | All text in `strings.json` (`config`, `options`, `config_subentries`, `selector`, `entity`, `services`, `exceptions`, `issues`, `common`). No UI text in code | rule `config-flow`, `entity-translations`, `exception-translations` |
| Selectors | The most specific selector for every field: entity selectors with domain and device class filters, `NumberSelector` with unit and box mode, `DurationSelector` for durations, `TimeSelector` for times, `SelectSelector` with `translation_key` for choices | rule `config-flow`; `generic_hygrostat/config_flow.py` |
| Grouping | `data_entry_flow.section` (one level, translated, icons in `icons.json`) instead of many small steps. Rarely changed tuning fields are in a collapsed section | data entry flow docs |
| Advanced fields | Output-shaping parameters are only shown when the user has advanced mode on (`self.show_advanced_options`) | data entry flow docs |
| Current values | Options forms are filled with `add_suggested_values_to_schema(schema, self.config_entry.options)` | options flow docs |
| Defaults | Defaults are written into `options` when the entry is created. New options in later versions are added by a migration (`MINOR_VERSION` bump, `async_migrate_entry`), never by silently changing code defaults for existing users | config flow docs, versioning |
| Validation | Errors are reported per field (`errors["co2_end"] = "co2_range"`), `base` only for cross-field problems. Entities are checked to exist before the entry is created | rules `config-flow`, `test-before-configure` |
| Renamed or removed entities | Entity id changes of configured entities are followed automatically; removed entities raise a repair issue | `helpers/helper_integration.py` (`async_handle_source_entity_changes`) does the same for single-source helpers |

## Config flow

`ConfigFlow`, `VERSION = 1`, `MINOR_VERSION = 1`. Three short steps, the second only when needed. The entry is created with `data={}`, `options={…all fields and defaults…}`, `title=name`.

### Step `user`

| Field | Selector | Required | Default |
|---|---|---|---|
| `name` | `TextSelector` | yes | "Ventilation" |
| `actuator` | `EntitySelector(domain=["number", "fan", "select"])` | yes | – |
| `max_flow` | `NumberSelector(min=10, max=2000, step=5, unit="m³/h", mode=box)` | yes | `max` attribute of a `number` actuator if its unit is m³/h |

Validation: actuator has a state or an entity registry entry (`actuator_not_found`); `select` has at least two options (`select_options`); actuator not used by another entry (abort `already_configured`).

### Step `actuator`

Shown only if needed:

| Actuator | Fields |
|---|---|
| `number` with unit `m³/h`/`m3/h` | none (step skipped) |
| `number` with unit `%` | none (step skipped), `actuator_unit = percent` |
| `number` with another or no unit | `actuator_unit`: `SelectSelector(["flow", "percent"], translation_key="actuator_unit")` |
| `fan` | none (step skipped) |
| `select` | one `NumberSelector(m³/h)` per option, optional. Empty = option is never used. Field keys are the option texts, so the form shows them as labels; there is no translation for these keys (they come from the device). At least two mapped options with distinct flows (`select_mapping`) |

### Step `inputs`

| Field | Selector | Required |
|---|---|---|
| `co2_sensors` | `EntitySelector(domain="sensor", device_class="carbon_dioxide", multiple=True)` | no |
| `humidity_sensors` | `EntitySelector(domain="sensor", device_class="humidity", multiple=True)` | no |

At least one of the two (`base: no_inputs`). `data_description` of `humidity_sensors` says: living and sleeping rooms only, no bathrooms, no rooms with a humidifier, no extract-duct sensor.

All other options get their defaults (see [defaults](#defaults-relative-to-max-flow)). The off actuator, presence, boosts, safety and notifications are set afterwards in the options flow. This keeps the first setup short.

## Options flow

`OptionsFlowWithReload`. Step `init` is a menu (`async_show_menu`) with the steps below. Each step is one form; submitting it saves and ends the flow with `async_create_entry(data={**self.config_entry.options, **flattened_input})`. Section input arrives nested per section (`{"co2": {"co2_start": …}}`) and is flattened before saving, so stored keys do not depend on the form layout.

### `actuator`

| Section | Field | Selector | Default |
|---|---|---|---|
| – | `actuator` | `EntitySelector(domain=["number","fan","select"])` | |
| – | `max_flow` | `NumberSelector(m³/h, box)` | |
| `off` | `off_entity` | `EntitySelector(domain=["switch","input_boolean","fan"])`, optional | – |
| `off` | `off_state` | `SelectSelector(["on","off"], translation_key="off_state")` | `on` |
| `range` (collapsed) | `actuator_min`, `actuator_max` | `NumberSelector`, optional | entity attributes |

`off_state` = which state of `off_entity` means "unit stopped" (a standby switch: `on`). Ignored for `fan`.

Changing the actuator to one used by another entry: error `actuator_in_use`. Changing to a `select` or to a `number` with unknown unit continues to the step `actuator_mapping` (same fields as config step `actuator`).

### `inputs`

| Section | Field | Selector |
|---|---|---|
| `rooms` | `co2_sensors`, `humidity_sensors` | as config flow |
| `presence` | `presence_entities` | `EntitySelector(domain=["person","device_tracker","zone","binary_sensor","input_boolean"], multiple=True)` |
| `monitoring` (collapsed) | `actual_flow_sensor` | `EntitySelector(domain="sensor", device_class="volume_flow_rate")`, optional |

### `control`

| Section | Key | Selector | Default | Requirement |
|---|---|---|---|---|
| `co2` | `co2_start` | `NumberSelector(400–2000, step 10, ppm, box)` | 850 | BHV-10 |
| `co2` | `co2_end` | `NumberSelector(500–3000, step 10, ppm, box)` | 1400 | BHV-10 |
| `flows` | `min_flow` | `NumberSelector(0–max_flow, step 5, m³/h, box)` | 20 % of max | BHV-11 |
| `flows` | `fallback_flow` | same | 37.5 % of max | BHV-19 |
| `humidity` | `humidity_high_start` | `NumberSelector(30–90, step 1, %, box)` | 55 | BHV-13 |
| `humidity` | `humidity_high_end` | `NumberSelector(35–100, step 1, %, box)` | 70 | BHV-13 |
| `humidity` | `dryness_threshold` | `NumberSelector(20–60, step 1, %, box)` | 40 | BHV-12 |
| `humidity` | `dryness_hysteresis` | `NumberSelector(0–10, step 0.5, %, box)` | 2 | BHV-12 |
| `humidity` | `dryness_humidity_high_limit` | `DurationSelector` | 1 h | BHV-12, 0 = humidity-high suppressed while dry |
| `night` | `night_enabled` | `BooleanSelector` | true | BHV-14 |
| `night` | `night_start`, `night_end` | `TimeSelector` | 22:00, 07:00 | window may cross midnight |
| `night` | `night_max_flow` | `NumberSelector(m³/h)` | 62.5 % of max | |
| `night` | `night_humidity_override` | `NumberSelector(40–100, %)` | 65 | |
| `away` | `away_enabled` | `BooleanSelector` | true | BHV-15 |
| `output` (collapsed, advanced mode only) | `min_write_interval` | `DurationSelector` | 5 min | BHV-16 |
| `output` | `max_step_up`, `max_step_down` | `NumberSelector(m³/h)` | 12.5 % of max | |
| `output` | `deadband_up`, `deadband_down` | `NumberSelector(0–100, m³/h)` | 10, 20 | |
| `output` | `flow_quantum` | `NumberSelector(1–50, m³/h)` | 5 | |
| `output` | `evaluation_interval` | `DurationSelector` | 1 min | BHV-17 |
| `output` | `debounce` | `DurationSelector` | 10 s | |
| `output` | `stale_timeout` | `DurationSelector` | 30 min | BHV-18 |
| `output` | `startup_grace` | `DurationSelector` | 1 min | |

When advanced mode is off, the `output` section is not shown and its stored values are kept unchanged.

### `boosts`

| Section | Key | Selector | Default |
|---|---|---|---|
| `manual` | `boost_default_duration` | `DurationSelector` | 30 min |
| `manual` | `boost_default_level` | `NumberSelector(m³/h)` | 75 % of max |
| `manual` | `boost_max_duration` | `DurationSelector` | 4 h |
| `manual` | `pause_reminder_after` | `DurationSelector` | 8 h |
| `manual` | `off_notice_after` | `DurationSelector` | 24 h |
| `shower` | `bathroom_humidity_sensors` | `EntitySelector(sensor, humidity, multiple)` | – |
| `shower` | `shower_rise` | `NumberSelector(1–40, %)` | 8 |
| `shower` | `shower_window` | `DurationSelector` | 10 min |
| `shower` | `shower_end_offset` | `NumberSelector(0–30, %)` | 5 |
| `shower` | `shower_level` | `NumberSelector(m³/h)` | 62.5 % of max |
| `shower` | `shower_max_duration` | `DurationSelector` | 1 h |
| `cooking` | `cooking_sensors` | `EntitySelector(sensor, multiple)` | – |
| `cooking` | `cooking_higher_is_worse` | `BooleanSelector` | true |
| `cooking` | `cooking_rise` | `NumberSelector(1–500)` | 50 |
| `cooking` | `cooking_window` | `DurationSelector` | 10 min |
| `cooking` | `cooking_end_offset` | `NumberSelector(0–200)` | 15 |
| `cooking` | `cooking_trigger` | `EntitySelector(domain=["binary_sensor","sensor","switch","fan","light"])`, optional | – |
| `cooking` | `cooking_trigger_threshold` | `NumberSelector` | 100 |
| `cooking` | `cooking_trigger_run_on` | `DurationSelector` | 15 min |
| `cooking` | `cooking_level` | `NumberSelector(m³/h)` | 62.5 % of max |
| `cooking` | `cooking_max_duration` | `DurationSelector` | 90 min |
| `common` (collapsed) | `auto_boost_night_cap` | `NumberSelector(m³/h)` | 50 % of max |
| `common` | `auto_boost_cooldown` | `DurationSelector` | 10 min |

A detector is active when its sensors are set. There are no separate "enabled" switches: no sensors = feature off. The `cooking_sensors` description says: IAQ/VOC sensor in the kitchen, not directly above the hob.

### `safety`

| Key | Selector | Default |
|---|---|---|
| `restart_confirm_timeout` | `DurationSelector` | 30 min |
| `safety_when_disabled` | `BooleanSelector` | true |

The warning sources themselves are subentries, see [below](#warning-sources-config-subentries). The menu step's description links to "Add warning source" on the integration page.

### `notifications`

| Key | Selector | Default |
|---|---|---|
| `notify_services` | `SelectSelector(multiple=True, mode=list)`, options = all `notify.mobile_app_*` services when the step opens (label = service name) | [] |
| `notify_safety`, `notify_reminders`, `notify_problems` | `BooleanSelector` | true |
| `problem_delay` | `DurationSelector` | 10 min |

Without `notify_services`, a `persistent_notification` is created instead (no actions). A stored service that no longer exists raises the repair issue `missing_notify_service`.

## Readiness conditions (config subentries)

`async_get_supported_subentry_types` returns `{"readiness_condition": ReadinessConditionFlow}`. The integration page then shows "Add readiness condition", and each condition is listed with its own reconfigure and delete entries.

`ReadinessConditionFlow(ConfigSubentryFlow)`:

| Step | Fields |
|---|---|
| `user` | `entity_id`: `EntitySelector(domain=["select","input_select","switch","input_boolean","number","sensor","binary_sensor"])` |
| `state` | `expected_state`: for `select`/`input_select` a `SelectSelector` with the entity's current options; `switch`/`input_boolean`/`binary_sensor`: `SelectSelector(["on","off"])`; `number`: `NumberSelector`; otherwise `TextSelector`. `auto_correct`: `BooleanSelector`, only offered for `select`, `input_select`, `switch`, `input_boolean`, `number` |
| `reconfigure` | same two steps, pre-filled, `async_update_and_abort` |

Subentry title: "<friendly name> = <expected state>". `unique_id` of the subentry = entity id, so the same entity cannot get two conditions (`already_configured`). Adding, changing or removing a subentry reloads the entry.

Translations under `config_subentries.readiness_condition`.

## Warning sources (config subentries)

Subentry type `warning_source`, handler `WarningSourceFlow(ConfigSubentryFlow)`. The integration page shows "Add warning source"; each source is listed with reconfigure and delete. Behaviour: [03-modes.md](03-modes.md#warning-sources).

| Step | Fields |
|---|---|
| `user` | `source_entity`: `EntitySelector(domain=["binary_sensor","input_boolean","switch","sensor","select","input_select"])` **or** `source_device`: `DeviceSelector(entity={"domain": "binary_sensor"})`. Exactly one (`source_required`). Optional `name` (`TextSelector`), default = friendly name / device name |
| `trigger` | Only for `sensor`/`select`/`input_select`: `trigger`: `SelectSelector(["numeric","state_list"], translation_key="trigger")`, then `threshold` (`NumberSelector`) or `states` (`SelectSelector(multiple, custom_value=True)` with the entity's options, if it has any). For on/off domains and devices the trigger is `on_off` and the step is skipped |
| `filters` | `details`: `SelectSelector(["auto","attributes","none"], translation_key="details")`, the detected provider is shown via `description_placeholders`. Section `keywords`: `headline_keywords`, `text_keywords`, `exclude_keywords` (`TextSelector(multiple=True)`). `min_severity`: `SelectSelector(["none","minor","moderate","severe","extreme"], translation_key="severity")` |
| `reconfigure` | same steps, pre-filled, `async_update_and_abort` |

- Subentry `unique_id` = source entity id or device id (`already_configured` for duplicates).
- Subentry title = `name`.
- Adding, changing or removing a source reloads the entry.
- Translations under `config_subentries.warning_source`.

**Suggested filters**, pre-filled on creation by detected provider and `hass.config.language` (fallback `en`). The `data_description` explains why.

| Provider / language | `headline_keywords` (events) | `text_keywords` (advice) | `exclude_keywords` |
|---|---|---|---|
| `nina`, `de` | `/brand(?!enburg)/`, `Rauch`, `Geruchsbelästigung`, `Geruchsentwicklung`, `Gefahrstoff`, `Gefahrgut`, `Schadstoff`, `Gasaustritt`, `Gasleck`, `Gasgeruch`, `Gaswolke`, `Erdgas`, `Chemie`, `chemisch`, `giftig`, `Giftwolke`, `Explosion`, `radioaktiv`, `Kernkraft`, `Atom` | `Lüftung`, `Klimaanlage` | `Entwarnung`, `Probealarm`, `Probewarnung`, `Fehlauslösung`, `Warntag`, `Übung`, `wieder öffnen`, `wieder geöffnet`, `wieder eingeschaltet` |
| `nina`, other languages | `fire`, `smoke`, `odour`, `odor`, `hazardous`, `chemical`, `toxic`, `gas leak`, `explosion`, `radioactive`, `nuclear` | `ventilation`, `air conditioning` | `all clear`, `test`, `exercise`, `drill`, `can be reopened`, `switched on again` |
| `dwd_weather_warnings`, `attributes`, `environment_canada` | – | – | – |
| `none` (smoke detector, template, input_boolean, …) | not shown | not shown | not shown |

`min_severity` is `none` for all providers.

Basis for the German defaults:

- **Standard texts NRW** (Runderlass Warnung der Bevölkerung, Anlage 2 "Standardisierte Warnmeldungen", version 2020, idf.nrw.de): "Schadstofffreisetzung" → "Schalten Sie die Lüftungs- und Klimaanlagen ab."; "Rauchgase" (fire) → "… schalten Sie Klima- und Lüftungsanlagen ab."; "Geruchsbelästigung" → "Es besteht keine akute Gesundheitsgefahr" (no ventilation advice, but counts by default: the unit would pull the smell into the house; owner decision 2026-10-13); "Probealarm", "Sirenenfehlauslösung" → "Es besteht keine Gefahr"; "Entwarnung" as its own message. Kampfmittelfund, Stromausfall, Hochwasser contain no ventilation advice.
- **Live NINA feed 2026-10-13:** DWD weather warnings, drinking-water notices (one with "Chlorung" in the headline, so `Chlor` is not a keyword) and an all-clear message with "Schließen Sie vorsorglich Fenster und Türen" (so window advice alone is not a keyword).
- Not in the defaults because of false hits: `Feuer` (sender/intro "Feuerwehr"), `Gas` ("Gasse"), `Strahlung` (DWD "UV-Strahlung"), `Fenster und Türen` (also in all-clear messages), `Geruch` alone (e.g. a drinking-water notice about an unusual smell).
- Checked against 25 sample messages (NRW templates, live messages, constructed headlines) with no wrong decision; the set becomes a test fixture ([06-testing.md](06-testing.md#warning-text-fixture)).

Validation: `source_required`, `invalid_regex` (a `/…/` keyword that does not compile), `keyword_conflict` (same keyword in an include list and in `exclude_keywords`), `numeric_state` (trigger `numeric` on an entity whose current state is not numeric; warning only if the state is `unavailable`).

## Validation rules

| Rule | Field | Error key |
|---|---|---|
| actuator exists (state or registry entry) | `actuator` | `actuator_not_found` |
| actuator not used by another entry | `actuator` | abort `already_configured` (config flow), error `actuator_in_use` (options) |
| `select` has ≥ 2 options; ≥ 2 mapped options with distinct flows | `actuator` / `base` | `select_options` / `select_mapping` |
| at least one CO₂ or humidity sensor | `base` | `no_inputs` |
| `co2_start < co2_end` | `co2_end` | `co2_range` |
| `humidity_high_start < humidity_high_end` | `humidity_high_end` | `humidity_range` |
| `dryness_threshold + dryness_hysteresis < humidity_high_start` | `dryness_threshold` | `dryness_overlap` |
| `min_flow ≤ fallback_flow ≤ max_flow` | `fallback_flow` | `fallback_range` |
| `min_flow ≤ night_max_flow ≤ max_flow` | `night_max_flow` | `night_range` |
| every level and cap within `min_flow`…`max_flow` | the field | `level_range` |
| `night_start ≠ night_end` | `night_end` | `night_window` |
| `away_enabled` needs `presence_entities` (checked on the `control` step against stored inputs) | `away_enabled` | `away_needs_presence` |
| `auto_correct` only for writable domains | `auto_correct` | `not_correctable` |
| a sensor appears in more than one input group (e.g. living and bathroom humidity) | the later field | `duplicate_sensor` |

Range limits of single fields are enforced by the selectors and are not repeated as errors.

## Defaults relative to max flow

Defaults given as "x % of max flow" are calculated when the entry is created, rounded to 5 m³/h, and stored in `options`. Changing `max_flow` later does **not** change them; the `actuator` step shows a hint (`description_placeholders`) that flow values may need adjusting. For a 400 m³/h unit:

| Option | Default |
|---|---|
| `min_flow` | 80 |
| `fallback_flow` | 150 |
| `max_step_up`, `max_step_down` | 50 |
| `night_max_flow` | 250 |
| `boost_default_level` | 300 |
| `shower_level`, `cooking_level` | 250 |
| `auto_boost_night_cap` | 200 |

## Stored format

- Durations are stored as seconds (int), converted from the `DurationSelector` dict on submit and back for `add_suggested_values_to_schema`.
- Times are stored as `"HH:MM:SS"` strings (as returned by `TimeSelector`).
- Entity lists are stored as lists of entity ids.
- Option keys are constants in `const.py` (`CONF_CO2_START = "co2_start"`, …). Keys never change once released; renames go through a migration.

## Renamed and removed entities

The controller subscribes to `async_track_entity_registry_updated_event` for every configured entity (options and subentries):

- `action == "update"` with `old_entity_id`: replace the id in `options` / the subentry (`async_update_entry` / `async_update_subentry`) and reload.
- `action == "remove"`: repair issue `missing_entity` naming the entity and the option it was used in. The entry keeps running with the remaining inputs.

`async_handle_source_entity_changes` from `helpers/helper_integration.py` handles one source entity per helper and is therefore not used directly; the behavior is the same.

## Entry lifecycle

| Hook | Behavior |
|---|---|
| `async_setup` | register services and intents, `CONFIG_SCHEMA` config-entry-only |
| `async_setup_entry` | create controller, load store, forward platforms, start after HA started. Does **not** raise `ConfigEntryNotReady` for missing source entities (they may load later); missing entities are handled as invalid inputs and repairs |
| `async_unload_entry` | unload platforms, cancel all listeners and timers, flush the store |
| `async_remove_entry` | delete the store file |
| `async_migrate_entry` | migrations for `VERSION`/`MINOR_VERSION` changes, each with a test |

## Example: Brink Flair with the ESPHome package `fonske/Brink-flair-modbus`

| Setting | Value |
|---|---|
| actuator | the `number` "Modbus fan speed" entity (0–400 m³/h) |
| max flow | 400 m³/h |
| off actuator | the standby `switch`, `off_state` = `on` |
| readiness condition | `select` "Modbus Control Mode" = `Modbus Flow`, `auto_correct` on. Without it the unit ignores the flow register, and the ESPHome package resets the mode on every boot |
| not as inputs | the ENV/BME sensor in the extract duct (mixed air). Its temperature and humidity are fine for monitoring, not for control |

Entity ids depend on the label language of the package and are therefore not given here.

## Best-practice checklist

Quality scale rules that concern configuration, checked for this design. Rules without relevance (polling, discovery, authentication, websession) are omitted.

| Rule | Tier | How it is met |
|---|---|---|
| `config-flow` | Bronze | UI setup, specific selectors, `data_description` for all fields, settings in `options` |
| `config-flow-test-coverage` | Bronze | 100 % of `config_flow.py`, including every error key ([06-testing.md](06-testing.md)) |
| `unique-config-entry` | Bronze | `_async_abort_entries_match` on the actuator |
| `test-before-configure` | Bronze | actuator and sensors are checked to exist; there is no connection to test |
| `test-before-setup` | Bronze | setup succeeds without source entities by design (helper); problems become repairs |
| `runtime-data` | Bronze | `entry.runtime_data` holds the controller |
| `action-setup` | Bronze | services registered in `async_setup` |
| `has-entity-name`, `entity-unique-id` | Bronze | [04-interface.md](04-interface.md#device) |
| `config-entry-unloading` | Silver | `async_unload_entry` cancels everything |
| `docs-configuration-parameters`, `docs-installation-parameters` | Silver | README documents every field (generated table from `strings.json` is acceptable) |
| `reconfiguration-flow` | Gold | not needed: `data` is empty, everything is editable in the options flow (helper pattern). Readiness and warning source subentries have their own reconfigure step |
| `repair-issues` | Gold | `missing_entity`, `missing_notify_service`, `state_reset` |
| `exception-translations` | Gold | all `ServiceValidationError`/`HomeAssistantError` with `translation_key` |
| `entity-translations`, `icon-translations` | Gold | [04-interface.md](04-interface.md#entities) |
| `strict-typing` | Platinum | `mypy --strict` for the whole package (target) |
