# vault-flow

Real-time HashiCorp Vault audit log monitor with a live flow diagram and event feed.

vault-flow tails your Vault audit log and displays every authentication, secret access, PKI certificate issuance, and access denial as it happens — with animated flow diagrams, per-agent colour coding, and a 200-event replay buffer on page load.

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ ● VAULT FLOW MONITOR   secure agent identity · real-time audit               │
│                                    events 47  auths 12  certs 3  connecting… │
├───────────────────┬──────────────────────────────────────────────────────────┤
│  Auth Flow        │  claude-code    JWT AUTH                        14:03:21 │
│                   │  policy: claude-code-agent                               │
│  ┌─────────────┐  │  [claude-code-agent]                                     │
│  │  AI AGENT   │  │                                                          │
│  └──┬──────┬───┘  │  python-agent   SPIRE AUTH                     14:02:55 │
│     │      │      │  policy: python-agent                                    │
│ ┌───┴──┐ ┌─┴────┐ │  [python-agent]                                          │
│ │AUTHN │ │SPIRE │ │                                                          │
│ │OIDC  │ │SVID  │ │  claude-code    PKI CERT                        14:02:40 │
│ └───┬──┘ └─┬────┘ │  CN=claude-code · role=claude-code                       │
│     └──┬───┘      │  [claude-code-agent]                                     │
│  ┌─────┴───────┐  │                                                          │
│  │ VAULT AUTH  │  │  n8n            READ                            14:01:12 │
│  │JWT·SPIFFE   │  │  agents/n8n/config                                       │
│  └──────┬──────┘  │  [n8n-agent]                                             │
│  ┌──────┴──────┐  │                                                          │
│  │  VAULT-MCP  │  │  chatgpt        READ                   ✗ denied 13:58:44 │
│  └──────┬──────┘  │  agents/claude-code/config                               │
│  ┌──────┴──────┐  │  permission denied                                       │
│  │ VAULT KV·PKI│  │                                                          │
│  └─────────────┘  │                                                          │
└───────────────────┴──────────────────────────────────────────────────────────┘
```

## Features

- **Live diagram** — SVG flow diagram animates with each event; JWT auth lights up Authentik (purple), SPIFFE auth lights up SPIRE (cyan), secret reads pulse through vault-mcp to KV, PKI issuances glow yellow
- **Event cards** — per-agent colour coding, policy chips, status badges (success / denied / error), timestamps
- **Event types** — JWT auth, SPIFFE/SPIRE auth, KV secret read/write/delete, PKI cert issuance, access denied
- **History replay** — last 200 events loaded on page open (configurable)
- **SSE streaming** — no polling; browser receives events via Server-Sent Events as they hit the audit log
- **Zero JS dependencies** — plain HTML/CSS/JS, no bundler needed
- **Configurable** — agent colours, KV mounts, audit log path, history depth all via env vars or code

## Quick start

### Prerequisites

- Docker + Docker Compose
- HashiCorp Vault with **audit logging enabled** (see [Vault audit log setup](#vault-audit-log-setup))

### 1. Clone

```bash
git clone https://github.com/your-org/vault-flow.git
cd vault-flow
```

### 2. Configure

Copy the example env file and set your audit log path:

```bash
cp .env.example .env
# Edit .env — at minimum set AUDIT_LOG_PATH
```

Or just pass `AUDIT_LOG_PATH` directly:

```bash
export AUDIT_LOG_PATH=/srv/vault/data/audit.log
```

### 3. Run

```bash
docker compose up -d --build
```

Open [http://localhost:9000](http://localhost:9000).

Authenticate an agent to Vault and watch the events flow.

## Vault audit log setup

vault-flow reads Vault's file audit device. Enable it if you haven't already:

```bash
vault audit enable file file_path=/vault/logs/audit.log
```

If Vault is running in Docker, the log is inside the container. Bind-mount it to the host so vault-flow can read it:

```yaml
# In your Vault docker-compose.yml
volumes:
  - /srv/vault/data:/vault/data
```

Then set `AUDIT_LOG_PATH=/srv/vault/data/audit.log` (or wherever you mounted it).

> **Note:** Vault HMAC-hashes sensitive fields in the audit log (secret values, token contents, PKI `common_name`, `serial_number`). vault-flow only reads non-sensitive fields: paths, operation types, policies, timestamps, and auth metadata.

## Configuration

### Environment variables

| Variable | Default | Description |
|---|---|---|
| `AUDIT_LOG_PATH` | `/vault/audit.log` | Path to Vault audit log **inside the container** |
| `HISTORY_LIMIT` | `200` | Number of past events to return on page load |
| `KV_MOUNTS` | `secret` | Comma-separated KV v2 mount names to watch (e.g. `secret,demo,kv`) |
| `PORT` | `9000` | Host port to expose (docker-compose only) |

### Agent colours

Edit `AGENT_COLORS` in [app.py](app.py) to match your Vault role names:

```python
AGENT_COLORS = {
    "my-service":  "#3b82f6",   # blue
    "my-worker":   "#f97316",   # orange
    "my-api":      "#22c55e",   # green
}
```

The key is the **Vault role name** — the value of the `role` field in the auth token's metadata. For JWT auth this is the role passed to `vault write auth/jwt/role/<name>`. For SPIFFE auth it's the role in `vault write auth/spiffe/role/<name>`.

Any agent not listed gets a neutral grey (`#64748b`).

### Watching multiple KV mounts

Set `KV_MOUNTS` to a comma-separated list:

```yaml
environment:
  - KV_MOUNTS=secret,demo,kv
```

### Internal path filtering

Vault makes several internal calls that aren't interesting to watch (`sys/mounts`, `sys/capabilities-self`, `auth/token/lookup-self`). These are hidden by default and shown dimmed when the "show sys/mounts" toggle is on.

To add more paths to hide, edit `INTERNAL_PATHS` in `app.py`:

```python
INTERNAL_PATHS = {"sys/mounts", "sys/capabilities-self", "auth/token/lookup-self"}
```

## Adding a new agent

1. Create the Vault role (JWT or SPIFFE — see your auth method docs)
2. Add the agent colour to `AGENT_COLORS` in `app.py` and rebuild:
   ```bash
   docker compose up -d --build
   ```
3. Add the agent to the legend in `static/index.html` (live reload — no rebuild):
   ```html
   <div class="legend-row">
     <div class="legend-dot" style="background:#your-colour"></div>
     <span class="legend-name">your-agent</span>
     <span class="legend-count" id="cnt-your-agent">0</span>
   </div>
   ```

> The `static/` directory is volume-mounted, so changes to `index.html` take effect immediately without rebuilding the image.

## HCP Vault Dedicated (HTTP push)

HCP Vault Dedicated cannot write to a local file — it pushes audit logs to an
HTTP endpoint. vault-flow's `/ingest` endpoint receives these pushes and streams
them to the browser in real time, exactly like the local file path.

### How it works

```
HCP Vault Dedicated
  │  POST https://your-vault-flow-host/ingest
  │  Body: NDJSON (one audit event per line)
  ▼
vault-flow /ingest
  │  parse → _combined_queue → _broadcast
  ▼
SSE /events → browser (live diagram + event cards)
```

`/history` serves from an in-memory ring buffer (last `HISTORY_LIMIT` events)
since there is no local file. The buffer resets on container restart.

### Setup

**1. Make vault-flow reachable from the internet**

HCP Vault Dedicated is a managed cloud service — it needs to reach your
`/ingest` endpoint over HTTPS. Options:

- Put vault-flow behind a reverse proxy (Traefik, nginx) with a valid TLS cert
- Use a tunnel for testing: `ngrok http 9000` → use the ngrok HTTPS URL

**2. Set an auth token**

Set `INGEST_BEARER_TOKEN` in your `.env` or `docker-compose.yml`:

```bash
INGEST_BEARER_TOKEN=your-secret-token-here
```

**3. Configure HCP Vault Dedicated**

In the HCP portal → your cluster → **Audit Logs** → **Add log streaming**:

| Field | Value |
|---|---|
| Provider | Generic HTTP Sink |
| URI | `https://your-host/ingest` |
| Method | POST |
| Authentication Strategy | Bearer |
| Token | your `INGEST_BEARER_TOKEN` value |
| Encoding | NDJSON (recommended) |
| Compression | optional — vault-flow handles gzip |

Click **Save**. Logs typically start flowing within a few minutes (up to 20 min
for full enablement per HCP docs).

> **Note:** HCP Vault Dedicated only supports streaming to one HTTP endpoint at
> a time. If you need to send logs elsewhere simultaneously, use a log aggregator
> (e.g. Fluent Bit, Vector) as a fan-out proxy in front of vault-flow.

### Testing /ingest manually

```bash
# NDJSON — simulate a JWT auth event
curl -X POST http://localhost:9000/ingest \
  -H "Content-Type: application/x-ndjson" \
  -H "Authorization: Bearer your-secret-token-here" \
  --data-binary '{"type":"response","time":"2026-01-01T10:00:00Z","request":{"path":"auth/jwt/login","operation":"update"},"auth":{"metadata":{"role":"claude-code"},"policies":["claude-code-agent"],"entity_id":"abc-123"},"error":null}'

# JSON array — simulate two events at once
curl -X POST http://localhost:9000/ingest \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your-secret-token-here" \
  -d '[
    {"type":"response","time":"2026-01-01T10:01:00Z","request":{"path":"secret/data/agents/n8n/config","operation":"read"},"auth":{"metadata":{"role":"n8n"},"policies":["n8n-agent"],"entity_id":"def-456"},"error":null},
    {"type":"response","time":"2026-01-01T10:02:00Z","request":{"path":"secret/data/agents/claude-code/config","operation":"read"},"auth":{"metadata":{"role":"n8n"},"policies":["n8n-agent"],"entity_id":"def-456"},"error":"1 error occurred: permission denied"}
  ]'

# Verify events appear
curl http://localhost:9000/history | python3 -m json.tool
```

### Running without a local Vault audit log

If you only have HCP Vault Dedicated (no local file), set `AUDIT_LOG_PATH` to a
path that does not exist — vault-flow will skip file tailing and use `/ingest`
as the sole event source:

```yaml
# docker-compose.yml
environment:
  - AUDIT_LOG_PATH=/nonexistent
  - INGEST_BEARER_TOKEN=your-secret-token
# Remove the audit log volume mount
```

## Architecture

```
Vault audit log (file)          HCP Vault Dedicated (HTTP push)
        │                                    │
        │  _run_file_tail()                  │  POST /ingest
        │  readline loop, 50ms poll          │  JSON array or NDJSON
        └──────────────┬─────────────────────┘
                       │
                _combined_queue (asyncio.Queue)
                       │
                _broadcast() task
                       │ fan-out
          ┌────────────┼────────────┐
          ▼            ▼            ▼
     client queue  client queue  client queue  (one per SSE connection)
          │
     GET /events ── SSE stream ──► browser (EventSource)
     GET /history ── JSON ──────► page load replay
                                       │
                                buildCard() + animateFlow()
                                       │
                               live SVG diagram + event feed
```

**Backend (`app.py`):**
- FastAPI with two routes: `/events` (SSE) and `/history` (JSON)
- `tail_log()` is an async generator that `readline()`s the audit log and sleeps 50ms when there's nothing new
- `parse_vault_event()` filters and shapes raw Vault JSON into typed event dicts
- No database — history is re-parsed from the log file on each `/history` request

**Frontend (`static/index.html`):**
- Single HTML file, no build step, no JS framework
- `EventSource('/events')` drives live updates
- `fetch('/history')` on page load replays recent events
- SVG diagram nodes are animated by `glowNode()` when events arrive
- `static/` is volume-mounted so you can edit the UI without rebuilding the Docker image

**Volume mounts:**
| Host path | Container path | Purpose |
|---|---|---|
| `$AUDIT_LOG_PATH` | `/vault/audit.log` | Vault audit log (read-only) |
| `./static` | `/app/static` | Frontend HTML (live, no rebuild) |

## Development

### Editing the frontend

Just edit `static/index.html` and refresh the browser. No restart needed.

### Editing the backend

```bash
# After changing app.py:
docker compose up -d --build
```

### Local dev with a sample log

Capture some real Vault audit events to a file, then replay them:

```bash
# On your Vault host, copy some recent audit log lines
tail -n 500 /srv/vault/data/audit.log > sample-audit.log

# Run against the sample
docker compose -f docker-compose.dev.yml up --build
```

Open [http://localhost:9000](http://localhost:9000) — the history endpoint will show the captured events immediately.

## Vault auth method compatibility

| Auth method | Vault path | vault-flow event type | Notes |
|---|---|---|---|
| JWT / OIDC | `auth/jwt/login` | `auth` (JWT AUTH) | Role name from `auth.metadata.role` |
| SPIFFE / SPIRE | `auth/spiffe/login` | `auth` (SPIRE AUTH) | Role name from `auth.metadata.role` |
| KV v2 read/write | `<mount>/data/<path>` | `read` | Mount must be in `KV_MOUNTS` |
| PKI issue | `pki/issue/<role>` | `pki` (PKI CERT) | `common_name` is HMAC-hashed; role name used instead |

Other auth methods (AppRole, TLS cert, AWS, etc.) will show as unhandled and be silently dropped. Open a PR or issue to add support.

## License

MIT
