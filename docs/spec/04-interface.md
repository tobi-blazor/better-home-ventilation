# 04 Interface

## Device

One device per config entry:

| Field | Value |
|---|---|
| identifiers | `(DOMAIN, entry_id)` |
| name | entry title (e.g. "Ventilation") |
| manufacturer | "Better Home Ventilation" |
| model | "Demand controller" |
| entry_type | `DeviceEntryType.SERVICE` |
| sw_version | integration version |

The device has its own identity. It never copies identifiers or connections of the actuator's device: in HA's single-config-entry device model a helper that does so ends up owning a duplicate device (`helpers/helper_integration.py`, `async_remove_helper_devices`). Linking the entities to the unit's device instead is backlog, because a ventilation unit often has dozens of entities and the controller entities would get lost among them.

All entities use `has_entity_name = True`, a `translation_key` and `unique_id = f"{entry_id}_{key}"`. HA derives the entity id from the translated name in the server language (for every language in `homeassistant.generated.languages.NATIVE_ENTITY_IDS`, which includes `en` and `de`; HA 2026.4 and 2026.10): with the device name "Lüftung" and German as server language the setpoint sensor becomes `sensor.luftung_sollwert` / „Lüftung Sollwert“, with English `sensor.luftung_setpoint` / "Lüftung Setpoint".

Entities are `available` as long as the controller runs; they do not become unavailable because an input failed (that is shown in attributes and the problem sensor).

## Entities

| Key | Platform | Phase | Category | Created when | Details |
|---|---|---|---|---|---|
| `setpoint` | sensor | 0.1 | – | always | flow m³/h, `device_class` volume_flow_rate, `state_class` measurement. State = setpoint after shaping (what the unit should run at): the written value, or the flow shaping holds while nothing is written (also during the startup grace, while the actuator is unavailable or not ready, and while a write awaits its confirmation), see [02-control.md](02-control.md#output-shaping). In off modes: 0. In `disabled`: the demand (shadow run). One state per evaluation |
| `reason` | sensor | 0.1 | – | always | `device_class` enum, options = all reasons (see below) |
| `mode` | sensor | 0.1 | – | always | `device_class` enum: `automatic`, `fallback`, `boost`, `shower`, `cooking`, `off`, `safety_off`, `disabled`. State icons in `icons.json` |
| `ends_at` | sensor | 0.1 | – | always | `device_class` timestamp: end of the current boost/pause/auto boost/restart wait, otherwise `None` |
| `boost` | button | 0.1 | – | always | starts/extends boost with the duration and level entities |
| `pause` | button | 0.1 | – | always | pause with the pause duration entity |
| `resume` | button | 0.1 | – | always | `resume()` |
| `control` | switch | 0.1 | – | always | control switch (BHV-03). State kept in the store |
| `profile` | select | 0.1 | – | always | `balanced`, `air_quality`, `quiet`, `winter` (translated state names, state icons in `icons.json`), BHV-26. State kept in the store |
| `boost_duration` | select | 0.1 | config | always | `15`, `30`, `60`, `120` min (translated state names); initial value from `boost_default_duration` (nearest option). `RestoreEntity` |
| `boost_level` | number | 0.1 | config | always | m³/h, `min_flow`…`max_flow`, step `flow_quantum`, mode box. `RestoreNumber`; initial value `boost_default_level`; a restored value outside the current range is clamped |
| `pause_duration` | select | 0.1 | config | always | `1h`, `4h`, `8h`, `indefinite` (translated). `RestoreEntity`; initial value `1h` (decided 2026-10-16 for M5: a finite pause is the safe default for moisture protection) |
| `problem` | binary_sensor | 0.1 | diagnostic | always | `device_class` problem, on if any problem persists longer than `problem_delay` |
| `safety_warning` | binary_sensor | 0.2 | – | at least one `warning_source` subentry | `device_class` safety, on while any warning is active (overridden or not). Attribute `warnings`: list of `{source, headline, severity, overridden, since}` |
| `safety_override` | switch | 0.2 | – | at least one `warning_source` subentry | on = active warnings are overridden. Turning on = `override_safety()`; turning off = remove overrides (shuts down again if a warning is active). Without an active warning, turning on has no effect and the switch returns to off |
| `shower_detected` | binary_sensor | 0.3 | – | `bathroom_humidity_sensors` set | no device class, icon `mdi:shower`. On while a shower auto boost is active |
| `cooking_detected` | binary_sensor | 0.3 | – | `cooking_sensors` or `cooking_trigger` set | icon `mdi:stove`. On while a cooking auto boost is active |

Rules applied (quality scale `entity-category`, `entity-device-class`, `entity-translations`, `icon-translations`, `has-entity-name`):

- **Category:** the three parameter entities are `EntityCategory.CONFIG` (they set how the buttons behave), the problem sensor is `EntityCategory.DIAGNOSTIC`. Controls and states used in daily operation have no category.
- **Only what is configured:** entities of features without inputs are not created. When the feature is configured later, the reload creates them; when it is removed, the entities are removed from the entity registry during setup (`er.async_entries_for_config_entry` minus the expected unique ids).
- **Names and icons** come from `strings.json` (`entity.<platform>.<key>.name`, state names) and `icons.json` (default and per state). No `_attr_name`, no `_attr_icon` in code.
- **Device classes and units** where HA has them (`volume_flow_rate`, `enum`, `timestamp`, `problem`, `safety`).
- **No entity disabled by default:** every created entity is useful in the UI. Revisit if users report noise.
- `PARALLEL_UPDATES = 0` in every platform (push-based, no polling; buttons/switches only call the controller).

`boost_duration`, `boost_level` and `pause_duration` are the only "parameter" entities. They exist because they are changed in daily use from the dashboard. All other parameters are in the options flow. Their values live in the controller at runtime (not in the store); the restored entity state is the persistence. A restored option that no longer exists falls back to the initial value.

Commands from the buttons, the control switch and the profile select carry `source: button` (the command sources have no separate value for switches and selects).

### Reasons (`reason` sensor options)

`minimum`, `co2`, `humidity_high`, `co2_and_humidity_high`, `dryness_protection`, `away`, `quiet_limit`, `fallback`, `boost`, `shower`, `cooking`, `off`, `safety_off`.

`co2_and_humidity_high` (en "CO₂ and humidity", de „CO₂ und Feuchte“, icon `mdi:cloud-percent`): CO₂ and humidity-high ask for about the same flow ([02-control.md](02-control.md#reason)).

### Setpoint attributes

| Attribute | Content |
|---|---|
| `mode`, `reason` | as the enum sensors |
| `reasons` | list of the single reasons behind `reason`: `[co2, humidity_high]` for `co2_and_humidity_high`, otherwise `[reason]` |
| `profile` | active profile |
| `target` | flow the active mode asks for, before shaping |
| `written`, `last_write` | last value written to the unit (flow) and its time (timestamp) |
| `actuator_value` | current native actuator value |
| `contributions` | dict: `minimum`, `co2`, `humidity_high`, `fallback`, `boost`, `shower`, `cooking` (flow or `None`) |
| `co2_max`, `co2_max_entity` | highest valid CO₂ and its entity |
| `humidity_max`, `humidity_max_entity`, `humidity_mean` | living humidity |
| `limits` | list of active limits: `away`, `quiet`, `dry` |
| `invalid_inputs` | list of entity ids currently invalid |
| `write_blocked_by` | `None`, `grace`, `interval`, `deadband`, `disabled`, `not_ready`, `unavailable` |
| `off_method` | `off_actuator` or `minimum` (only in off modes) |
| `awaiting_restart` | bool (only in `safety_off`; phase 0.2) |

Attributes that change on every evaluation but have no value for history (`actuator_value`, `write_blocked_by`) are excluded from the recorder via `_unrecorded_attributes`.

### Problem attributes

`problems`: list of `{key, entity_id?, since}` of the problems that persist longer than `problem_delay` (the ones that turn the sensor on); `entity_id` is omitted for problems without an entity. Keys: `actuator_unavailable`, `not_ready`, `write_failed`, `write_mismatch`, `inputs_invalid`, `no_valid_co2`, `warning_source_unavailable`, `warning_details_failed`, `missing_entity`, `missing_notify_service`. `missing_entity` has the id of the missing entity (the device id for a warning source device).

## Services

Registered in `async_setup` (not per entry), as required by HA for services of integrations with config entries.

All services take an optional `config_entry_id` (selector `config_entry`, integration `better_home_ventilation`). If omitted and exactly one entry exists, that entry is used; otherwise `ServiceValidationError` (`translation_key: entry_required`). Disabled entries do not count. An id that is unknown or belongs to another integration raises `entry_not_found`; an entry that is not loaded raises `entry_not_loaded`.

| Service | Fields | Effect |
|---|---|---|
| `boost` | `duration` (minutes, 5–720, optional), `level` (m³/h, optional) | `boost()` |
| `pause` | `duration` (minutes, 1–10080, optional; omitted = indefinite) | `pause()` |
| `resume` | – | `resume()` |
| `override_safety` | – | `override_safety()` |

Errors (all `ServiceValidationError` with translations): `entry_required`, `entry_not_found`, `entry_not_loaded`, `level_out_of_range`, `control_disabled` (boost or pause while control is off; the buttons raise the same error), `no_active_warning` (override without warning).

Services return nothing.

## Events

| Event | When | Data |
|---|---|---|
| `better_home_ventilation_mode_changed` | resolved mode changed | see [03-modes.md](03-modes.md#mode-change-events) |
| `better_home_ventilation_readiness_corrected` | a readiness condition was auto-corrected | `entry_id`, `entity_id`, `from_state`, `to_state` |

## Logbook

`logbook.py` implements `async_describe_events` for `better_home_ventilation_mode_changed`:

- name: device name (a name given by the user wins), otherwise `name` from the event
- message (translated): e.g. "switched to Boost (button, until 18:30)", "safety shutdown: <headline>", "safety override by user", "back to Automatic (timeout)". The details are the command source for `trigger: command`, otherwise the trigger; "until" comes from `ends_at` of the event (with the date if it is not the day of the event).
- `entity_id`: the `mode` sensor, so the entry shows up on the device page.

Translation (decided 2026-10-16 for M5): the logbook has no translation support for descriptions, and the describe callback is synchronous (`homeassistant.components.logbook`, HA 2026.4 and 2026.10; none of the core logbook platforms translates its message). The message is built from the integration's cached backend translations in the server language (`hass.config.language`, English as fallback): sentence templates, sources and triggers from `common` (`logbook_switched_to`, `logbook_back_to`, `logbook_until`, `logbook_source_<source>`, `logbook_trigger_<trigger>`), mode names from the state translations of the `mode` sensor. The text is therefore in the server language, not in each user's frontend language.

The setpoint itself is not logged per write (the history of the `setpoint` sensor covers it).

## Intents

Phase 0.2. Registered with `homeassistant.helpers.intent.async_register` in `async_setup`.

| Intent | Slots | Effect / response |
|---|---|---|
| `BetterVentilationBoost` | `name` (optional, entry title), `duration` (minutes, optional), `level` (optional) | `boost()`, speech: "Ventilation boosted to 300 m³/h until 18:30" |
| `BetterVentilationPause` | `name`, `duration` (optional) | `pause()` |
| `BetterVentilationResume` | `name` | `resume()` |
| `BetterVentilationStatus` | `name` | speech with mode, setpoint, reason, end time |

Each handler sets `description` and `slot_schema` so that LLM agents can use it. The Assist API offers custom intents to LLM agents ([Q2](README.md#open-questions)): up to HA 2026.4 by itself, from HA 2026.10 through the integration's `llm` platform (`llm.py`), which returns the four intents as `IntentTool`s named `better_home_ventilation__<intent>`.

For the default conversation agent the repository ships example sentence files `examples/custom_sentences/{en,de}/better_home_ventilation.yaml` that users copy to `config/custom_sentences/<lang>/` (a custom integration cannot add sentences itself, [Q3](README.md#open-questions)). Examples (de): „Lüftung Boost“, „Lüftung 30 Minuten stärker“, „Lüftung für 2 Stunden aus“, „Lüftung wieder normal“, „Was macht die Lüftung?“.

Details (decided 2026-10-29 for M10):

- **Registration:** `intent.py` is also the integration's `intent` platform: the `intent` integration calls its `async_setup_intents` for every loaded integration (`homeassistant.components.intent`, `_async_process_intent`, HA 2026.4 and 2026.10). The same function is called in `async_setup`; it skips handlers that are registered already, so the intents exist with and without the `intent` integration and are never overwritten.
- **Slots:** `duration` in minutes, 5–720 for boost and 1–10080 for pause (as the services); `level` in m³/h (range checked by the engine, `level_out_of_range`). `name` is compared with the entry titles, ignoring case and blanks; without `name` exactly one enabled entry must exist. Commands carry `source: voice`; boost without slots uses the parameter entities like the button.
- **Speech:** in the language of the request (its base language, `de-CH` → `de`; English as fallback) from `common` (`intent_boost`, `intent_pause`, `intent_pause_indefinite`, `intent_resume`, `intent_status`, `intent_status_until`, `intent_status_not_started`); mode and reason names from the state translations of the `mode` and `reason` sensors. Status is a `query_answer` response, the others `action_done`. Flows are spoken without decimals, times as `HH:MM` (with the date if not today).
- **Errors:** `IntentHandleError` with the translated text `common.intent_error_<code>` and `response_key` `better_home_ventilation_<code>`; codes `control_disabled`, `level_out_of_range`, `entry_required`, `entry_not_found`, `no_entry`, `entry_not_loaded`. LLM agents get the text (`error_text` of the tool result). The default agent speaks the error response of the key, so the example sentence files define `responses.errors` for every code.
- **LLM tools (2026.10):** offered for the Assist API only and only while at least one entry is loaded, with titles and annotations (all `open_world: false`, not destructive; status `read_only`; pause and resume idempotent). They do not depend on exposed entities, like the intents in 2026.4 (`platforms` is None).
- SPEC-GAP: the example sentence files are checked by a test against the intents, slots, lists and error codes, but not parsed by hassil (the `conversation` requirements are not installed in the test environment). The hour sentences use a range list with `multiplier: 60`; whether the default agent matches every sentence (and how they rank against built-in sentences such as „Lüftung aus“ for an entity named „Lüftung“) is to be checked on the real installation.

Independently of intents, all buttons and switches can be exposed to Assist like any entity.

## Notifications

Sent via the configured `notify.mobile_app_*` services (`notify.<service>` with `message`, `title`, `data`). Without configured services: a persistent notification (`persistent_notification.async_create`, the function behind the `persistent_notification.create` action; no actions), with the tag as notification id.

| Kind | Title / message (en; de in translations) | Actions | Tag |
|---|---|---|---|
| safety | "Ventilation off: <headline>" / "<recommended actions, otherwise description; HTML stripped, max. 300 chars> (<sender>)". Without details: "Ventilation off: <source name>" / "<source name> reports a warning. The ventilation was switched off." | `KEEP_VENTILATING` → `override_safety()`, `OK` → nothing | `bhv_<entry8>_safety` |
| restart | "Warning lifted" / "Restart ventilation? Restarts automatically at <time>." | `RESTART` → `resume()`, `KEEP_OFF` → `keep_off()` | `bhv_<entry8>_safety` (replaces) |
| restarted | "Ventilation restarted" / "The warning has ended; the ventilation runs again." | – | `bhv_<entry8>_safety` |
| reminder | "Ventilation off since <time>" / "Moisture protection is inactive." | `RESUME` → `resume()` | `bhv_<entry8>_reminder` |
| problem | "Ventilation problem" / problem text (`common.notify_problem_<key>`, with the friendly names of the problem's entities) | – | `bhv_<entry8>_problem_<key>` |

`<entry8>` = first 8 characters of the entry id. Action ids are `BHV_<entry8>_<ACTION>`. `<time>` is `HH:MM` in local time, with the date if it is not today.

- The integration listens to `mobile_app_notification_action` and maps the action id to the entry and command (`source: notification`). Action ids of other entries and unknown actions are ignored; a command the engine rejects (e.g. "Keep ventilating" after the warning was already lifted) is ignored as well (logged at debug level).
- When the reason disappears (warning overridden, problem resolved), the notification is cleared with `message: clear_notification` and the tag; a persistent notification with the tag is dismissed.
- Safety notifications use `data.push.interruption-level: time-sensitive` (iOS) and `data.priority: high`, `data.ttl: 0` (Android), the keys of the mobile_app timer notifications (`mobile_app/timers.py`, HA 2026.10.1).
- Texts are in `strings.json` under `common` (e.g. `common.notify_safety_title`, with placeholders like `{headline}`). hassfest accepts a `common` category for integrations (`script/hassfest/translations.py`, HA `main` 2026-10-13). They are loaded with `helpers.translation.async_get_translations(hass, hass.config.language, "common", {DOMAIN})`, fallback `en` (works for custom integrations, [Q7](README.md#open-questions)).

Details (decided 2026-10-29 for M9):

- **Options:** the `notify_*` options switch the kinds: `notify_safety` covers safety, restart and restarted, `notify_reminders` the reminder, `notify_problems` the problem notifications. A clear is sent only for an enabled kind. The priority data is sent with safety and restart (the two that ask for an answer), not with restarted.
- **Targets:** every configured service that exists gets the notification. If services are configured but none of them exists, the persistent notification is used, so a safety shutdown is never silent.
- **Problems:** one notification per problem key, sent when a problem of the key first persists longer than `problem_delay`, not repeated while it persists, cleared when no problem of the key is left. Problems are decisions of the engine; the glue sends them after each evaluation.
- **Never blocking:** each notification runs in its own task after the previous one (so a clear never overtakes its notification); each service call has a timeout of 10 s; a failing service is logged once (warning) until it works again (info).
- SPEC-GAP: which problem notifications were sent is not persisted. After a restart a problem resolved while Home Assistant was down leaves its notification on the phone, and a problem that persists is notified again after `problem_delay`.
- SPEC-GAP: two entries whose ids share the first 8 characters (entry ids are ULIDs whose first 8 characters are a timestamp of about 0.25 s resolution) both react to the same action id.

## Repairs

| Issue id | When | Fix |
|---|---|---|
| `missing_entity_<entity_id>` | a configured entity (options, readiness condition, warning source entity) is not in the state machine and not in the entity registry 5 min after Home Assistant started, or is removed from the entity registry; a warning source device that is not in the device registry (id = device id); one issue per entity, listing every loaded entry that uses it and where (options step or subentry); removed when the entity has a state or a registry entry again or no loaded entry uses it; also the problem `missing_entity` | text points to the options flow |
| `missing_notify_service_<service>` | a configured notify service no longer exists (checked when the controller starts and whenever a `notify` service is registered or removed); one issue per service, listing every loaded entry that misses it, removed when the service exists again or no loaded entry uses it; also the problem `missing_notify_service` (implemented in M9) | text points to the options step `notifications` |
| `state_reset_<entry_id>` | the store is corrupt or from an unknown version | informational, dismissable |

Details (decided 2026-10-29 for M10):

- **Module:** `issues.py`, not `repairs.py` (the module name of an integration's repairs platform with fix flows).
- **`missing_entity`:** the wait counts from the start of Home Assistant, not of the entry: an entry loaded or reloaded later checks at once, and an entry reloaded after an options change keeps the issues of entities that are still missing. Disabling or removing an entry drops its usages. The issue is not fixable (no fix flow; the options flow is the fix); severity warning. The usages name the entry and the place in the server language (`common.issue_where_<key>`, e.g. "Ventilation: CO₂ sensors (step Inputs)"), because issue placeholders are not translated by the frontend. The problem `missing_entity` has the entity id (the device id for a warning source device) and is raised at once (no startup grace; the issue already waited).
- **`state_reset`:** one issue per entry (`state_reset_<entry_id>`, two entries can reset independently). Not fixable and not persistent (`is_persistent=False`): the user ignores it in the repairs dialog, and it is gone after the next restart. Removed with the entry.
- SPEC-GAP: an entity without a registry entry (e.g. a YAML template sensor without `unique_id`) whose state is removed while Home Assistant runs is found only at the next start: removing a state is no proof that the entity is gone (reloads remove and add states).

## Diagnostics

`diagnostics.py` returns: `entry.options`, all subentries, the persisted controller state, current states of all configured entities (state, `last_reported`), the last 100 decisions, active problems. No redaction needed (no secrets), but person/device_tracker states are redacted to `home`/`not_home`.

Details (decided 2026-10-29 for M10):

- **Layout:** `entry` (title, version, minor version, options), `subentries` (id, type, title, unique id, data), `controller` (started, `state_reset`, missing entities, number of missing notify services), `stored_state` (the persisted part of the controller state, as in the store), `entities` (per entity read by the controller, warning source devices expanded: `state`, `last_changed`, `last_reported`; `None` for an entity without state), `problems` and `warnings` (active warnings, overridden incident keys, `awaiting_restart`) of the last decision, `decisions` (oldest first; dataclasses as dicts, datetimes ISO 8601, durations in seconds, sets as sorted lists).
- **Redaction:** `async_redact_data` with `notify_services`: the service names carry phone (and often person) names. Entity attributes are not included at all (person and device_tracker attributes hold coordinates). Person/device_tracker states other than `home`, `unavailable` and `unknown` become `not_home` (zone names are locations).
- The download is also an input of the replay tool ([06-testing.md](06-testing.md#replay)).
