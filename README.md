# Better Home Ventilation

Demand-controlled ventilation for central home ventilation units (MVHR / HRV / ERV) in Home Assistant.

One integration instead of a pile of helpers, template sensors and automations: pick the entity that sets your unit's flow and your room sensors in a dialog, and Better Home Ventilation calculates the flow the unit should run at, writes it calmly to the unit and tells you why it chose that value.

> **Status: 0.1.0, for test runs only.** Version 0.1 contains the demand control, profiles, boost and pause. It does **not** yet contain the safety shutdown on hazard warnings (smoke, fire, chemical release). Use 0.1 for supervised test runs, not for unattended everyday operation: keep your existing automation for daily use until 0.2. See [What 0.1 does not do yet](#what-01-does-not-do-yet).

## Contents

- [What it does](#what-it-does)
- [What 0.1 does not do yet](#what-01-does-not-do-yet)
- [Use cases](#use-cases)
- [Requirements and supported units](#requirements-and-supported-units)
- [Installation](#installation)
- [Setup](#setup)
- [Options](#options)
- [Readiness conditions](#readiness-conditions)
- [Warning sources](#warning-sources)
- [Profiles](#profiles)
- [How the setpoint is calculated](#how-the-setpoint-is-calculated)
- [When the setpoint is updated](#when-the-setpoint-is-updated)
- [Entities](#entities)
- [Actions](#actions)
- [Events and logbook](#events-and-logbook)
- [Examples](#examples)
- [Limitations and FAQ](#limitations-and-faq)
- [Troubleshooting](#troubleshooting)
- [Removal](#removal)
- [Not in scope](#not-in-scope)
- [Development](#development)

## What it does

- **Room-based demand control.** CO₂ sensors in the rooms where people are drive a CO₂ ramp; the room with the highest valid value wins. A humidity-high ramp raises the flow when a living or sleeping room gets damp. A minimum flow keeps a basic air exchange for moisture protection.
- **Dryness protection.** When the mean humidity of the living rooms falls below a threshold in winter, the humidity-high ramp may only raise the flow for a limited time; the CO₂ demand still counts.
- **Quiet and away limits.** A quiet limit caps the flow in a night window (or all day in the quiet profile); while nobody is home the flow drops to the minimum unless the humidity is high.
- **Profiles.** A fixed set of strategies (balanced, air quality, quiet, winter) that you switch in daily use from a select entity.
- **Calm output.** A minimum time between changes, a maximum step per change, an asymmetric dead band and rounding to a flow step: no flapping between two values. Boosts, pauses and profile changes act at once.
- **Boost and pause.** Buttons and actions for a boost (level and duration, pressing again extends it) and a pause (for a duration or until resumed). Stopping the unit at the unit itself counts as a pause.
- **Robust.** Failed, stale or implausible sensor values are ignored, never read as 0. Without any valid CO₂ sensor a fallback flow is used. Readiness conditions (for example a control mode the unit must be in) are checked and can be corrected automatically. Boost, pause, profile and the control switch survive restarts.
- **Traceable.** A setpoint sensor carries the full decision (mode, reason, every contribution, limits, invalid inputs, why nothing was written) as attributes; a reason sensor and a mode sensor make the history readable; every mode change appears in the logbook.

Works with any unit Home Assistant can control through a `number` (m³/h or %), a `fan` (percentage) or a `select` (stages). The integration contains no device driver: it uses the entities your unit's own integration (or ESPHome package) already provides.

## What 0.1 does not do yet

| Feature | Planned for |
|---|---|
| **Safety shutdown** on hazard warnings (official warnings such as NINA, smoke detectors, any `binary_sensor`), with override and confirmed restart | 0.2 |
| Phone notifications (safety, problems) | 0.2 |
| Reminders for long pauses and long off states | 0.2 |
| Voice commands (Assist intents) | 0.2 |
| Diagnostics download, repair issues (for example for a removed sensor) | 0.2 |
| Replay of recorded sensor history through the control logic | 0.2 |
| Automatic boosts on showering (bathroom humidity rise) and cooking (kitchen air quality rise) | 0.3 |

Because the safety shutdown is missing, **0.1 is meant for test runs** on a real installation while someone is around, not for unattended everyday operation. Until 0.2, problems are only visible on the problem sensor; there are no notifications.

## Use cases

- **Replace a home-made automation or blueprint** that maps an extract-air sensor to a flow: control from the room sensors instead, with fallback, calm output and a reason for every value.
- **Shadow run before switching over:** create the entry, switch its **Control** switch off and let your existing automation keep writing. The integration still calculates and records the setpoint, so you can compare both in the history before you switch.
- **Daily control from the dashboard:** a boost while cooking or with guests, a pause while changing the filter or airing through open windows, the quiet profile for a home-office day, the winter profile in a dry heating season.
- **Nights and holidays:** the quiet limit keeps bedrooms quiet at night; the away limit drops to the minimum flow when nobody is home.

## Requirements and supported units

- **Home Assistant 2026.4 or newer.**
- A central ventilation unit whose flow can be set from Home Assistant through one of these entities (the **actuator**):

| Actuator | How the flow is written |
|---|---|
| `number` in m³/h (or `m3/h`) | the flow itself, rounded to the entity's step |
| `number` in % | percentage of the max flow |
| `number` without a unit or with another unit | you choose whether it takes m³/h or % |
| `fan` with percentage | percentage of the max flow (at least one step above 0) |
| `select` with stages | you assign a flow to each stage; the lowest stage that reaches the flow is chosen, with hysteresis when stepping down |

- Optional: an **off actuator** that stops the unit, such as a standby `switch` or `input_boolean` (you choose which state means "stopped"), or a `fan` that is turned off. Without one, "off" means the lowest actuator value, which some units do not take as a stop; such units need an off actuator.
- At least one room **CO₂ sensor** (device class `carbon_dioxide`) or room **humidity sensor** (device class `humidity`).
- Optional: presence entities (persons, device trackers, zones, binary sensors, input booleans) for the away limit.

One entry controls exactly one unit through one actuator. Several units need several entries; one actuator can only be used by one entry.

## Installation

There is no YAML configuration. Everything is set up in the UI.

### HACS (custom repository)

1. In HACS, open the menu (⋮) → **Custom repositories**.
2. Add `https://github.com/tobi-blazor/better-home-ventilation` with the type **Integration**.
3. Search for **Better Home Ventilation** in HACS and download it.
4. Restart Home Assistant.

If HACS reports a download error, use the manual installation. After an update, always restart Home Assistant: reloading the entry does not load new code.

### Manual

1. Copy the folder `custom_components/better_home_ventilation/` of this repository into the `custom_components/` folder of your Home Assistant configuration directory (create `custom_components/` if it does not exist).
2. Restart Home Assistant.

## Setup

> **Before you start:** if an existing automation, blueprint or script already sets the unit's flow, disable it first. Two controllers writing to the same unit fight each other (see [FAQ](#running-two-controllers)). The integration starts writing about one minute after the entry is created.

**Settings → Devices & services → Helpers → Create helper → Better Home Ventilation.**

### Step 1: Create ventilation control

| Key | UI name | Default | Meaning |
|---|---|---|---|
| `name` | Name | Ventilation | Name of the unit, used for the device and its entities (stored as the entry title). |
| `actuator` | Actuator | – (required) | The entity that sets the unit's flow: a `number` (m³/h or %), a `fan` (percentage) or a `select` (stages). |
| `max_flow` | Max flow | the actuator's maximum | Nominal maximum flow of the unit in m³/h (10–2000). Leave empty to use the actuator's maximum if it is a `number` in m³/h. The default flows (minimum, fallback, quiet limit, boost level, steps) are calculated from it. |

Section **Off actuator** (collapsed, optional; the same fields as in the options, see [Actuator](#actuator)):

| Key | UI name | Default | Meaning |
|---|---|---|---|
| `off_entity` | Off actuator | none | A `switch` or `input_boolean` that stops the unit (e.g. a standby switch), or a `fan` that is turned off. Leave empty only if the unit stops at its lowest value. |
| `off_state` | Unit stopped when | On | Which state of the off actuator means "stopped": "On (e.g. a standby switch)" or "Off (e.g. a power switch)". Ignored for a `fan`. |

Without an off actuator, off and pause set the unit to its lowest value (0 %, the actuator minimum or the lowest stage). **Some units do not stop at their lowest value** and keep running, for example a unit that ignores flows below its own minimum. If your unit has a standby switch or a power switch in Home Assistant, choose it here (see [FAQ](#pause-does-not-stop-the-unit)).

Errors: the actuator does not exist; a `select` has fewer than two options; the max flow cannot be taken from the actuator and must be entered; the actuator is already controlled by another entry; the off actuator does not exist or is the actuator itself.

### Step 2: Actuator mapping

Shown only when needed:

- **`number` without a known unit:** **Actuator unit** (`actuator_unit`), either "Flow (m³/h)" or "Percentage of the max flow (%)".
- **`select`:** one field per option of the select, labelled with the option text (stored as `select_mapping`). Enter the flow in m³/h that the stage delivers; leave a field empty if that option should never be used. At least two options need a flow, and no two options may have the same flow.

A `number` in m³/h or %, and a `fan`, skip this step.

### Step 3: Room sensors

| Key | UI name | Default | Meaning |
|---|---|---|---|
| `co2_sensors` | CO₂ sensors | none | CO₂ sensors (device class `carbon_dioxide`) in living and sleeping rooms. The highest valid value drives the CO₂ demand. |
| `humidity_sensors` | Humidity sensors | none | Humidity sensors (device class `humidity`) in living and sleeping rooms only: no bathrooms, no rooms with a humidifier and no sensor in the extract duct. |

At least one CO₂ or humidity sensor is required, and a sensor cannot be in both lists.

The entry is created with all other settings at their defaults. Presence entities and all tuning are set afterwards in the options; the off actuator can also be added or changed there.

### Changing the settings later

Open the integration page: in **Settings → Devices & services → Devices**, open the controller's device (named after the entry, for example "Ventilation") and follow its link to **Better Home Ventilation**. Select **Configure** on the entry. (From **Settings → Devices & services → Helpers**, the settings of any of its entities also offer the options.) The menu **Ventilation settings** offers four steps: **Actuator**, **Inputs**, **Control** and **Boosts**. Saving a step applies it right away (the entry is reloaded; a running boost or pause continues).

Readiness conditions are added on the same page with **Add readiness condition** ([below](#readiness-conditions)).

## Options

Defaults marked "of max" are calculated from the max flow when the entry is created, rounded to 5 m³/h, and **do not follow a later change of the max flow**; the **Actuator** step shows the current values as a reminder. The defaults below are for a 400 m³/h unit. Durations are entered as hours:minutes:seconds.

### Actuator

| Key | UI name | Default | Meaning |
|---|---|---|---|
| `actuator` | Actuator | – | The entity that sets the unit's flow. Changing to a `select` or to a `number` without a known unit continues to **Actuator mapping**. |
| `max_flow` | Max flow | from step 1 | Nominal maximum flow of the unit in m³/h. |

Section **Off actuator**:

| Key | UI name | Default | Meaning |
|---|---|---|---|
| `off_entity` | Off actuator | from step 1 | A `switch` or `input_boolean` that stops the unit (e.g. a standby switch), or a `fan` that is turned off. Without it, off means the lowest actuator value, which some units do not take as a stop ([FAQ](#pause-does-not-stop-the-unit)). |
| `off_state` | Unit stopped when | On | Which state of the off actuator means "stopped": "On (e.g. a standby switch)" or "Off (e.g. a power switch)". Ignored for a `fan`. |

Errors: the off actuator does not exist or is the actuator itself.

Section **Native range** (collapsed):

| Key | UI name | Default | Meaning |
|---|---|---|---|
| `actuator_min` | Actuator minimum | entity's own limit | Lowest value written to the actuator, in its own unit. |
| `actuator_max` | Actuator maximum | entity's own limit | Highest value written to the actuator, in its own unit. |

**Actuator mapping** (only for a `select` or a `number` without a known unit, pre-filled with the stored mapping): same fields as [step 2](#step-2-actuator-mapping).

### Inputs

Section **Room sensors**: **CO₂ sensors** (`co2_sensors`) and **Humidity sensors** (`humidity_sensors`), as in [step 3](#step-3-room-sensors).

Section **Presence**:

| Key | UI name | Default | Meaning |
|---|---|---|---|
| `presence_entities` | Presence entities | none | Persons, device trackers, zones, binary sensors or input booleans. Home means `home` (person, device tracker), `on` (binary sensor, input boolean) or a number above 0 (zone). Away means none of them is home. Unavailable or unknown entities count as home, so missing data never lowers the flow. |

### Control

Section **CO₂ ramp**:

| Key | UI name | Default | Range | Meaning |
|---|---|---|---|---|
| `co2_start` | CO₂ start | 850 ppm | 400–2000 | CO₂ value at which the flow starts to rise above the minimum flow. |
| `co2_end` | CO₂ end | 1400 ppm | 500–3000 | CO₂ value at which the max flow is reached. Must be above the start. |

Section **Flows**:

| Key | UI name | Default | Range | Meaning |
|---|---|---|---|---|
| `min_flow` | Minimum flow | 80 m³/h (20 % of max) | 0–max | Lowest flow in automatic mode; keeps a basic air exchange for moisture protection. |
| `fallback_flow` | Fallback flow | 150 m³/h (37.5 % of max) | minimum–max | Flow used when CO₂ sensors are configured but none of them has a valid value. |

Section **Humidity**:

| Key | UI name | Default | Range | Meaning |
|---|---|---|---|---|
| `humidity_high_start` | Humidity-high start | 55 % | 30–90 | Highest room humidity at which the flow starts to rise above the minimum flow. |
| `humidity_high_end` | Humidity-high end | 70 % | 35–100 | Highest room humidity at which the max flow is reached. Must be above the start. |
| `dryness_threshold` | Dryness threshold | 40 % | 20–60 | When the mean room humidity is below this value, the air counts as dry. Threshold plus hysteresis must stay below the humidity-high start. |
| `dryness_hysteresis` | Dryness hysteresis | 2 % | 0–10 | Dry ends only when the mean humidity is this much above the threshold, so the state does not flap. |
| `dryness_humidity_high_limit` | Humidity-high time while dry | 1 h | | How long the humidity-high ramp may exceed the CO₂ demand while the air is dry. 0 suppresses it completely while dry. |

Section **Quiet limit**:

| Key | UI name | Default | Range | Meaning |
|---|---|---|---|---|
| `night_enabled` | Quiet limit at night | on | | Apply the quiet limit in the night window. The quiet profile applies it all day regardless. |
| `night_start` | Night start | 22:00 | | Start of the night window (local time, inclusive). |
| `night_end` | Night end | 07:00 | | End of the night window (exclusive); it may be on the next day. Must differ from the start. |
| `quiet_max_flow` | Quiet max flow | 250 m³/h (62.5 % of max) | minimum–max | Maximum flow while the quiet limit applies. User boosts are not limited. |
| `quiet_humidity_override` | Humidity override | 65 % | 40–100 | Above this highest room humidity the quiet limit does not apply. |

Section **Away**:

| Key | UI name | Default | Meaning |
|---|---|---|---|
| `away_enabled` | Away limit | on | Reduce the flow to the minimum while nobody is home, unless the humidity is high. Without presence entities it has no effect. Turning it off disables the away limit in every profile. |

Section **Output shaping** (collapsed):

| Key | UI name | Default | Range | Meaning |
|---|---|---|---|---|
| `min_write_interval` | Minimum write interval | 5 min | | Minimum time between two changes of the flow. Boosts and pauses act at once. |
| `max_step_up` | Maximum step up | 50 m³/h (12.5 % of max) | 5–max | Largest increase per change. |
| `max_step_down` | Maximum step down | 50 m³/h (12.5 % of max) | 5–max | Largest decrease per change. |
| `deadband_up` | Dead band up | 10 m³/h | 0–100 | The flow is only raised when the target is at least this much above the written flow. |
| `deadband_down` | Dead band down | 20 m³/h | 0–100 | The flow is only lowered when the target is at least this much below the written flow, so the unit does not flap. |
| `flow_quantum` | Flow step | 5 m³/h | 1–50 | Written flows are rounded to multiples of this value. |

Section **Advanced** (collapsed):

| Key | UI name | Default | Meaning |
|---|---|---|---|
| `evaluation_interval` | Evaluation interval | 1 min | The control is evaluated at least this often, in addition to every input change. |
| `debounce` | Debounce | 10 s | Input changes within this time are evaluated together. |
| `stale_timeout` | Stale timeout | 30 min | An input that has not reported a value for this long is ignored. |
| `startup_grace` | Startup grace | 1 min | After Home Assistant starts, nothing is written for this long so that all inputs can load. |
| `problem_delay` | Problem delay | 10 min | How long a problem must persist before the problem sensor turns on. |

### Boosts

Section **Manual boost**:

| Key | UI name | Default | Meaning |
|---|---|---|---|
| `boost_default_duration` | Default duration | 30 min | Duration of a boost when none is given. Also the initial value of the **Boost duration** entity (nearest option). |
| `boost_default_level` | Default level | 300 m³/h (75 % of max) | Flow of a boost when no level is given. Also the initial value of the **Boost level** entity. Must be between the minimum flow and the max flow. The automatic demand still wins if it is higher. |
| `boost_max_duration` | Maximum duration | 4 h | A boost never lasts longer than this from now, also when it is extended. |

The profile is not an option: it is chosen in daily use with the **Profile** entity.

## Readiness conditions

Some units ignore the written flow unless another entity has a certain state, for example a **control mode** `select` that must be set to a "remote" or "bus control" option. A readiness condition tells the integration about it.

On the integration page select **Add readiness condition**:

| Step | Key | UI name | Default | Meaning |
|---|---|---|---|---|
| 1 | `entity_id` | Entity | – | The entity whose state must match: a `select`, `input_select`, `switch`, `input_boolean`, `number`, `sensor` or `binary_sensor`, for example the control mode select `select.ventilation_unit_control_mode`. |
| 2 | `expected_state` | Expected state | – | Writes to the actuator only happen while the entity has this state. For a select one of its current options (for example `Remote`); for on/off entities "On" or "Off"; for a `number` a value (compared with a tolerance of 0.5); otherwise free text. |
| 2 | `auto_correct` | Correct automatically | off | Set the entity to the expected state before writing when it differs. Offered only for `select`, `input_select`, `switch`, `input_boolean` and `number`. |

Example: the unit only accepts a flow from Home Assistant while its control mode select is set to `Remote`, and the unit resets the mode after a power cut. Add a readiness condition with the control mode select, expected state `Remote` and **Correct automatically** on. After a power cut the integration sets the mode back to `Remote` and then writes the flow again.

The condition is listed as "<entity name> = <expected state>" and can be reconfigured or deleted there. Each entity can have only one condition.

How it works:

- Conditions are checked in every evaluation. While a condition is not met, nothing is written to the unit (also no off/on) and the setpoint attribute `write_blocked_by` shows `not_ready`.
- With **Correct automatically**, the integration sets the entity back to the expected state (at most once per minute per condition), fires the event `better_home_ventilation_readiness_corrected`, and writes the flow again right after it is restored. This also happens when no flow change is due, because the unit ignores the flow until the condition is met again. If the condition is still not met after three attempts, the problem `not_ready` is reported and further attempts follow every five minutes.
- Without automatic correction (or while the entity is unavailable), the problem `not_ready` is reported at once; the problem sensor still waits for the problem delay.
- No correction is made while the **Control** switch is off or during the startup grace.

## Warning sources

> **Unreleased (0.2, in development).** Warning sources and the safety shutdown are in the development branch; phone notifications with "Keep ventilating" and the restart question, and the safety settings in the options, follow before 0.2.0.

A warning source is a hazard signal that switches the ventilation off (**safety shutdown**), so that smoke or a chemical cloud outside is not pulled into the house and smoke inside is not spread through the ducts. Each source has its own trigger and filters, so a smoke detector, a NINA region and a weather warning sensor can be combined.

On the integration page select **Add warning source**:

| Step | Key | UI name | Default | Meaning |
|---|---|---|---|---|
| 1 | `source_entity` | Entity | – | A `binary_sensor`, `input_boolean` or `switch` (on = warning), a `sensor` with a warning level or count, or a `select`/`input_select` with states. |
| 1 | `source_device` | Device | – | Instead of an entity: a device whose binary sensors are all warnings, for example a NINA region with several message slots. All its enabled binary sensors count; slots added later are picked up automatically. Exactly one of entity and device. |
| 1 | `name` | Name | name of the entity or device | Shown on the **Safety warning** entity and in notifications. |
| 2 | `trigger` | Trigger | numeric for a numeric sensor, else state list | Only for a `sensor`, `select` or `input_select`. **Numeric threshold**: active while the state is a number at or above the threshold. **State list**: active while the state is one of the states (case does not matter). |
| 2 | `threshold` | Threshold | – | For a numeric threshold, for example `3` for DWD warning level 3 or `1` for one Environment Canada alert. |
| 2 | `states` | States | – | For a state list, for example `Orange` and `Rouge` for a Météo-France weather alert. Options of a select are offered; other values can be typed. |
| 3 | `details` | Warning details | Automatic | Where the text of a warning comes from. **Automatic** uses the detected provider (shown in the form). **Attributes** reads headline and text from the entity's attributes. **None**: every activation is one warning; keywords and severity do not apply. |
| 3 | `headline_keywords` | Event keywords (headline) | suggestions (NINA) | Matched in the headline only, for example "Brand" or "fire". |
| 3 | `text_keywords` | Advice keywords (whole text) | suggestions (NINA) | Matched in headline, text and recommended actions, for example "Lüftung" or "ventilation". |
| 3 | `exclude_keywords` | Exclude keywords | suggestions (NINA) | A warning that contains one of these anywhere never counts, for example "Entwarnung", "all clear", "Probealarm". Checked first. |
| 3 | `min_severity` | Minimum severity | No minimum | Warnings below this severity are ignored; a warning without a known severity counts as extreme. |

For an on/off entity or a device there is no step 2 (the trigger is on/off). For sources without details (smoke and CO detectors, template and group helpers, `input_boolean`, `switch`) the keywords and the severity are not shown, because they would never apply.

How warnings count:

- `unavailable` or `unknown` never makes a source active; after the problem delay the problem `warning_source_unavailable` is reported.
- A warning counts if it contains no exclude keyword, passes the minimum severity, and matches an event keyword in the headline **or** an advice keyword anywhere. With both include lists empty every warning that is not excluded counts. Keywords ignore case; `/…/` is a regular expression (for example `/brand(?!enburg)/`).
- Event words such as "Brand" are reliable in the short headline but give false hits in the body (the fire brigade as sender, "Amt für Brand- und Katastrophenschutz"). Advice words such as "Lüftung" are reliable anywhere, because authorities only mention ventilation when it must be switched off.
- A source without details, or whose details cannot be fetched within 10 seconds, **always counts** (fail-safe); a failed details call is reported as the problem `warning_details_failed`.
- While a warning is active the mode is **Safety shutdown** (`safety_off`): the unit is switched off (off actuator, or lowest value) and nothing else is written. By default this also happens while the **Control** switch is off.
- **Safety override** (or the action `override_safety`) keeps the ventilation running despite the current warnings. Updates of the same incident (for example "1. AKTUALISIERUNG: Großbrand …") stay overridden; a different warning switches the unit off again. Turning the switch off removes the override.
- When the last warning ends, the unit does not restart silently: it waits for the restart confirmation (**Resume**) and restarts by itself after 30 minutes. While it waits, the setpoint attribute `awaiting_restart` is true.

The suggested keywords for NINA come in German (server language `de`) and English (every other language) and are based on the standardised warning texts of the German authorities and on live NINA messages; the [specification](docs/spec/01-configuration.md#warning-sources-config-subentries) lists them with the reasons. They are only suggestions: change them in the source's **Reconfigure** dialog.

### Supported integrations

| Integration | Entity | Details | Typical setup |
|---|---|---|---|
| [NINA](https://www.home-assistant.io/integrations/nina/) | `binary_sensor` per message slot | action `nina.get_details` (Home Assistant 2026.4: the attributes) | the region **device** as source, details Automatic, the suggested keywords |
| [DWD Weather Warnings](https://www.home-assistant.io/integrations/dwd_weather_warnings/) | `sensor` (current warning level) | attributes `warning_<i>_headline`, `_description`, `_instruction`, `_level` | numeric threshold 3 or 4; usually not needed for ventilation |
| [Environment Canada](https://www.home-assistant.io/integrations/environment_canada/) | `sensor` per alert type (count) | attribute `alert_<i>` (title only) | numeric threshold 1 |
| [Meteoalarm](https://www.home-assistant.io/integrations/meteoalarm/) | `binary_sensor` | generic attributes (`headline`, `description`, `severity`, `awareness_level`) | on/off, minimum severity if wanted |
| [Météo-France](https://www.home-assistant.io/integrations/meteo_france/) | `sensor` weather alert (colour) | none | state list, for example `Orange`, `Rouge` |
| smoke and CO detectors, template, threshold or group `binary_sensor`, `input_boolean` | on/off | none | on/off; for example a threshold helper on outdoor PM2.5 or an `input_boolean` as manual danger switch |

Any other entity with a `headline`/`title`/`event` or `description`/`text`/`message` attribute works with the generic attributes provider; otherwise it counts without details.

**Setting up NINA:** add the [NINA integration](https://www.home-assistant.io/integrations/nina/) for your region with enough message slots (for example 5) and without its own filters, then add a warning source and choose the NINA region **device** (not a single slot: warnings move between slots). The suggested keywords filter out weather warnings, drinking-water notices, all-clear messages and tests.

**Indoor gas detectors are not a recommended source.** With gas in the house the advice is not to operate electrical equipment, to open the windows and to leave the building; switching the unit does not help and is itself switching. Indoor smoke and CO detectors are good sources (smoke must not be spread through the ducts; CO near a fireplace).

## Profiles

The profile decides which limits apply and how CO₂ counts while the air is dry. It is chosen with the **Profile** select, takes effect at once (without step limits), is kept across restarts and can be changed while control is off. Profiles have no settings of their own; they use the options above.

The select shows each profile with a short description, e.g. "Quiet – limited all day" (German: "Leise – ganztägig gedrosselt").

| Profile | Quiet limit | Away limit | CO₂ while the air is dry |
|---|---|---|---|
| **Balanced** (default) | in the night window | yes | full CO₂ ramp |
| **Air quality** | never | never | full CO₂ ramp |
| **Quiet** | all day | yes | full CO₂ ramp |
| **Winter** | in the night window | yes | CO₂ ramp starts at its midpoint (1125 ppm with the defaults), so moderate CO₂ no longer raises the flow while the air is dry; high CO₂ still reaches the max flow |

In every profile:

- "In the night window" requires **Quiet limit at night**; the away limit requires **Away limit** and presence entities.
- Dryness protection of the humidity-high ramp applies.
- User boosts are never capped by the quiet limit.

Example with the defaults, dry air and CO₂ 1300 ppm: balanced 340 m³/h (reason CO₂), winter 285 m³/h (reason CO₂). At 1000 ppm: balanced 165 m³/h, winter 80 m³/h (reason dryness protection).

## How the setpoint is calculated

All values are flows in m³/h. The calculation has five stages: **contributions → demand → limits → target → shaping**.

1. **Contributions.** Each rule asks for a flow:
   - **minimum:** the minimum flow (80).
   - **CO₂:** a linear ramp over the highest valid room CO₂ value, from the minimum flow at CO₂ start to the max flow at CO₂ end. With the defaults: 850 ppm or less → 80, 1125 ppm → 240, 1400 ppm or more → 400.
   - **humidity-high:** the same kind of ramp over the highest valid room humidity, 55 % → 80 up to 70 % → 400 (62 % → about 229).
   - **fallback:** the fallback flow (150), only when CO₂ sensors are configured but none has a valid value.
2. **Demand.** The highest of the CO₂ contribution (or the fallback flow), the humidity-high contribution and the minimum.
   **Dryness protection:** when the mean room humidity is below 40 %, the air counts as dry (until it is back at 42 %). While dry, humidity-high may raise the flow above the CO₂ demand for at most 1 h at a time; after that it is suppressed until the most humid room is below the humidity-high start or the air is no longer dry. The CO₂ demand is never limited by dryness, except for the CO₂ ramp start in the winter profile.
3. **Limits** (depending on the [profile](#profiles)):
   - **away:** while nobody is home, CO₂ is ignored and the demand drops to the minimum, unless humidity-high or the fallback flow asks for more.
   - **quiet:** the demand is limited to the quiet max flow (250), unless the most humid room is at or above the humidity override (65 %). The quiet limit also applies to the fallback flow.
4. **Target** of the active mode:

| Mode | Target |
|---|---|
| Automatic | the demand |
| Fallback (no valid CO₂ sensor) | the demand, which contains the fallback flow |
| Boost | the boost level, or the demand before the quiet limit if that is higher |
| Off (pause) | the unit is stopped; the setpoint is 0 |
| Control off | the demand is calculated and shown, nothing is written |

5. **Shaping** turns the target into the **setpoint**, the flow actually written. The target is rounded to the flow step and kept within the actuator range. A mode change, a boost command, a profile change, the first write after startup and a restored readiness condition are written at once. Otherwise:
   - at most one change per minimum write interval (5 min);
   - up only if the target is at least 10 above the current flow, by at most 50 per change;
   - down only if the target is at least 20 below the current flow, by at most 50 per change.

   Example: the unit runs at 80 and CO₂ jumps to 1400 ppm (target 400). The setpoint becomes 130, five minutes later 180, and so on.

   The current flow is read back from the actuator; a value changed at the unit or by someone else is taken as the new current flow. 30 s after each write the actuator is checked; on a mismatch the value is written once more, then the problem `write_mismatch` is reported.

### Reasons

The **Reason** sensor names the rule or mode that determined the setpoint:

| Reason | Meaning |
|---|---|
| `minimum` | the minimum flow |
| `co2` | the CO₂ ramp |
| `humidity_high` | the humidity-high ramp |
| `dryness_protection` | the flow is lower than humidity-high (or, in the winter profile, the full CO₂ ramp) would ask for, because the air is dry |
| `away` | the away limit |
| `quiet_limit` | the quiet limit |
| `fallback` | no valid CO₂ sensor, fallback flow |
| `boost` | a manual boost |
| `off` | a pause |
| `safety_off` | a safety shutdown or the wait for the restart confirmation ([warning sources](#warning-sources)) |
| `shower`, `cooking` | reserved for 0.3; not used yet |

During a boost the reason is the automatic rule (for example `co2`) when the automatic demand is higher than the boost level.

## When the setpoint is updated

The integration is event driven; it does not poll anything.

| Trigger | When |
|---|---|
| state change of a room sensor or presence entity | after the debounce time (10 s); further changes within that time are evaluated together |
| periodic | every evaluation interval (1 min) |
| boost, pause, resume, profile change, control switch, end of a boost or pause | at once |
| state change of the actuator, off actuator, a readiness entity or a warning source | at once (warning details are fetched first, at most 10 s) |
| saving options, readiness conditions or warning sources | the entry is reloaded |

Control starts once Home Assistant has started. During the startup grace (1 min) the entities show values but nothing is written and no input problems are raised, because many sensors become available a few seconds after start.

Input values count only when they are **valid**: not `unavailable`, `unknown` or empty; numeric; reported within the stale timeout (30 min, also when the value did not change); and plausible (CO₂ 250–10000 ppm, humidity 0–100 %). Invalid values are left out of every calculation, listed in the setpoint attribute `invalid_inputs`, and reported as a problem after the problem delay.

## Entities

All entities belong to one device per entry (named after the entry, model "Demand controller"). The entity ids below are the ones created for the default name "Ventilation" with English as the server language; with German they are, for example, `sensor.ventilation_sollwert`. No entity is disabled by default.

| Entity | Entity id | Category | Description |
|---|---|---|---|
| Setpoint | `sensor.ventilation_setpoint` | | Flow the unit should run at (m³/h), after shaping; 0 in a pause. Attributes below. |
| Reason | `sensor.ventilation_reason` | | Rule or mode that determined the setpoint ([reasons](#reasons)). |
| Mode | `sensor.ventilation_mode` | | Automatic, Fallback, Boost, Off, Safety shutdown (with [warning sources](#warning-sources)), Control off. (Shower and Cooking exist as states but are not used yet.) |
| Ends at | `sensor.ventilation_ends_at` | | End of the current boost or timed pause; empty otherwise. |
| Boost | `button.ventilation_boost` | | Starts a boost with the **Boost duration** and **Boost level**; pressing again extends it by the duration. |
| Pause | `button.ventilation_pause` | | Stops the unit for the **Pause duration**. |
| Resume | `button.ventilation_resume` | | Ends a boost or pause and returns to automatic control. |
| Control | `switch.ventilation_control` | | Off: the integration writes nothing (no flow, no off/on, no readiness correction), rejects boost and pause, and ends a running boost or pause. It keeps calculating; the mode is "Control off". When it is switched on again after a pause had stopped the unit, the unit is started again. |
| Profile | `select.ventilation_profile` | | Balanced, Air quality, Quiet, Winter ([profiles](#profiles)). |
| Boost duration | `select.ventilation_boost_duration` | config | 15 min, 30 min, 1 h, 2 h. Restored after a restart. |
| Boost level | `number.ventilation_boost_level` | config | Flow of the boost button, minimum flow to max flow. Restored after a restart. |
| Pause duration | `select.ventilation_pause_duration` | config | 1 h (initial), 4 h, 8 h, Until resumed. Restored after a restart. |
| Problem | `binary_sensor.ventilation_problem` | diagnostic | On while a problem persists longer than the problem delay. |
| Safety warning | `binary_sensor.ventilation_safety_warning` | | Only with a warning source. On while any warning is active, overridden or not. Attribute `warnings`: list of `{source, headline, severity, overridden, since}`. |
| Safety override | `switch.ventilation_safety_override` | | Only with a warning source. On: the active warnings are overridden and the unit keeps running. Off: an active warning switches the unit off again. Without an active warning turning it on has no effect. |

### Setpoint attributes

| Attribute | Content |
|---|---|
| `mode`, `reason`, `profile` | as the entities |
| `target` | flow the active mode asks for, before shaping |
| `written`, `last_write` | last flow written to the unit and when |
| `actuator_value` | current native value of the actuator (not recorded) |
| `contributions` | `minimum`, `co2`, `humidity_high`, `fallback`, `boost`, `shower`, `cooking` (flow or empty) |
| `co2_max`, `co2_max_entity` | highest valid CO₂ value and its sensor |
| `humidity_max`, `humidity_max_entity`, `humidity_mean` | highest and mean valid room humidity |
| `limits` | active limits: `away`, `quiet`, `dry` (also when they did not change the value) |
| `invalid_inputs` | sensors currently ignored |
| `write_blocked_by` | why nothing was written: `grace`, `interval`, `deadband`, `disabled`, `not_ready`, `unavailable`, or empty (not recorded) |
| `off_method` | in a pause: `off_actuator` or `minimum` |

### Problems

The **Problem** sensor's attribute `problems` lists `{key, entity_id, since}` for every problem older than the problem delay. Problems clear themselves when the cause is gone.

| Key | Meaning |
|---|---|
| `actuator_unavailable` | the actuator or off actuator is unavailable; nothing is written |
| `not_ready` | a readiness condition is not met; nothing is written |
| `write_failed` | a service call to the unit failed (see the log) |
| `write_mismatch` | the actuator did not show the written value, also after a retry |
| `inputs_invalid` | a room sensor is invalid (one entry per sensor) |
| `no_valid_co2` | CO₂ sensors are configured but none is valid; the fallback flow is used |
| `warning_source_unavailable` | an entity of a warning source is unavailable (one entry per source) |
| `warning_details_failed` | the details of an active warning could not be fetched; it counts as a warning without details |

## Actions

All actions take an optional **Entry** (`config_entry_id`). It can be left out when exactly one enabled entry exists.

| Action | Fields | Effect |
|---|---|---|
| `better_home_ventilation.boost` | `duration` (minutes, 5–720, optional; default: the **Boost duration** entity), `level` (m³/h, minimum flow to max flow, optional; default: the **Boost level** entity) | Starts a boost, or extends a running one by the duration (from its current end, never beyond the maximum duration from now); a given level replaces the running level. Replaces a pause. |
| `better_home_ventilation.pause` | `duration` (minutes, 1–10080, optional; without it the pause lasts until resumed) | Stops the unit. Replaces a boost. |
| `better_home_ventilation.resume` | – | Ends a boost or a pause; confirms the restart after a safety shutdown. |
| `better_home_ventilation.override_safety` | – | Keeps the ventilation running despite the current warnings, or ends the wait for the restart confirmation ([warning sources](#warning-sources)). |

Errors (shown translated in the UI):

| Error | When |
|---|---|
| `entry_required` | no entry given and there is not exactly one entry |
| `entry_not_found` | the given id is not a Better Home Ventilation entry |
| `entry_not_loaded` | the entry is not loaded (for example disabled or failed) |
| `level_out_of_range` | the boost level is outside minimum flow to max flow |
| `control_disabled` | boost or pause while the **Control** switch is off (the buttons raise the same error) |
| `no_active_warning` | `override_safety` without an active warning or restart wait |

Between boost and pause the last command wins. A boost or pause that ends while Home Assistant is offline ends at the next start.

## Events and logbook

| Event | When | Data |
|---|---|---|
| `better_home_ventilation_mode_changed` | the mode changed | `entry_id`, `device_id`, `name`, `from_mode`, `to_mode`, `reason`, `setpoint`, `trigger`, `source`, `warning` (headline of the warning, or the source name, when the new mode is `safety_off`), `ends_at` (end of a new boost or timed pause, ISO 8601) |
| `better_home_ventilation_readiness_corrected` | a readiness condition was corrected automatically | `entry_id`, `entity_id`, `from_state`, `to_state` |

`trigger` is one of `startup`, `command`, `timeout`, `timeout_while_offline`, `inputs_lost`, `inputs_restored`, `control_switch`, and with warning sources `warning`, `warning_ended` and `override`. `source` (for commands) is `button`, `service` or `external` (at the unit).

Every mode change appears in the logbook of the device, linked to the **Mode** sensor, for example "Ventilation switched to Boost (button, until 18:30)" or "Ventilation back to Automatic (timeout)". The text is in the server language. The setpoint itself is not logged per write; its history is in the **Setpoint** sensor.

## Examples

### Dashboard

[`examples/dashboard.yaml`](examples/dashboard.yaml) is a dashboard with one view and built-in cards only: tiles for setpoint, mode, reason, end time, profile, control and problem, buttons for boost, pause and resume, the parameter entities, the key decision attributes, a history graph of setpoint, reason and mode, and a logbook card. Create a new dashboard, open **Edit dashboard → ⋮ → Raw configuration editor** and replace the content with the file (or paste its view under `views:` of an existing dashboard), then adjust the entity ids to your installation.

### Automations

Boost while the cooker hood runs (until the automatic cooking detection of 0.3 arrives):

```yaml
triggers:
  - trigger: state
    entity_id: switch.kitchen_hood
    to: "on"
actions:
  - action: better_home_ventilation.boost
    data:
      duration: 45
      level: 300
```

Pause while a window is open and resume when it is closed again:

```yaml
triggers:
  - trigger: state
    entity_id: binary_sensor.living_room_window
    to: "on"
    for: "00:02:00"
    id: open
  - trigger: state
    entity_id: binary_sensor.living_room_window
    to: "off"
    id: closed
actions:
  - choose:
      - conditions:
          - condition: trigger
            id: open
        sequence:
          - action: better_home_ventilation.pause
            data:
              duration: 120
      - conditions:
          - condition: trigger
            id: closed
        sequence:
          - action: better_home_ventilation.resume
```

Note that `resume` also ends a boost that was started in the meantime.

## Limitations and FAQ

### Why are extract-duct IAQ, VOC or CO₂-equivalent sensors not supported?

A sensor in the extract duct measures the mixed air of the whole home, diluted by every room with good air, so one occupied bedroom with high CO₂ barely shows up. IAQ indices and CO₂-equivalent values (eCO₂) are estimates from VOC readings, not CO₂: they react to cooking, cleaning agents and perfume, and their baseline drifts. The integration controls from **real CO₂ sensors in the rooms** and lets the worst room win. The CO₂ field only offers sensors with the device class `carbon_dioxide`; do not select a CO₂-equivalent sensor even if it uses that device class. Temperature and humidity from the extract duct are fine for monitoring, but not as control inputs.

### Which humidity sensors should I use?

Only sensors in **living and sleeping rooms**. Not in bathrooms (a shower would drive the humidity-high ramp to the max flow and distort the mean that dryness protection relies on; shower detection comes in 0.3 with its own sensors), not in rooms with a humidifier (the integration would ventilate the added moisture away, and the room would never look dry), and not in the extract duct.

### Pets and away

While nobody is home, CO₂ is ignored and the flow drops to the minimum flow, because nobody produces CO₂. Pets do. If pets stay at home alone, choose the **Air quality** profile while you are away, turn off **Away limit**, or do not configure presence entities. Humidity-high and the fallback flow still apply while away.

### What happens when sensors fail?

Failed, stale or implausible values are ignored, never read as 0. If one of several CO₂ sensors fails, the others are used. If CO₂ sensors are configured and none is valid, the mode becomes **Fallback** and the fallback flow (150 m³/h) is used, also while away; the quiet limit still applies. Without any valid input and without CO₂ sensors, the minimum flow is used. Every invalid sensor is listed in `invalid_inputs` and, after the problem delay, turns the problem sensor on. When the actuator itself is unavailable, nothing is written until it is back.

A sensor that is removed from Home Assistant counts as invalid; a repair issue for it comes in 0.2. A renamed sensor (new entity id) is followed automatically.

### Pause does not stop the unit

Without an off actuator, a pause (and every other "off") writes the lowest actuator value: 0 % for a `fan`, the lowest value of a `number` (**Actuator minimum** if set, else its `min` attribute, else 0), the lowest mapped stage for a `select`. The setpoint attribute `off_method` is then `minimum`. Many units do not stop at that value: they ignore flows below their own minimum (for example 50 m³/h) and keep running.

Fix: configure an **off actuator**, at setup in the section **Off actuator** or later in **Configure → Actuator**. Choose the entity that really stops the unit, for example a standby switch such as `switch.ventilation_standby`, and set **Unit stopped when** to the state that means stopped (On for a standby switch, Off for a power switch). A pause then switches that entity and writes no flow value; resuming switches it back and writes the setpoint again.

If the unit has no such entity, the lowest value is all the integration can do. Raising **Actuator minimum** does not help: it only moves the lowest written value.

### Stopping the unit at the unit counts as a pause

If the off actuator (or a `fan` actuator without an off actuator) is switched to "stopped" by anyone other than the integration, for example at the unit's panel or in its app before a filter change, the integration takes it as a **pause until resumed** (logbook: "at the unit"). Starting the unit there again ends the pause. Changes from or to `unavailable` (for example when the unit restarts) are not commands, and while the **Control** switch is off such changes are ignored. A pause has no reminder in 0.1, so remember to resume.

Many devices report a command only on their next poll (for example an ESPHome Modbus device every 30 s). If the unit reaches the state the integration has just switched it to within 60 s, that is the integration's own change, not a stop or start at the unit: a pause for one hour stays a pause for one hour.

A changed flow value at the unit is not a pause: it is taken as the current flow, and the next write follows the normal shaping rules.

### Running two controllers

Only one controller may write to the unit. If an existing automation, blueprint or the unit's own schedule also sets the flow, both fight: values jump, the integration reports `write_mismatch`, and the history is meaningless. **Disable the existing automation while the integration writes** and re-enable it if you remove the integration or switch its **Control** switch off for good. For a comparison without writing, keep the **Control** switch off ([shadow run](#use-cases)).

### Other limitations

- One actuator per entry; multi-zone systems with dampers are not supported.
- A pause without an off actuator writes the lowest actuator value, which may not stop the unit completely (`off_method: minimum`, see [Pause does not stop the unit](#pause-does-not-stop-the-unit)).
- Changing the max flow does not change the flows derived from it; adjust them in **Control** and **Boosts**.
- The logbook text is in the server language, not in each user's language.

## Troubleshooting

| Symptom | What to check |
|---|---|
| The setpoint changes but the unit does not | Setpoint attribute `write_blocked_by`: `disabled` (Control switch off), `not_ready` (readiness condition, for example the control mode), `unavailable` (actuator), `grace` (just started), `interval` / `deadband` (shaping holds on purpose). Then the **Problem** sensor's `problems` attribute. |
| The unit runs at another value than the setpoint | Another automation or the unit's own program writes too ([two controllers](#running-two-controllers)); the problem `write_mismatch` appears. Or the unit needs a readiness condition. |
| `write_failed` | A service call to the unit failed; the Home Assistant log has the error. |
| A sensor is in `invalid_inputs` | It is unavailable, not numeric, has not reported within the stale timeout, or is outside the plausible range (CO₂ 250–10000 ppm, humidity 0–100 %). |
| The flow reacts slowly | That is the output shaping: one change per 5 minutes, at most 50 m³/h per change by default. Boosts, pauses and profile changes act at once. Tune the **Output shaping** section if needed. |
| The flow stays low although CO₂ is high | Look at `limits` and the reason: `away` (nobody home), `quiet_limit` (night window or quiet profile) or `dryness_protection` (winter profile). |
| The mode is Fallback | No CO₂ sensor is valid; see `invalid_inputs`. |
| Boost or pause is rejected | The **Control** switch is off (`control_disabled`). |
| The unit went into a pause by itself | It was stopped at the unit or by another integration ([external stop](#stopping-the-unit-at-the-unit-counts-as-a-pause)); the logbook shows "at the unit". |
| Entity ids differ from this README | They depend on the device name and the server language. |

For more detail enable debug logging on the integration page (**Enable debug logging**), reproduce the issue, and disable it again to download the log.

## Removal

1. Press **Resume** and wait until the unit runs, so that it is not left stopped. Removing the integration does not write anything to the unit: it stays at the last written flow, or stopped if a pause had stopped it.
2. Open the integration page ([see above](#changing-the-settings-later)) and select **Delete** in the entry's menu (⋮). The device, its entities, the readiness conditions, the warning sources and the stored controller state are removed.
3. If you installed with HACS: in HACS, open **Better Home Ventilation** and select **Remove**. If you installed manually: delete the folder `custom_components/better_home_ventilation/`.
4. Restart Home Assistant.
5. Re-enable your previous automation if you had one.

## Not in scope

Device drivers (use the integration or ESPHome package for your unit), humidifier control, bypass/summer cooling logic, multi-zone systems. See [non-goals](docs/spec/README.md#non-goals).

## Documentation

- [Specification](docs/spec/README.md) (source of truth for the behavior)
- [Glossary](CONTEXT.md)
- [Implementation plan](docs/plan.md)
- [Changelog](CHANGELOG.md)

## Development

Home Assistant does not run on Windows, so development happens in WSL (Ubuntu) with [uv](https://docs.astral.sh/uv/) and Python 3.14. Keep the virtual environment in the WSL home directory, not on `/mnt/c`.

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh   # installs uv to ~/.local/bin
uv python install 3.14
uv venv --python 3.14 ~/.venvs/bhv
uv pip install --python ~/.venvs/bhv/bin/python -r requirements_test.txt
source ~/.venvs/bhv/bin/activate
```

`requirements_test.txt` pins `pytest-homeassistant-custom-component` to the latest supported Home Assistant version; CI also runs the tests against the minimum version (2026.4).

From the repository root:

| Check | Command |
|---|---|
| format | `ruff format --check .` (`ruff format .` to fix) |
| lint | `ruff check .` |
| types | `mypy` and `MYPYPATH=custom_components/better_home_ventilation mypy tests/engine` |
| tests | `pytest --cov` (fails below 90 % coverage) |
| engine tests only (no Home Assistant needed) | `pytest tests/engine` |

The control logic lives in `custom_components/better_home_ventilation/engine/` and must not import Home Assistant. Ruff enforces this through `engine/ruff.toml`, and a test imports the engine with Home Assistant blocked. The Home Assistant glue (controller, actuator adapters, entities, actions, logbook, config flow) lives next to it in `custom_components/better_home_ventilation/`.

### Adding a details provider

Details providers live in `custom_components/better_home_ventilation/warning_providers/`, one module per integration, registered in `REGISTRY` by the entity registry platform. A provider is one class:

```python
class ExampleProvider:
    name = "example_platform"

    async def async_get_details(
        self, hass: HomeAssistant, entity_id: str, state: State
    ) -> list[WarningDetails]:
        # Called only for an active entity.
        return [
            make_details(
                key=..., headline=..., text=..., actions=..., severity=..., sender=...
            )
        ]
```

- Build every warning with `engine.safety.make_details`; it strips HTML and normalises the severity (CAP words, levels 1–4, Meteoalarm awareness levels).
- `key` is the provider's identity of the warning (for example a CAP id); warnings with the same key are the same warning across evaluations.
- Return an empty list when the entity has no details (it then counts as one warning without details). Raise `DetailsError` when the details cannot be fetched; the call must finish within `DETAILS_TIMEOUT` (10 s).
- Add a test with a recorded state of the integration (see `tests/components/test_warning_providers.py`).

Behavior changes start in the [specification](docs/spec/README.md); see [CLAUDE.md](CLAUDE.md) for the working rules. User-visible changes go into [CHANGELOG.md](CHANGELOG.md).

## License

TBD
