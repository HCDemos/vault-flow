# 🔐 vault-flow

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

## ✨ Features

- 🗺️ **Live diagram** — SVG flow diagram animates with each event; JWT auth lights up Authentik (purple), SPIFFE auth lights up SPIRE (cyan), secret reads pulse through vault-mcp to KV, PKI issuances glow yellow
- 🃏 **Event cards** — per-agent colour coding, policy chips, status badges (success / denied / error), timestamps
- 📡 **SSE streaming** — no polling; browser receives events via Server-Sent Events as they hit the audit log
- 🕐 **History replay** — last 200 events loaded on page open (configurable)
- ☁️ **HCP Vault Dedicated** — `/ingest` endpoint receives HTTP-pushed logs from HCP, no file needed
- 🪶 **Zero JS dependencies** — plain HTML/CSS/JS, no bundler, no framework
- ⚙️ **Configurable** — agent colours, KV mounts, audit log path, history depth all via env vars

## 🚀 Quick start

### Prerequisites

- Docker + Docker Compose
- HashiCorp Vault with **audit logging enabled** (see [Vault audit log setup](#-vault-audit-log-setup))

### 1. Clone

```bash
git clone https://github.com/your-org/vault-flow.git
cd vault-flow
```

### 2. Configure

```bash
cp .env.example .env
# Edit .env — at minimum set AUDIT_LOG_PATH
```

Or pass it directly:

```bash
export AUDIT_LOG_PATH=/srv/vault/data/audit.log
```

### 3. Run

```bash
docker compose up -d --build
```

Open [http://localhost:9000](http://localhost:9000) and authenticate an agent to Vault — watch the events flow.

## 📋 Vault audit log setup

vault-flow reads Vault's file audit device. Enable it if you haven't already:

```bash
vault audit enable file file_path=/vault/logs/audit.log
```

If Vault is running in Docker, bind-mount the log file to the host so vault-flow can read it:

```yaml
# In your Vault docker-compose.yml
volumes:
  - /srv/vault/data:/vault/data
```

Then set `AUDIT_LOG_PATH=/srv/vault/data/audit.log`.

> **🔒 Privacy note:** Vault HMAC-hashes sensitive fields in the audit log (secret values, token contents, PKI `common_name`, `serial_number`). vault-flow only reads non-sensitive fields: paths, operation types, policies, timestamps, and auth metadata.

## ⚙️ Configuration

### Environment variables

| Variable | Default | Description |
|---|---|---|
| `AUDIT_LOG_PATH` | `/vault/audit.log` | Path to Vault audit log **inside the container** |
| `HISTORY_LIMIT` | `200` | Number of past events to return on page load |
| `KV_MOUNTS` | `secret` | Comma-separated KV v2 mount names to watch (e.g. `secret,demo,kv`) |
| `PORT` | `9000` | Host port to expose (docker-compose only) |
| `INGEST_BEARER_TOKEN` | _(none)_ | Bearer token to protect `/ingest` (recommended for HCP) |
| `INGEST_USERNAME` | _(none)_ | Basic auth username for `/ingest` |
| `INGEST_PASSWORD` | _(none)_ | Basic auth password for `/ingest` |

### Agent colours

Edit `AGENT_COLORS` in [app.py](app.py) to match your Vault role names:

```python
AGENT_COLORS = {
    "my-service":  "#3b82f6",   # blue
    "my-worker":   "#f97316",   # orange
    "my-api":      "#22c55e",   # green
}
```

The key is the **Vault role name** — the `role` field in the auth token's metadata. For JWT auth this is the role passed to `vault write auth/jwt/role/<name>`. For SPIFFE auth it's the role in `vault write auth/spiffe/role/<name>`. Any agent not listed gets a neutral grey (`#64748b`).

### Watching multiple KV mounts

```yaml
environment:
  - KV_MOUNTS=secret,demo,kv
```

### Internal path filtering

Vault's internal housekeeping calls (`sys/mounts`, `sys/capabilities-self`, `auth/token/lookup-self`) are hidden by default and shown dimmed when the "show sys/mounts" toggle is on. To add more paths, edit `INTERNAL_PATHS` in `app.py`.

## 🤖 Adding a new agent

1. Create the Vault role (JWT or SPIFFE)
2. Add the agent colour to `AGENT_COLORS` in `app.py` and rebuild:
   ```bash
   docker compose up -d --build
   ```
3. Add the agent to the legend in `static/index.html` (live — no rebuild needed):
   ```html
   <div class="legend-row">
     <div class="legend-dot" style="background:#your-colour"></div>
     <span class="legend-name">your-agent</span>
     <span class="legend-count" id="cnt-your-agent">0</span>
   </div>
   ```

> 💡 The `static/` directory is volume-mounted, so changes to `index.html` take effect on browser refresh — no image rebuild needed.

## ☁️ HCP Vault Dedicated (HTTP push)

HCP Vault Dedicated cannot write to a local file — it pushes audit logs to an HTTP endpoint you configure. vault-flow's `/ingest` endpoint receives these pushes and streams them to the browser in real time, exactly like the local file path.

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

`/history` serves from an in-memory ring buffer (last `HISTORY_LIMIT` events) since there is no local file. The buffer resets on container restart.

### Setup

**1️⃣  Make vault-flow reachable from the internet**

HCP Vault Dedicated is a managed cloud service — it needs to reach your `/ingest` endpoint over HTTPS. Options:

- Put vault-flow behind a reverse proxy (Traefik, nginx) with a valid TLS cert
- Use a tunnel for testing: `ngrok http 9000` → use the ngrok HTTPS URL

**2️⃣  Set an auth token**

```bash
# .env
INGEST_BEARER_TOKEN=your-secret-token-here
```

**3️⃣  Configure HCP Vault Dedicated**

In the HCP portal → your cluster → **Audit Logs** → **Add log streaming**:

| Field | Value |
|---|---|
| Provider | Generic HTTP Sink |
| URI | `https://your-host/ingest` |
| Method | `POST` |
| Authentication Strategy | Bearer |
| Token | your `INGEST_BEARER_TOKEN` value |
| Encoding | NDJSON (recommended) |
| Compression | optional — vault-flow handles gzip |

Click **Save**. Logs typically start flowing within a few minutes (up to 20 min per HCP docs).

> **⚠️ Note:** HCP Vault Dedicated only supports streaming to one HTTP endpoint at a time. If you need to fan out to multiple destinations, put a log aggregator (e.g. Fluent Bit, Vector) in front of vault-flow.

### Testing `/ingest` manually

```bash
# NDJSON — simulate a JWT auth event
curl -X POST http://localhost:9000/ingest \
  -H "Content-Type: application/x-ndjson" \
  -H "Authorization: Bearer your-secret-token-here" \
  --data-binary '{"type":"response","time":"2026-01-01T10:00:00Z","request":{"path":"auth/jwt/login","operation":"update"},"auth":{"metadata":{"role":"claude-code"},"policies":["claude-code-agent"],"entity_id":"abc-123"},"error":null}'

# JSON array — two events at once
curl -X POST http://localhost:9000/ingest \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your-secret-token-here" \
  -d '[
    {"type":"response","time":"2026-01-01T10:01:00Z","request":{"path":"secret/data/agents/n8n/config","operation":"read"},"auth":{"metadata":{"role":"n8n"},"policies":["n8n-agent"],"entity_id":"def-456"},"error":null},
    {"type":"response","time":"2026-01-01T10:02:00Z","request":{"path":"secret/data/agents/claude-code/config","operation":"read"},"auth":{"metadata":{"role":"n8n"},"policies":["n8n-agent"],"entity_id":"def-456"},"error":"1 error occurred: permission denied"}
  ]'

# Verify events in history
curl http://localhost:9000/history | python3 -m json.tool
```

### HCP-only mode (no local file)

If you have no local Vault file, point `AUDIT_LOG_PATH` at a non-existent path — vault-flow skips file tailing and uses `/ingest` as the sole source:

```yaml
environment:
  - AUDIT_LOG_PATH=/nonexistent
  - INGEST_BEARER_TOKEN=your-secret-token
# Remove the audit log volume mount
```

## 🏗️ Architecture

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
- FastAPI with background tasks (`_run_file_tail`, `_broadcast`) started via `lifespan`
- `parse_vault_event()` filters and shapes raw Vault audit JSON into typed event dicts
- `/ingest` handles JSON array, NDJSON, gzip, and optional Bearer/Basic auth
- `/history` reads from the local file if it exists, otherwise falls back to the in-memory HTTP buffer

**Frontend (`static/index.html`):**
- Single HTML file, no build step, no JS framework
- `EventSource('/events')` drives live updates
- `fetch('/history')` on page load replays recent events
- SVG diagram nodes animated by `glowNode()` when events arrive
- Volume-mounted — edit the UI without rebuilding the Docker image

**Volume mounts:**

| Host path | Container path | Purpose |
|---|---|---|
| `$AUDIT_LOG_PATH` | `/vault/audit.log` | Vault audit log (read-only) |
| `./static` | `/app/static` | Frontend HTML (live, no rebuild) |

## 🛠️ Development

### Editing the frontend

Edit `static/index.html` and refresh the browser. No restart needed.

### Editing the backend

```bash
# After changing app.py:
docker compose up -d --build
```

### Local dev with a sample log

```bash
# Capture recent audit events from your Vault host
tail -n 500 /srv/vault/data/audit.log > sample-audit.log

# Run against the sample
docker compose -f docker-compose.dev.yml up --build
```

Open [http://localhost:9000](http://localhost:9000) — history loads immediately from the sample file.

## 🔌 Vault auth method compatibility

| Auth method | Vault path | Event type | Notes |
|---|---|---|---|
| JWT / OIDC | `auth/jwt/login` | `auth` · JWT AUTH | Role from `auth.metadata.role` |
| SPIFFE / SPIRE | `auth/spiffe/login` | `auth` · SPIRE AUTH | Role from `auth.metadata.role` |
| KV v2 read/write | `<mount>/data/<path>` | `read` | Mount must be in `KV_MOUNTS` |
| PKI issue | `pki/issue/<role>` | `pki` · PKI CERT | `common_name` is HMAC-hashed; role name used |

Other auth methods (AppRole, TLS cert, AWS, etc.) are silently dropped. Open a PR or issue to add support.

## 📄 License

MIT
