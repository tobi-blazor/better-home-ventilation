# 03 Modes

Exactly one mode is active at any time. It is resolved in every evaluation from the controller state; it is never stored as such (only its inputs are stored), so the mode cannot get out of sync with timers or warnings.

## Controller state

```text
ControllerState
  control_enabled: bool                       # control switch, default true
  profile: str                                # balanced | air_quality | quiet | winter, default balanced
  manual: None | Boost(until, level, started, source) | Pause(until | None, started, source)
  auto_boosts: {detector_key: AutoBoost(kind, entity_id, baseline, started, deadline)}
  detector_cooldown: {detector_key: until}
  safety:
    active: {warning_key: WarningInfo(source_id, source_name, entity_id, incident_key, headline, text,
                                      recommended_actions, severity, sender, since, details)}
    overridden: set[incident_key]
    restart_pending: None | RestartPending(deadline)
  safety_restore: None | SafetyRestore(turn_on, flow)   # safety_when_disabled, see below
  dry_humidity_high_since: None | datetime    # dryness counter, see 02-control
  dry_active: bool
  off_since: None | datetime                  # start of the current off state
  last_reminder: None | datetime
  stopped_by_integration: bool                # off actuator stopped by an integration write, see 02-control "Off and on"
  last_write: None | datetime
  last_written_flow: None | float
  last_mode: str                              # for change detection and events only
```

## Mode resolution

Highest priority first:

| Prio | Mode | Condition |
|---|---|---|
| 1 | `safety_off` | any warning in `safety.active` whose incident key is not in `safety.overridden`, **or** `safety.restart_pending` is set. Applies even when `control_enabled` is false if `safety_when_disabled` |
| 2 | `disabled` | `control_enabled` is false |
| 3 | `off` | `manual` is `Pause` |
| 4 | `boost` | `manual` is `Boost` |
| 5 | `shower` / `cooking` | any entry in `auto_boosts`; if both, the one with the higher level (tie: `cooking`) |
| 6 | `fallback` | CO₂ sensors configured and none valid (see [02-control.md](02-control.md#combining)) |
| 7 | `automatic` | otherwise |

Between `off` and `boost` the last command wins, because both live in the single `manual` slot.

**Safety while control is disabled** (`safety_when_disabled`): the integration only switches the unit off (off actuator, or lowest value) and, after the restart confirmation, back on to the state it had before. It writes no setpoint and no readiness correction while control is disabled.

"The state it had before" (decided 2026-10-29 for M7): when the safety off stops the unit while control is disabled, the engine remembers it in `safety_restore`. With an off actuator: if the unit was running, it is switched on again (`TurnOn`); a unit that was already stopped stays stopped. Without off actuator: the flow the actuator showed before the lowest value is written again. The restore waits for availability and readiness like any write; `keep_off` and switching control on drop it (control on switches the unit on through `stopped_by_integration` anyway). `safety_when_disabled` false: while control is disabled warnings are tracked (entities show them) but nothing is switched, no notification is sent and no restart wait starts. SPEC-GAP: whether a notification ("warning active, control disabled") should be sent in this case is open.

`not_ready` and `actuator_unavailable` are **problems**, not modes: the mode stays, writes are suspended (`write_blocked_by`).

## Target per mode

`demand` = automatic demand after limits ([02-control.md](02-control.md#limits)). The target is shaped into the setpoint ([02-control.md](02-control.md#output-shaping)).

| Mode | Target | Reason | Quiet limit | Shaping |
|---|---|---|---|---|
| `safety_off` | off | `safety_off` | – | none |
| `disabled` | `demand` (shown, not written) | reason of `demand` | as `demand` | – |
| `off` | off | `off` | – | none |
| `boost` | `max(level, demand_unquiet)` | `boost` (or reason of `demand_unquiet` if it is higher than `level`) | ignored: uses the demand before the quiet limit | bypass on every boost command and on end |
| `shower`, `cooking` | `max(min(level, cap_if_quiet), demand)` | `shower` / `cooking` (or reason of `demand` if higher) | `auto_boost_quiet_cap` instead of `quiet_max_flow`, when the profile applies it | bypass on start/end |
| `fallback` | `demand` (already contains `fallback_flow`) | `fallback` or `humidity_high` | applies | normal |
| `automatic` | `demand` | reason of `demand` | applies | normal |

## Commands

Commands come from buttons, services, intents, notification actions and the unit itself (external stop/start of the off actuator; a late confirmation of the integration's own stop or start is not one, [02-control.md](02-control.md#writing)). Each command carries a `source` (`button`, `service`, `voice`, `notification`, `external`) for the logbook.

| Command | Effect |
|---|---|
| `boost(duration?, level?)` | If `manual` is `Boost`: `until = min(max(until, now) + duration, now + boost_max_duration)`, `level` replaced if given. Otherwise `manual = Boost(min(now + duration, now + boost_max_duration), level or boost level entity)`. Replaces a `Pause`. `duration` default: boost duration entity |
| `pause(duration?)` | `manual = Pause(now + duration)` or `Pause(None)` (indefinite). Replaces a `Boost` |
| `resume()` | `manual = None`, all `auto_boosts` cleared (detectors enter cooldown), `restart_pending` cleared (= confirm restart). Does **not** lift an active warning: that is `override_safety` |
| `override_safety()` | the incident keys of all warnings in `safety.active` are added to `safety.overridden`; `restart_pending` cleared; safety notifications cleared. Without an active warning and without `restart_pending` it is rejected (`no_active_warning`); with only `restart_pending` it ends the restart wait (decided 2026-10-29) |
| remove overrides | `safety.overridden` cleared (the `safety_override` switch turned off); shuts down again if a warning is active |
| `keep_off()` | only valid while `restart_pending`: `restart_pending` cleared, `manual = Pause(None)`. Outside the restart wait (e.g. a late notification action) it does nothing |
| control switch on/off | `control_enabled`. Switching off ends a `Boost` or `Pause` (`manual = None`) and clears `auto_boosts` (detectors enter cooldown) |
| profile change | `profile`; accepted at any time, also while control is disabled |

While `control_enabled` is false, `boost` and `pause` are **rejected**: services, buttons and intents raise `ServiceValidationError` (`control_disabled`), and external off actuator changes are ignored. `resume` has nothing to end and does nothing. Exceptions: `override_safety` always acts; `keep_off` during `restart_pending` only clears `restart_pending`, so the unit is not switched back on; `resume` during `restart_pending` confirms the restart (the `safety_when_disabled` restart, decided 2026-10-29); an external start during `safety_off` is an override ([Q11](README.md#open-questions), see [Flow](#flow)).

Timer expiry (`Boost.until`, `Pause.until`, `AutoBoost.deadline`, `RestartPending.deadline`) is scheduled with `async_track_point_in_utc_time` and re-scheduled whenever the state changes. Expiry runs an evaluation with trigger `timeout`.

## Safety shutdown

### Warning sources

Warning sources are **config subentries** of type `warning_source` ([01-configuration.md](01-configuration.md#warning-sources-config-subentries)). Each source has its own trigger and its own filters, so a smoke detector, a NINA region and a weather warning sensor can be combined without sharing one keyword list.

The design separates three questions:

| Question | Part | Where |
|---|---|---|
| Is the source active? | **trigger** | engine, from the state |
| What are the warnings behind it? | **details provider** | glue (may call services or read attributes), returns normalised `WarningDetails` |
| Does a warning count? | **filters** | engine, on `WarningDetails` |

#### Triggers

| Trigger | For | Source is active when |
|---|---|---|
| `on_off` | `binary_sensor`, `input_boolean`, `switch`, or a device (all its enabled `binary_sensor` entities) | any entity is `on` |
| `numeric` | `sensor` with a numeric state (e.g. a warning level or count) | state ≥ `threshold` |
| `state_list` | `sensor`, `select`, `input_select` with text states (e.g. a colour level) | state is one of `states` (case-insensitive) |

`unavailable`/`unknown` never make a source active, but raise problem `warning_source_unavailable` after `problem_delay` (one problem per subentry, named after its first unavailable entity; like the input problems it is not raised during the startup grace, decided 2026-10-29).

A **device source** is expanded by the glue to all enabled `binary_sensor` entities of the device (sorted by entity id). The controller listens to entity registry changes and rebuilds the sources in place when an entity of a source device is added, removed, disabled or renamed (e.g. a new NINA message slot); no reload is needed, because the entities of the entry do not change (decided 2026-10-29 for M8). State changes of every source entity trigger an immediate evaluation (no debounce). SPEC-GAP: a source device without any enabled `binary_sensor` (all removed or disabled) has no entities, is never active and raises no problem.

**Indoor gas detectors are not a recommended source.** With gas inside the house the advice is not to operate electrical equipment, to open windows and to leave the building; switching the unit does not help and is itself switching. The user README says so. Indoor smoke and CO detectors are good sources (smoke must not be spread through the ducts; CO near a fireplace).

Everything else is covered by HA's own helpers: a **template** or **threshold** `binary_sensor` (e.g. outdoor PM2.5 above a limit, wind from the direction of an industrial area), a **group** of smoke detectors, or an **`input_boolean`** as a manual "danger" switch, all with trigger `on_off`.

#### Details providers

A provider turns an active source into a list of zero or more `WarningDetails`:

```text
WarningDetails(key, headline, text, actions, severity, sender)
  key: the provider's identity of the warning (NINA/CAP id, DWD headline + ":" + start) or None
  severity: "minor" | "moderate" | "severe" | "extreme" | None
```

The incident and the source name are not part of `WarningDetails`: the engine derives the incident key and knows the source (decided 2026-10-29 for M7). Providers build `WarningDetails` with `safety.make_details`, which strips HTML and normalises the severity. An active entity for which the provider returns no warnings (None or an empty list) counts as one warning without details (fail-safe).

The provider is chosen automatically from the entity registry `platform` of the source entity. The subentry can force `attributes` or `none` instead (option `details`: `auto` / `attributes` / `none`).

| Provider | Selected for | How | Warnings per entity |
|---|---|---|---|
| `nina` | platform `nina` | service `nina.get_details` (see below) | 1 |
| `dwd_weather_warnings` | platform `dwd_weather_warnings` | indexed attributes `warning_count`, `warning_<i>_headline`, `_description`, `_instruction`, `_level`, `_name`, `_type`, `_start`, `_end` | 0…n |
| `environment_canada` | platform `environment_canada` (alert sensors) | indexed attributes `alert_<i>` (title only) | 0…n |
| `attributes` | any other entity (fallback) | single-warning attributes, first present name wins: headline `headline`/`title`/`event`/`name`; text `description`/`text`/`message`; actions `instruction`/`recommended_actions`; severity `severity`/`level`/`awareness_level`; id `id`/`identifier` | 0…1 |
| `none` | when forced, or when `attributes` finds neither headline nor text | – | the source counts as one warning without details |

Severity normalisation: CAP words (`Minor`, `Moderate`, `Severe`, `Extreme`, any case); DWD levels `1`→minor, `2`→moderate, `3`→severe, `4`→extreme (`0` = no warning); Meteoalarm awareness levels `2`/yellow→moderate, `3`/orange→severe, `4`/red→extreme (parsed from strings like `"3; orange; Severe"`, unconfirmed format). Anything else → `None`, which the severity filter treats as `extreme` (fail-safe).

Providers strip HTML and unescape entities in all text fields.

**Extensibility:** providers live in `warning_providers/` with a registry `{platform: provider}`. A provider is one class with `async_get_details(hass, entity_id, state) -> list[WarningDetails]`. Support for another integration is a small PR with a test based on a recorded state. The contract is documented in the README. Until then, users fall back to `attributes`, `none` or a template `binary_sensor`.

**Calling the providers** (decided 2026-10-29 for M8): before each evaluation the glue asks the provider of every *active* source entity (trigger active, `details` not `none`) for its details, all concurrently. Each call must finish within 10 s (`DETAILS_TIMEOUT`); the glue cancels it shortly after. A call that raises `DetailsError`, times out or raises any other exception counts as failed: the entity is listed in `Snapshot.details_failed` (one warning without details, problem `warning_details_failed`). The evaluation therefore waits at most about 10 s for details, so that a NINA all-clear message is filtered before the unit is switched off instead of shutting down for a moment without details. Provider instances live as long as the controller (the NINA cache survives evaluations). Keys: DWD `headline:start`; Environment Canada `title:time` (or the title alone); `attributes` the `id`/`identifier`; DWD entries with level `0` are skipped.

Facts per integration (HA core `main`, checked on 2026-10-13; NINA, DWD, Environment Canada and Meteoalarm checked again in the sources of 2026.4.0 and 2026.10.1 on 2026-10-29):

- **NINA** (`binary_sensor`, `device_class: safety`, one per message slot): slots are positions in the list of current warnings, so a warning can move from slot 2 to slot 1. The attributes `headline`, `description`, `sender`, `severity`, `recommended_actions`, `affected_areas`, `web`, `sent`, `start`, `expires` are **deprecated and removed in HA 2026.11**; only `id` stays. The provider calls `nina.get_details` (`SupportsResponse.ONLY`, target = the binary_sensor, `return_response=True`) on every off→on transition and when `id` changes, cached per `id`. NINA polls every 5 min. Its feed also contains DWD weather warnings, drinking-water notices and **all-clear messages** (e.g. "Entwarnung: Feuerwehreinsatz – …", category Fire, severity Minor, instruction "… Schließen Sie vorsorglich Fenster und Türen."; live NINA API, 2026-10-13). Fire warnings can be Minor. If the service call fails or times out (10 s), the source counts as one warning without details (fail-safe) and problem `warning_details_failed` is raised.
  - The service is an entity service (`service.async_register_platform_entity_service`, `func="get_details"`); its response is keyed by entity id: `{entity_id: {headline, description, sender, severity, recommended_actions, affected_areas, web, id, sent, start, expires}}` (`nina/services.py`, `nina/binary_sensor.py`, HA 2026.10.1). `severity` is the CAP word or `"Unknown"`.
  - **HA 2026.4** (the minimum version) has no `nina.get_details` (no `services.py` in `nina/`); there the same fields are still state attributes. Without the service the provider reads the attributes (decided 2026-10-29 for M8).
  - The cache keeps the details of the last 64 ids (cached per id: an off→on with an id seen before needs no call). A failed call is not repeated for the same id for 5 minutes (NINA polls every 5 minutes); meanwhile the entity keeps counting as failed (decided 2026-10-29 for M8).
- **DWD Weather Warnings** (`sensor`, current and advance warnings per region): state = highest level 0–4 of the non-expired warnings; attributes as in the table, plus `warning_<i>` with the complete warning. Typical trigger: `numeric`, threshold 3 or 4. For ventilation usually not needed.
- **Meteoalarm** (`binary_sensor`, `device_class: safety`, on while an alert is valid): the attributes are the alert dict returned by `meteoalertapi` 0.3.1 `get_alert()`, copied as is (2026.10.1 only re-encodes strings to valid UTF-8; `meteoalarm/binary_sensor.py`, HA 2026.4.0 and 2026.10.1); `expires` is one of its keys (HA reads it). The other keys (expected `headline`, `description`, `instruction`, `severity`, `event`, `awareness_level`) come from the library and are not confirmed from its source ([Q8](README.md#open-questions)). Handled by the `attributes` provider, which reads `severity` (a CAP word) before `awareness_level` (`"3; orange; Severe"` is parsed as well).
- **Météo-France** (`sensor` "weather alert", state = colour text of the highest phenomenon, attributes = colour per phenomenon): trigger `state_list` (e.g. `Orange`, `Rouge`), provider `none`.
- **Environment Canada** (`sensor` per alert type, state = count, attributes `alert_<i>` with the title and `alert_time_<i>` with the date, numbered from 1): trigger `numeric` ≥ 1.

#### Filters

Applied per `WarningDetails`. A warning **without** details (provider `none`, or a failed details call) always counts.

| Filter | Subentry option | Matched against | Rule |
|---|---|---|---|
| exclude | `exclude_keywords` | headline + text + actions | if any keyword occurs, the warning does **not** count. Checked first |
| event | `headline_keywords` | headline only | the warning counts if a keyword occurs |
| advice | `text_keywords` | headline + text + actions | the warning counts if a keyword occurs |
| severity | `min_severity` | severity | if not `none`: severity ≥ minimum, otherwise the warning does not count; `None` counts as `extreme` |

A warning counts if it is not excluded, passes the severity filter, and matches `headline_keywords` **or** `text_keywords`. If both lists are empty, every non-excluded warning counts.

Why two lists: event words like "Brand" are reliable in the short headline ("Großbrand – …") but produce false hits in the body (the sender "Amt für Brand- und Katastrophenschutz", "Feuerwehr" in the introductory sentence of almost every message). Advice words like "Lüftung" are reliable anywhere, because authorities only mention ventilation when it must be switched off.

Keywords are case-insensitive substrings; `/…/` is a regular expression (`re.search`, `IGNORECASE`).

Every `WarningDetails` that counts is an active warning. Suggested filters per provider are pre-filled when the subentry is created ([01-configuration.md](01-configuration.md#warning-sources-config-subentries)).

#### Warning key and incident

- **Warning key** = `subentry_id + ":" + id` if the provider knows an id (NINA, `attributes` with `id`/`identifier`); else `subentry_id + ":" + headline + ":" + start` (DWD); else `subentry_id + ":" + entity_id + ":" + ISO time of the last activation`. For NINA the key does not contain the entity, so moving to another slot keeps the key. The activation time is the first evaluation that saw the entity active; while the entity stays active (also across a restart, through the loaded warnings) the key is kept, so a `numeric` level change from 3 to 4 is the same warning and an override keeps applying (decided 2026-10-29).
- **Incident key** = `subentry_id + ":" +` the headline, lower-cased, with leading update/all-clear prefixes removed (`^\d+\.\s*aktualisierung:\s*`, `^entwarnung:\s*`, `^update:\s*`, `^all clear:\s*`). Without a headline: the warning key.
- An **override** applies to incident keys: updates of an overridden incident stay overridden, a different incident shuts down again.

### Flow

```text
warning becomes active (new key)
  → safety.active += key
  → if its incident key is not overridden: mode safety_off, unit off, notification "safety" (BHV-41)

user override (notification action, button/switch, service, voice)
  → overridden += incident keys of the active warnings, mode falls back to the next priority, notification cleared, logbook entry

warning ends (key no longer active)
  → safety.active -= key; an incident key leaves `overridden` when no active warning has it any more
  → if it was the last non-overridden warning and the unit was in safety_off:
       restart_pending = now + restart_confirm_timeout
       notification "restart?" with actions "Restart now" (resume) and "Keep off" (keep_off)

restart_pending deadline reached → restart_pending cleared, trigger "timeout", notification "restarted"
```

A new warning during `restart_pending` clears `restart_pending` and shuts down again.

While `safety_off` is caused only by `restart_pending`, the reason attribute is `safety_off` and the attribute `awaiting_restart` is true.

**External start during `safety_off`** ([Q11](README.md#open-questions), owner decision 2026-10-29): an external start of the unit (off actuator, or a `fan` actuator without off actuator, switched to running by someone else after the confirmation window, [02-control.md](02-control.md#writing)) is an **override** of the current warnings, like "Keep ventilating": the incident keys of the active warnings are overridden, `restart_pending` is cleared, the logbook and the event show trigger `override` with source `external`. It also ends a pause (the unit was started) and acts while control is disabled (`override_safety` always acts). A new warning (a different incident) shuts down again.

Details of the flow (decided 2026-10-29 for M7):

- Notification `safety` is sent when the unit enters `safety_off` because of active warnings (also when control is switched on or an override is removed during a warning), and again when a new warning shuts down while the unit already is in `safety_off` (an update of a non-overridden incident replaces the text). `restart` replaces it when the restart wait begins. Leaving `safety_off` by the restart timeout sends `restarted`; leaving it in any other way (override, resume, keep off, control switch) clears the safety notification.
- `restart_confirm_timeout` 0: no restart wait; the unit restarts at once (trigger `warning_ended`, notification `restarted`).
- The engine always emits the notification effects; the glue sends nothing for a kind whose `notify_*` option is off.

## Automatic boosts

### Rise detector

Shower and cooking use the same detector class, one instance per sensor.

```text
parameters: rise, window, end_offset, max_duration, cooldown, higher_is_worse
samples: deque of (time, value), kept for `window`; added on every valid state change
         and on every periodic evaluation (current value)

if higher_is_worse is false: value = -value (internally always "higher = worse")

not active, not in cooldown:
    vmin = min(values in window)
    if value - vmin ≥ rise:
        active, baseline = vmin, started = now, deadline = now + max_duration

active:
    if value ≤ baseline + end_offset  → end (trigger "detector_end")
    if now ≥ deadline                  → end (trigger "timeout")
    invalid input for longer than stale_timeout → end (trigger "inputs_lost")

end: cooldown until now + cooldown, samples cleared
```

The auto boost for a kind is active while at least one of its detectors is active.

### Cooking trigger entity

Optional, in addition to the detectors. Active when the entity is `on` (binary_sensor, switch, fan, light) or numeric `> cooking_trigger_threshold`. The cooking auto boost stays active until `cooking_trigger_run_on` after the trigger turned inactive, at most `cooking_max_duration` from start.

### Persistence of detectors

Active auto boosts (kind, entity, baseline, started, deadline) and cooldowns are persisted. Sample buffers are not; after a restart they refill.

## Reminders (BHV-24)

One rule for all off states (`off`, `safety_off`):

- `off_since` is set when an off mode starts and cleared when it ends.
- First reminder: after `pause_reminder_after` for an indefinite `Pause`, otherwise after `off_notice_after`.
- Further reminders every 24 h while the off state lasts.
- Reminder notification has the action "Resume". Timed pauses that end before the threshold send nothing.
- `off_since` spans a change between the two off states (a pause followed by a safety shutdown is one off state); the threshold is the one of the current mode. No reminder is sent during the startup grace (a due one follows when it ends). When the off state ends, a sent reminder is cleared (decided 2026-10-29).
- SPEC-GAP: a unit that stays stopped while control is disabled (e.g. a pause stopped it, then the control switch was turned off) is in mode `disabled`, not in an off state, so it gets no reminder although moisture protection is inactive.

## Persistence

- Storage: `homeassistant.helpers.storage.Store`, version 1, key `better_home_ventilation.<entry_id>`. Minor version 1 = 0.1, minor version 2 = 0.2 (safety, `safety_restore`, `off_since`, `last_reminder`); the minor bump is backwards compatible, so 0.1 still reads the data. Migration 1.1 → 1.2 sets `off_since` of a running pause to its start (`serialize.migrate_store_data`, called by the glue's `Store._async_migrate_func`).
- Content: the full `ControllerState` except `last_mode` derivations; datetimes as ISO strings in UTC.
- Saved with `async_delay_save(…, 1)` after every state change.
- Removed in `async_remove_entry`.
- **On load:**
  - expired `Boost`/`Pause`/`AutoBoost`/`RestartPending` are ended with trigger `timeout_while_offline` (logged once); an expired `RestartPending` also sends the notification `restarted`.
  - `safety.active` is **not** trusted: it is rebuilt from the current warning source states after startup. `overridden` incident keys are kept only if an active warning with the same incident key exists again after startup.
  - How (decided 2026-10-29 for M7): the loaded warnings are *unconfirmed*. A warning that a source reports again is confirmed (no new notification). Until the startup grace ends, unconfirmed warnings still count (fail-safe: many sources report late); at its end they are dropped like ended warnings, so a unit that was in `safety_off` gets the restart wait and question instead of a silent restart (BHV-43). Overridden incident keys follow the normal rule: they leave `overridden` when no active warning has them any more, i.e. at the end of the grace if no source reports the incident again.
  - If the store is missing (first start): start with defaults, nothing else.
  - If the store is corrupt or has an unknown future version: start with defaults, log a warning, raise repair issue `state_reset`. "Corrupt" means content that cannot be read as a controller state; a file that is not valid JSON is handled by `Store` itself (renamed to `*.corrupt.<time>`, core repair `storage_corruption`, loaded as missing), and a future major version raises `UnsupportedStorageVersionError` (HA core `helpers/storage.py`, checked on 2026.4.0 and 2026.10.1).
- Migration: `Store` with `minor_version`; any format change bumps the version and adds a migration function with a test.

## Mode change events

On every change of the resolved mode an event `better_home_ventilation_mode_changed` is fired:

```text
entry_id, device_id, name, from_mode, to_mode, reason, setpoint, trigger, source, warning (headline or None), ends_at
```

`ends_at` is the end of the new boost or pause as an ISO 8601 string, `None` for an indefinite pause and every other mode (decided 2026-10-16 for M5: the logbook shows "until 18:30" from the event alone, because the `ends_at` sensor may have changed again by the time the logbook is read).

`trigger` values: `startup`, `command`, `timeout`, `timeout_while_offline`, `warning`, `warning_ended`, `override`, `detector_start`, `detector_end`, `inputs_lost`, `inputs_restored`, `control_switch`.

The logbook platform describes these events ([04-interface.md](04-interface.md#logbook)).
