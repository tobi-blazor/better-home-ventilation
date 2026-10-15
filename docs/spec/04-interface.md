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

All entities use `has_entity_name = True`, a `translation_key` and `unique_id = f"{entry_id}_{key}"`. With the device name "Lüftung" the setpoint sensor becomes `sensor.luftung_setpoint` / „Lüftung Sollwert“.

Entities are `available` as long as the controller runs; they do not become unavailable because an input failed (that is shown in attributes and the problem sensor).

## Entities

| Key | Platform | Phase | Category | Created when | Details |
|---|---|---|---|---|---|
| `setpoint` | sensor | 0.1 | – | always | flow m³/h, `device_class` volume_flow_rate, `state_class` measurement. State = setpoint after shaping (what the unit should run at). In off modes: 0 |
| `reason` | sensor | 0.1 | – | always | `device_class` enum, options = all reasons (see below) |
| `mode` | sensor | 0.1 | – | always | `device_class` enum: `automatic`, `fallback`, `boost`, `shower`, `cooking`, `off`, `safety_off`, `disabled`. State icons in `icons.json` |
| `ends_at` | sensor | 0.1 | – | always | `device_class` timestamp: end of the current boost/pause/auto boost/restart wait, otherwise `None` |
| `boost` | button | 0.1 | – | always | starts/extends boost with the duration and level entities |
| `pause` | button | 0.1 | – | always | pause with the pause duration entity |
| `resume` | button | 0.1 | – | always | `resume()` |
| `control` | switch | 0.1 | – | always | control switch (BHV-03). State kept in the store |
| `profile` | select | 0.1 | – | always | `balanced`, `air_quality`, `quiet`, `winter` (translated state names, state icons in `icons.json`), BHV-26. State kept in the store |
| `boost_duration` | select | 0.1 | config | always | `15`, `30`, `60`, `120` min (translated state names); initial value from `boost_default_duration` (nearest option). `RestoreEntity` |
| `boost_level` | number | 0.1 | config | always | m³/h, `min_flow`…`max_flow`, step `flow_quantum`, mode box. `RestoreNumber` |
| `pause_duration` | select | 0.1 | config | always | `1h`, `4h`, `8h`, `indefinite` (translated). `RestoreEntity` |
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

`boost_duration`, `boost_level` and `pause_duration` are the only "parameter" entities. They exist because they are changed in daily use from the dashboard. All other parameters are in the options flow.

### Reasons (`reason` sensor options)

`minimum`, `co2`, `humidity_high`, `dryness_protection`, `away`, `quiet_limit`, `fallback`, `boost`, `shower`, `cooking`, `off`, `safety_off`.

### Setpoint attributes

| Attribute | Content |
|---|---|
| `mode`, `reason` | as the enum sensors |
| `profile` | active profile |
| `target` | flow the active mode asks for, before shaping |
| `written` | last value written to the unit (flow) and `last_write` (timestamp) |
| `actuator_value` | current native actuator value |
| `contributions` | dict: `minimum`, `co2`, `humidity_high`, `fallback`, `boost`, `shower`, `cooking` (flow or `None`) |
| `co2_max`, `co2_max_entity` | highest valid CO₂ and its entity |
| `humidity_max`, `humidity_max_entity`, `humidity_mean` | living humidity |
| `limits` | list of active limits: `away`, `quiet`, `dry` |
| `invalid_inputs` | list of entity ids currently invalid |
| `write_blocked_by` | `None`, `grace`, `interval`, `deadband`, `disabled`, `not_ready`, `unavailable` |
| `off_method` | `off_actuator` or `minimum` (only in off modes) |
| `awaiting_restart` | bool (only in `safety_off`) |

Attributes that change on every evaluation but have no value for history (`actuator_value`, `write_blocked_by`) are excluded from the recorder via `_unrecorded_attributes`.

### Problem attributes

`problems`: list of `{key, entity_id?, since}`. Keys: `actuator_unavailable`, `not_ready`, `write_failed`, `write_mismatch`, `inputs_invalid`, `no_valid_co2`, `warning_source_unavailable`, `warning_details_failed`, `missing_entity`, `missing_notify_service`.

## Services

Registered in `async_setup` (not per entry), as required by HA for services of integrations with config entries.

All services take an optional `config_entry_id` (selector `config_entry`, integration `better_home_ventilation`). If omitted and exactly one entry exists, that entry is used; otherwise `ServiceValidationError` (`translation_key: entry_required`).

| Service | Fields | Effect |
|---|---|---|
| `boost` | `duration` (minutes, 5–720, optional), `level` (m³/h, optional) | `boost()` |
| `pause` | `duration` (minutes, 1–10080, optional; omitted = indefinite) | `pause()` |
| `resume` | – | `resume()` |
| `override_safety` | – | `override_safety()` |

Errors (all `ServiceValidationError` with translations): `entry_required`, `entry_not_loaded`, `level_out_of_range`, `control_disabled` (boost or pause while control is off; the buttons raise the same error), `no_active_warning` (override without warning).

Services return nothing.

## Events

| Event | When | Data |
|---|---|---|
| `better_home_ventilation_mode_changed` | resolved mode changed | see [03-modes.md](03-modes.md#mode-change-events) |
| `better_home_ventilation_readiness_corrected` | a readiness condition was auto-corrected | `entry_id`, `entity_id`, `from_state`, `to_state` |

## Logbook

`logbook.py` implements `async_describe_events` for `better_home_ventilation_mode_changed`:

- name: device name
- message (translated): e.g. "switched to boost (button, until 18:30)", "safety shutdown: <headline>", "safety override by user", "back to automatic (timeout)".
- `entity_id`: the `mode` sensor, so the entry shows up on the device page.

The setpoint itself is not logged per write (the history of the `setpoint` sensor covers it).

## Intents

Phase 0.2. Registered with `homeassistant.helpers.intent.async_register` in `async_setup`.

| Intent | Slots | Effect / response |
|---|---|---|
| `BetterVentilationBoost` | `name` (optional, entry title), `duration` (minutes, optional), `level` (optional) | `boost()`, speech: "Ventilation boosted to 300 m³/h until 18:30" |
| `BetterVentilationPause` | `name`, `duration` (optional) | `pause()` |
| `BetterVentilationResume` | `name` | `resume()` |
| `BetterVentilationStatus` | `name` | speech with mode, setpoint, reason, end time |

Each handler sets `description` and `slot_schema` so that LLM agents can use it, if the Assist API exposes intents of custom integrations ([Q2](README.md#open-questions)).

For the default conversation agent the repository ships example sentence files `examples/custom_sentences/{en,de}/better_home_ventilation.yaml` that users copy to `config/custom_sentences/<lang>/` ([Q3](README.md#open-questions)). Examples (de): „Lüftung Boost“, „Lüftung 30 Minuten stärker“, „Lüftung für 2 Stunden aus“, „Lüftung wieder normal“, „Was macht die Lüftung?“.

Independently of intents, all buttons and switches can be exposed to Assist like any entity.

## Notifications

Sent via the configured `notify.mobile_app_*` services (`notify.<service>` with `message`, `title`, `data`). Without configured services: `persistent_notification.create` (no actions).

| Kind | Title / message (en; de in translations) | Actions | Tag |
|---|---|---|---|
| safety | "Ventilation off: <headline>" / "<recommended actions, otherwise description; HTML stripped, max. 300 chars> (<sender>)". Without details: "Ventilation off: <source name>" | `KEEP_VENTILATING` → `override_safety()`, `OK` → nothing | `bhv_<entry8>_safety` |
| restart | "Warning lifted" / "Restart ventilation? Restarts automatically at <time>." | `RESTART` → `resume()`, `KEEP_OFF` → `keep_off()` | `bhv_<entry8>_safety` (replaces) |
| restarted | "Ventilation restarted" | – | `bhv_<entry8>_safety` |
| reminder | "Ventilation off since <time>" / "Moisture protection is inactive." | `RESUME` → `resume()` | `bhv_<entry8>_reminder` |
| problem | "Ventilation problem" / problem text | – | `bhv_<entry8>_problem_<key>` |

`<entry8>` = first 8 characters of the entry id. Action ids are `BHV_<entry8>_<ACTION>`.

- The integration listens to `mobile_app_notification_action` and maps the action id to the entry and command (`source: notification`).
- When the reason disappears (warning overridden, problem resolved), the notification is cleared with `message: clear_notification` and the tag.
- Safety notifications use `data.push.interruption-level: time-sensitive` (iOS) and `data.priority: high`, `data.ttl: 0` (Android).
- Texts are in `strings.json` under `common` (e.g. `common.notify_safety_title`, with placeholders like `{headline}`). hassfest accepts a `common` category for integrations (`script/hassfest/translations.py`, HA `main` 2026-10-13). They are loaded with `helpers.translation.async_get_translations(hass, hass.config.language, "common", {DOMAIN})`, fallback `en` (works for custom integrations, [Q7](README.md#open-questions)).

## Repairs

| Issue id | When | Fix |
|---|---|---|
| `missing_entity_<entity_id>` | a configured entity is not in the state machine and not in the entity registry 5 min after startup | text points to the options flow |
| `missing_notify_service_<service>` | a configured notify service no longer exists | text points to the options step `notifications` |
| `state_reset` | the store is corrupt or from an unknown version | informational, dismissable |

## Diagnostics

`diagnostics.py` returns: `entry.options`, all subentries, the persisted controller state, current states of all configured entities (state, `last_reported`), the last 100 decisions, active problems. No redaction needed (no secrets), but person/device_tracker states are redacted to `home`/`not_home`.
