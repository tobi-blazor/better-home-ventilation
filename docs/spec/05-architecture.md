# 05 Architecture

## Engine and glue

The central design rule: **all control logic is a pure, deterministic engine without Home Assistant imports.** The HA part only collects inputs, runs the engine and applies its decision.

```text
            HA state machine, timers, services, notification actions
                              │
                              ▼
   controller.py ── builds Snapshot(now, inputs, actuator, presence, warnings)
        │                     │
        │                     ▼
        │          engine.evaluate(snapshot, state, options) ──► Decision + new ControllerState + Effects
        │                     (pure, no I/O, no HA)
        ▼
   applies Effects: write actuator, off/on, readiness correction, notify, fire event, persist, schedule timers
        │
        ▼
   entities read the current Decision (push via listener callbacks)
```

- **Commands** (`boost`, `pause`, `resume`, `override_safety`, `keep_off`, control switch) are also pure functions: `engine.apply_command(state, command, options, now) -> ControllerState`.
- **Effects** are plain data (`WriteFlow(value)`, `TurnOff`, `TurnOn`, `CorrectReadiness(entity_id, state)`, `Notify(kind, …)`, `ClearNotification(tag)`, `FireEvent(…)`, `ScheduleAt(time)`). The engine never calls anything.
- `now` is always passed in. Tests control time without patching.

This makes the logic testable with plain `pytest` in milliseconds, and the same engine runs in the replay tool ([06-testing.md](06-testing.md#replay)).

## Package layout

```text
custom_components/better_home_ventilation/
  __init__.py          async_setup (services, intents), async_setup_entry / unload / remove, runtime_data
  manifest.json
  const.py             DOMAIN, option keys, defaults, enums
  config_flow.py       ConfigFlow, OptionsFlow (menu + sections), ReadinessConditionFlow, WarningSourceFlow (subentries)
  controller.py        HA glue: subscriptions, debounce, lock, timers, snapshot, effect execution, store
  actuator.py          adapters number/fan/select + off actuator: native <-> flow, service calls, verification
  notifications.py     sending/clearing notifications, action listener, missing notify service issue
                       (not `notify.py`: that module name is an integration's notify platform,
                       `notify/legacy.py` imports `<integration>.notify` for a configured platform)
  issues.py            repair issues missing_entity and state_reset
                       (not `repairs.py`: that module name is an integration's repairs platform)
  warning_providers/   details providers (HA glue): __init__.py (registry), nina.py, dwd.py, environment_canada.py, attributes.py
  entity.py            BetterVentilationEntity base (device info, listener registration)
  sensor.py  binary_sensor.py  button.py  select.py  number.py  switch.py
  intent.py            intent handlers (0.2)
  logbook.py           describe mode_changed events
  diagnostics.py
  services.yaml  strings.json  icons.json  translations/en.json  translations/de.json
  quality_scale.yaml   status per quality scale rule (done / exempt with comment / todo)
  engine/
    __init__.py        evaluate(), apply_command()
    models.py          Options, Snapshot, InputValue, ControllerState, Decision, Effects (frozen dataclasses)
    validity.py        input validity
    demand.py          ramp, automatic demand, dryness, limits, reasons
    modes.py           mode resolution, setpoint per mode, timers, safety, reminders
    shaping.py         output shaping
    mapping.py         flow <-> native mapping (pure part)
    detector.py        rise detector
    safety.py          triggers, filters, warning/incident keys, severity normalisation, HTML-to-text (pure; providers use it)
    serialize.py       ControllerState <-> dict for the store, migrations
tests/
  engine/              plain pytest, no HA
  components/          pytest-homeassistant-custom-component
tools/
  replay.py            replay recorded history through the engine (0.2)
examples/
  custom_sentences/{en,de}/better_home_ventilation.yaml
  dashboard.yaml       example card configuration with built-in cards only
```

`engine/` must not import `homeassistant`. This is enforced by a test that imports the engine package with `homeassistant` blocked, and by a ruff rule (`flake8-tidy-imports` banned module for `engine/`).

## Home Assistant APIs used

| Purpose | API |
|---|---|
| runtime data | `ConfigEntry[BetterVentilationData]`, `entry.runtime_data` |
| configuration | `ConfigFlow`, `OptionsFlow`, `ConfigSubentryFlow`, `data_entry_flow.section`, `helpers.selector` ([01-configuration.md](01-configuration.md#principles)) |
| reload on configuration changes | one update listener, `entry.add_update_listener` ([ADR 0001](../adr/0001-update-listener-instead-of-options-flow-with-reload.md)) |
| YAML guard | `CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)` |
| entity renames/removals | `async_track_entity_registry_updated_event` |
| notification texts | `helpers.translation.async_get_translations(…, "common", …)` |
| state changes | `async_track_state_change_event` for all configured entities |
| periodic | `async_track_time_interval` |
| timers | `async_track_point_in_utc_time` (re-scheduled on state change) |
| debounce | `homeassistant.helpers.debounce.Debouncer` |
| startup | `async_at_started` |
| storage | `helpers.storage.Store` |
| restore of `select`/`number` parameter entities | `RestoreEntity` / `RestoreNumber` |
| service calls | `hass.services.async_call(…, blocking=True, context=…)` |
| events | `hass.bus.async_fire` |
| notification actions | listen to `mobile_app_notification_action` |
| warning details | `hass.services.async_call("nina", "get_details", target=…, blocking=True, return_response=True)` |
| repairs | `helpers.issue_registry.async_create_issue` |
| intents | `helpers.intent.async_register`, `IntentHandler` |
| translations for logbook/services/errors | `strings.json`, `translation_key` on exceptions |

No polling of external systems, no `requirements` in the manifest.

## manifest.json

```json
{
  "domain": "better_home_ventilation",
  "name": "Better Home Ventilation",
  "after_dependencies": ["mobile_app", "conversation", "logbook", "nina"],
  "codeowners": ["@tobi-blazor"],
  "config_flow": true,
  "dependencies": [],
  "documentation": "https://github.com/tobi-blazor/better-home-ventilation",
  "integration_type": "helper",
  "iot_class": "calculated",
  "issue_tracker": "https://github.com/tobi-blazor/better-home-ventilation/issues",
  "requirements": [],
  "version": "0.1.0"
}
```

`hacs.json`: `{"name": "Better Home Ventilation", "homeassistant": "<minimum version>", "render_readme": true}`.

## Compatibility

- **Minimum HA version: 2026.4** (decided 2026-10-14). Guideline: the oldest release of the last six months that provides every API above. Every API in the table is available from 2025.10 on (the latest one is `menu_option_descriptions`, 2025.10; config subentries 2025.3; `data_entry_flow.section` 2024.7; `last_reported` 2024.4). Written to `hacs.json` as `"homeassistant": "2026.4.0"`.
- Python: 3.14 (HA ≥ 2026.3 requires `>=3.14.2`).
- Schema library: since HA 2026.9 core validates with `probatio` and installs it permanently as `voluptuous` alias (core PR #175128, #182107). The integration uses `import voluptuous as vol`, which works on 2026.4 (real voluptuous) and on 2026.9+ (alias). No compatibility module. CI runs the tests on both sides of this switch.
- Translations: `en` (source) and `de`.

## Code quality

- Typing: full type hints, `mypy --strict` for `engine/`, `mypy` for the rest.
- Lint/format: `ruff` (format + lint), configured in `pyproject.toml`.
- Follow the HA [Integration Quality Scale](https://developers.home-assistant.io/docs/core/integration-quality-scale/) rules that apply to custom integrations (config flow, unique ids, `has_entity_name`, translations, `runtime_data`, unload, diagnostics, repairs, exception translations). Target: everything from Bronze and Silver, Gold where it applies.
- Logging: `_LOGGER = logging.getLogger(__name__)`; no logging of every evaluation above `debug`.
- No blocking I/O in the event loop.

## CI and releases

GitHub Actions:

| Workflow | Content |
|---|---|
| `lint.yml` | ruff, mypy |
| `test.yml` | pytest with coverage on the minimum (2026.4) and the latest HA version |
| `validate.yml` | `hassfest` (home-assistant/actions) on every push and PR; HACS validation (`hacs/action`) only on `main`, because it reads the default branch through the GitHub API |
| `release.yml` | on tag `v*`: check that the tag matches `manifest.json` version, create GitHub release with notes from `CHANGELOG.md` |

- Versioning: SemVer. `0.x` while the spec phases are implemented.
- `CHANGELOG.md` in Keep-a-Changelog format.
- Dev environment: WSL (Ubuntu) with `uv` and Python 3.14; Home Assistant does not support Windows as a runtime. `requirements_test.txt` with `pytest-homeassistant-custom-component` pinned to the latest HA version used for development (`0.13.372` = HA 2026.10.1); the CI job for the minimum version installs `0.13.321` (= HA 2026.4.0). No devcontainer. The engine tests must also run without HA installed (plain `pytest tests/engine`).
- Testing on a real instance before the repository is public: HACS custom repository if private repositories work ([Q10](README.md#open-questions)), otherwise copy `custom_components/better_home_ventilation/` to the instance and restart.

## Before 1.0 / HACS default listing

- Repository public, description and topics set (`home-assistant`, `hacs`, `ventilation`, `mvhr`, `hrv`, `co2`).
- Brand images (icon/logo) in `home-assistant/brands` (custom integrations section) or, if supported for custom integrations at that time, in the repository.
- README with installation via HACS custom repository, configuration walkthrough, screenshots, FAQ (why no extract-duct IAQ, how to set up NINA as warning source).
- License file.
