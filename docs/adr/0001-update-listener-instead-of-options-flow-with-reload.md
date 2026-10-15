# Update listener instead of OptionsFlowWithReload

The config entry is reloaded by one update listener for every change (options, readiness condition and warning source subentries, entity renames written by the integration itself); the options flow is a plain `OptionsFlow` and subentry flows finish with `async_update_and_abort`. `OptionsFlowWithReload` cannot be used: in HA core (checked on 2026.10.0, `config_entries.py`) adding or removing a subentry, including the delete action in the UI, only fires update listeners and never reloads, and `OptionsFlowWithReload` raises a `ValueError` when the entry has an update listener. Core integrations with subentries (Anthropic, OpenAI, Ollama, Google Generative AI, MQTT) use the same pattern.

## Consequences

- Never call `async_update_reload_and_abort` or `_abort_if_unique_id_configured(reload_on_update=True)`: together with an update listener this is deprecated since HA 2026.6 and an error from 2026.12 ([developer blog 2026-05-07](https://developers.home-assistant.io/blog/2026/05/07/config-entry-listener-together-with-reloading-methods), core PR #169198).
