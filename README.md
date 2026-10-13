# Better Home Ventilation

Demand-controlled ventilation for central home ventilation units (MVHR / HRV / ERV) in Home Assistant.

One integration instead of a pile of helpers, template sensors and automations: pick your ventilation unit and your room sensors in a config dialog, and Better Home Ventilation takes care of the rest.

> **Status:** specification phase. There is no code yet. The specification in [docs/spec/](docs/spec/README.md) is the source of truth for the implementation.

## Planned features

- **Room-based demand control:** CO₂ ramp per room (the worst room wins), minimum flow, upper humidity limit
- **Dryness protection:** limits ventilation when the indoor air gets too dry in winter
- **Calm output:** rate limiting, step limits and dead bands, no flapping between two values
- **Boost and pause** with duration, from the dashboard, services and voice (Assist)
- **Automatic boosts** for showering (bathroom humidity rise) and cooking (kitchen IAQ rise)
- **Night limit** (noise) and **away mode**
- **Safety shutdown** on official hazard warnings (e.g. NINA), smoke detectors or any `binary_sensor`, with actionable phone notifications, per-warning override and confirmed restart
- **Robust:** failed sensors are ignored instead of read as 0, fallback flow, state survives restarts, readiness checks for the unit (e.g. control mode)
- **Traceable:** a setpoint sensor with the deciding reason and all partial demands, logbook entries for every mode change, diagnostics with a decision log

Works with any unit Home Assistant can control through a `number` (m³/h or %), a `fan` (percentage) or a `select` (stages).

## Not in scope

Device drivers (use the integration or ESPHome package for your unit), humidifier control, bypass/summer cooling logic. See [non-goals](docs/spec/README.md#non-goals).

## Documentation

- [Specification](docs/spec/README.md)
- [Implementation plan](docs/plan.md)

## License

TBD
