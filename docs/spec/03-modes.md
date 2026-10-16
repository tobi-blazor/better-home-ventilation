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
    active: {warning_key: WarningInfo(entity_id, incident_key, headline, severity, sender, recommended_actions, since)}
    overridden: set[incident_key]
    restart_pending: None | RestartPending(deadline)
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

Commands come from buttons, services, intents, notification actions and the unit itself (external stop/start of the off actuator, [02-control.md](02-control.md#writing)). Each command carries a `source` (`button`, `service`, `voice`, `notification`, `external`) for the logbook.

| Command | Effect |
|---|---|
| `boost(duration?, level?)` | If `manual` is `Boost`: `until = min(max(until, now) + duration, now + boost_max_duration)`, `level` replaced if given. Otherwise `manual = Boost(min(now + duration, now + boost_max_duration), level or boost level entity)`. Replaces a `Pause`. `duration` default: boost duration entity |
| `pause(duration?)` | `manual = Pause(now + duration)` or `Pause(None)` (indefinite). Replaces a `Boost` |
| `resume()` | `manual = None`, all `auto_boosts` cleared (detectors enter cooldown), `restart_pending` cleared (= confirm restart). Does **not** lift an active warning: that is `override_safety` |
| `override_safety()` | the incident keys of all warnings in `safety.active` are added to `safety.overridden`; `restart_pending` cleared; safety notifications cleared |
| `keep_off()` | only valid while `restart_pending`: `restart_pending` cleared, `manual = Pause(None)` |
| control switch on/off | `control_enabled`. Switching off ends a `Boost` or `Pause` (`manual = None`) and clears `auto_boosts` (detectors enter cooldown) |
| profile change | `profile`; accepted at any time, also while control is disabled |

While `control_enabled` is false, `boost` and `pause` are **rejected**: services, buttons and intents raise `ServiceValidationError` (`control_disabled`), and external off actuator changes are ignored. `resume` has nothing to end and does nothing. Exceptions: `override_safety` always acts; `keep_off` during `restart_pending` only clears `restart_pending`, so the unit is not switched back on.

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

`unavailable`/`unknown` never make a source active, but raise problem `warning_source_unavailable` after `problem_delay` (one problem per subentry).

**Indoor gas detectors are not a recommended source.** With gas inside the house the advice is not to operate electrical equipment, to open windows and to leave the building; switching the unit does not help and is itself switching. The user README says so. Indoor smoke and CO detectors are good sources (smoke must not be spread through the ducts; CO near a fireplace).

Everything else is covered by HA's own helpers: a **template** or **threshold** `binary_sensor` (e.g. outdoor PM2.5 above a limit, wind from the direction of an industrial area), a **group** of smoke detectors, or an **`input_boolean`** as a manual "danger" switch, all with trigger `on_off`.

#### Details providers

A provider turns an active source into a list of zero or more `WarningDetails`:

```text
WarningDetails(key, incident, headline, text, actions, severity, sender, source_title)
  severity: "minor" | "moderate" | "severe" | "extreme" | None
```

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

Facts per integration (HA core `main`, checked on 2026-10-13):

- **NINA** (`binary_sensor`, `device_class: safety`, one per message slot): slots are positions in the list of current warnings, so a warning can move from slot 2 to slot 1. The attributes `headline`, `description`, `sender`, `severity`, `recommended_actions`, `affected_areas`, `web`, `sent`, `start`, `expires` are **deprecated and removed in HA 2026.11**; only `id` stays. The provider calls `nina.get_details` (`SupportsResponse.ONLY`, target = the binary_sensor, `return_response=True`) on every off→on transition and when `id` changes, cached per `id`. NINA polls every 5 min. Its feed also contains DWD weather warnings, drinking-water notices and **all-clear messages** (e.g. "Entwarnung: Feuerwehreinsatz – …", category Fire, severity Minor, instruction "… Schließen Sie vorsorglich Fenster und Türen."; live NINA API, 2026-10-13). Fire warnings can be Minor. If the service call fails or times out (10 s), the source counts as one warning without details (fail-safe) and problem `warning_details_failed` is raised.
- **DWD Weather Warnings** (`sensor`, current and advance warnings per region): state = highest level 0–4 of the non-expired warnings; attributes as in the table, plus `warning_<i>` with the complete warning. Typical trigger: `numeric`, threshold 3 or 4. For ventilation usually not needed.
- **Meteoalarm** (`binary_sensor`, on while an alert is valid): attributes are the alert dict of the API (keys such as `headline`, `description`, `severity`, `event`, `awareness_level`; exact set unconfirmed, [Q8](README.md#open-questions)). Handled by the `attributes` provider.
- **Météo-France** (`sensor` "weather alert", state = colour text of the highest phenomenon, attributes = colour per phenomenon): trigger `state_list` (e.g. `Orange`, `Rouge`), provider `none`.
- **Environment Canada** (`sensor` per alert type, state = count, attributes `alert_<i>` with the title): trigger `numeric` ≥ 1.

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

- **Warning key** = `subentry_id + ":" + id` if the provider knows an id (NINA, `attributes` with `id`/`identifier`); else `subentry_id + ":" + headline + ":" + start` (DWD); else `subentry_id + ":" + entity_id + ":" + ISO time of the last activation`. For NINA the key does not contain the entity, so moving to another slot keeps the key.
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

## Persistence

- Storage: `homeassistant.helpers.storage.Store`, version 1, key `better_home_ventilation.<entry_id>`.
- Content: the full `ControllerState` except `last_mode` derivations; datetimes as ISO strings in UTC.
- Saved with `async_delay_save(…, 1)` after every state change.
- Removed in `async_remove_entry`.
- **On load:**
  - expired `Boost`/`Pause`/`AutoBoost`/`RestartPending` are ended with trigger `timeout_while_offline` (logged once).
  - `safety.active` is **not** trusted: it is rebuilt from the current warning source states after startup. `overridden` incident keys are kept only if an active warning with the same incident key exists again after startup.
  - If the store is missing (first start): start with defaults, nothing else.
  - If the store is corrupt or has an unknown future version: start with defaults, log a warning, raise repair issue `state_reset`.
- Migration: `Store` with `minor_version`; any format change bumps the version and adds a migration function with a test.

## Mode change events

On every change of the resolved mode an event `better_home_ventilation_mode_changed` is fired:

```text
entry_id, device_id, name, from_mode, to_mode, reason, setpoint, trigger, source, warning (headline or None)
```

`trigger` values: `startup`, `command`, `timeout`, `timeout_while_offline`, `warning`, `warning_ended`, `override`, `detector_start`, `detector_end`, `inputs_lost`, `inputs_restored`, `control_switch`.

The logbook platform describes these events ([04-interface.md](04-interface.md#logbook)).
