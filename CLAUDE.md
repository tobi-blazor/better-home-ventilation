# CLAUDE.md – Better Home Ventilation

Custom Home Assistant integration (HACS) for demand-controlled home ventilation. Domain `better_home_ventilation`.

## Source of truth

- The specification in [docs/spec/](docs/spec/README.md) defines the behavior. Read the relevant spec document before implementing or changing anything.
- If the implementation needs behavior the spec does not define, or the spec is contradictory: **stop and ask**, then update the spec in the same commit as the code.
- Requirement IDs (`BHV-xx`) are referenced in tests and, where helpful, in docstrings.
- Open questions are tracked in [docs/spec/README.md](docs/spec/README.md#open-questions). Answer them with sources (HA developer docs, HA core source with version), never by guessing.

## Rules

- **Engine is pure:** nothing in `custom_components/better_home_ventilation/engine/` imports `homeassistant`, does I/O, reads the clock or logs. `now` is passed in. See [05-architecture.md](docs/spec/05-architecture.md#engine-and-glue).
- **Tests first** for engine rules: write the scenario from [06-testing.md](docs/spec/06-testing.md) as a failing test, then implement.
- Follow current Home Assistant developer guidelines (config entries, `runtime_data`, `has_entity_name`, translations, exception translations). When unsure about an HA API, check the HA core source for the minimum supported version.
- No YAML configuration, no `configuration.yaml` instructions in docs.
- Configuration follows [01-configuration.md](docs/spec/01-configuration.md#principles): all settings in `entry.options`, `data_description` for every field, specific selectors, sections, subentries for repeated items. Keep `quality_scale.yaml` up to date.
- Language: code, comments, docs and commit messages in **English**. Translations: `en` and `de`.
- No personal data: no addresses, real entity ids of a specific home, IP addresses or names in code, tests, docs or examples. Use neutral examples (`sensor.living_room_co2`).
- No secrets anywhere.

## Getting started

- Read [docs/spec/README.md](docs/spec/README.md) first, in particular "Scope of phase 0.1" and "Before the first line of code".
- Work phase by phase. Do not build features of later phases "on the way".
- The owner communicates in German. Answer in German; code, docs and commits stay English.

## Workflow

- Branch per phase or feature, PR into `main`.
- Commit messages: Conventional Commits (`feat:`, `fix:`, `docs:`, `test:`, `chore:`), referencing BHV ids where it applies.
- Before committing: `ruff format`, `ruff check`, `mypy`, `pytest` must pass.
- Update `CHANGELOG.md` for every user-visible change.

## Agent skills

### Issue tracker

Issues live in GitHub Issues of this repository, managed with the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

The five default labels: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `CONTEXT.md` and `docs/adr/` at the repo root. The specification in `docs/spec/` remains the source of truth. See `docs/agents/domain.md`.
