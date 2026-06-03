# RelayCLI web — per-role specialist models + API keys in Settings

Date: 2026-07-03
Status: approved (user: "di setting ada menu konfigurasi, ada rolenya apa
aja kita bisa set mau model apa, masing-masing model punya spesialis, di
sana kita masukin api key dan model rolenya apa, langsung ngejalanin").

## Intent

Not one model split into agents — a TEAM of different models, each a
specialist for its role (e.g. deepseek-reasoner plans, qwen3-coder codes,
claude reviews). The web Settings panel becomes the config surface: assign
a model per role and enter provider API keys; the next run uses them.

The backend already supports this — Settings has planner_model / coder_model
/ reviewer_model / explorer_model / tester_model and router.resolve_model
picks the override or falls back to the base model. This exposes it in the
UI and adds runtime key entry.

## Changes

### Backend (web.py)
- `set_role_model(role, model)`: sets settings.<role>_model ("" clears the
  override → role falls back to the base model). Roles:
  explorer/planner/coder/tester/reviewer.
- `set_key(provider, key)`: for the 6 keys Settings fields exist for
  (openai/anthropic/gemini/groq/mistral/openrouter) sets the field; for
  deepseek/dashscope(Qwen)/zhipu(GLM) sets the process env var LiteLLM
  reads. Empty clears. Never echoed back — state() only reports detected
  true/false.
- state() adds `role_models` (role, enabled, assigned override, resolved
  short name) and `providers` (id, label, env, detected).
- Endpoints: POST /api/role-model, POST /api/key (behind the existing
  loopback Host+Origin guards). Keys arrive over loopback only.

### run_command.py
- Add DEEPSEEK_API_KEY / DASHSCOPE_API_KEY / ZHIPUAI_API_KEY to the scrubbed
  env set so a UI-entered key can't be read back by a spawned command.

### Frontend (Settings panel)
- Panel becomes scrollable (max-height). Two new sections:
  - "Agent models": a row per enabled role with a <select> (base model +
    the full catalog grouped). Changing it POSTs /api/role-model; the run
    header / graph then show per-role models.
  - "API keys": a row per provider — label, a password input, Save, and a
    detected dot. Saving POSTs /api/key.
- The top-bar model dropdown stays the BASE model; role overrides layer on
  top.

## Out of scope
Persisting keys/role-models to config.toml (session-only for now),
per-role model in single-agent mode, key validation/ping.

## Tests
set_role_model sets/clears the field; set_key sets a Settings field vs an
env var by provider and clears; _provider_status reflects both; state()
carries role_models + providers; endpoints roundtrip. Existing suites pass.
