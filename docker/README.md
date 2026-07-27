# RelayCLI in Docker

One image, optional services via compose profiles — enable what you want, or
run your own instances and just point RelayCLI at them.

## Quick start

```bash
export OPENROUTER_API_KEY=sk-or-...          # any provider key you use
mkdir -p workspace                           # the project the agent works on

docker compose build
docker compose run --rm relaycli             # interactive REPL in /workspace
docker compose run --rm relaycli -p "explain this repo"
docker compose run --rm relaycli doctor      # health checks inside the container
```

Config, memory, and history persist in the `relaycli-config` volume, so
`/config`, `relaycli mcp add …`, and remembered facts survive restarts.
Point the agent at a real project with
`RELAYCLI_WORKSPACE=/path/to/project docker compose run --rm relaycli`.

## Desktop UI

```bash
docker compose --profile web up -d
# → http://127.0.0.1:8484
```

The container binds 0.0.0.0 internally, but the published port is mapped to
**127.0.0.1 on the host**, so the agent stays private. To expose it on a
trusted LAN, change the mapping to `"8484:8484"` and add
`--allow-host <your-hostname>` to the service command — understand that
whoever reaches the port controls an agent with the container's permissions.

## Optional services

Each service is a compose profile — combine freely:

```bash
docker compose --profile ollama up -d        # local open-weights models
docker compose --profile postgres up -d      # a database for your project
docker compose --profile n8n up -d           # workflow automation
docker compose --profile ollama --profile postgres --profile web up -d
```

### Ollama (local models, no API key)

```bash
docker compose --profile ollama up -d
docker compose exec ollama ollama pull llama3.1
docker compose run --rm relaycli -m ollama_chat/llama3.1
```

Inside compose the agent already gets `OLLAMA_BASE_URL=http://ollama:11434`.
Running your own Ollama instead? `export OLLAMA_BASE_URL=http://host.docker.internal:11434`.

### Postgres

`DATABASE_URL` has no baked-in default — set it once the `postgres` profile
is up (its hostname only resolves inside the compose network), so the
container never silently carries a URL pointing nowhere:

```bash
docker compose --profile postgres up -d
export DATABASE_URL=postgresql://relay:relay@postgres:5432/relay   # or your own POSTGRES_PASSWORD
docker compose run --rm relaycli mcp add postgres
docker compose run --rm relaycli mcp test postgres
```

Using your own database? Just export its `DATABASE_URL` instead.

### n8n

Runs at http://127.0.0.1:5678 with its own persistent volume. Pair it with
RelayCLI by giving n8n webhooks for the agent to call (via the `fetch` MCP
connector) or by driving n8n's REST API.

## Notes

- The image ships git, ripgrep, and node/npx, so native tools and the MCP
  presets work out of the box.
- The agent runs as the non-root `relay` user.
- Nothing here weakens RelayCLI's safety rails: permission modes still gate
  edits/commands, and the web UI's Host/Origin guard stays on.
