import asyncio, json, os, pathlib
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse

app = FastAPI()

# ---------------------------------------------------------------------------
# Configuration — override via environment variables
# ---------------------------------------------------------------------------
AUDIT_LOG     = pathlib.Path(os.getenv("AUDIT_LOG_PATH", "/vault/audit.log"))
HISTORY_LIMIT = int(os.getenv("HISTORY_LIMIT", "200"))

# Comma-separated KV mount names to watch for secret read/write/delete events.
# Add any custom KV mounts here or set KV_MOUNTS env var.
_kv_env   = os.getenv("KV_MOUNTS", "secret")
KV_MOUNTS = {m.strip() for m in _kv_env.split(",") if m.strip()}

# ---------------------------------------------------------------------------
# Agent colours — add an entry for each role name Vault assigns to your agents.
# The role name comes from auth metadata (JWT role / SPIFFE role).
# Any agent not listed here gets the default grey (#64748b).
# ---------------------------------------------------------------------------
AGENT_COLORS = {
    "n8n":            "#3b82f6",   # blue
    "claude-desktop": "#f97316",   # orange
    "claude-code":    "#22c55e",   # green
    "chatgpt":        "#ef4444",   # red
    "gemini":         "#a855f7",   # purple
    "python-agent":   "#14b8a6",   # teal
}

# Vault internal housekeeping paths — shown dimmed, hidden by default in the UI
INTERNAL_PATHS = {"sys/mounts", "sys/capabilities-self", "auth/token/lookup-self"}


# ---------------------------------------------------------------------------
# Event parsing
# ---------------------------------------------------------------------------
def parse_vault_event(line: str) -> dict | None:
    """Parse one Vault audit log JSON line into a UI event dict."""
    try:
        raw = json.loads(line)
    except Exception:
        return None

    # Only process response events — they contain the operation result
    if raw.get("type") != "response":
        return None

    req   = raw.get("request", {})
    auth  = raw.get("auth", {})
    path  = req.get("path", "")
    error = raw.get("error") or ""
    ts    = raw.get("time", "")

    metadata      = auth.get("metadata") or {}
    agent         = metadata.get("role", "")
    authorized_by = metadata.get("authorized_by", "")
    policies      = auth.get("policies") or []
    entity_id     = auth.get("entity_id") or ""

    # ── JWT login ─────────────────────────────────────────────────────────────
    if path == "auth/jwt/login":
        if not agent:
            return None
        detail = f"policy: {', '.join(policies)}"
        if authorized_by:
            detail += f"  ·  authorized by: {authorized_by}"
        return {
            "event_type":    "auth",
            "auth_method":   "jwt",
            "agent":         agent,
            "color":         AGENT_COLORS.get(agent, "#64748b"),
            "policies":      policies,
            "entity_id":     entity_id,
            "authorized_by": authorized_by,
            "timestamp":     ts,
            "status":        "error" if error else "success",
            "detail":        detail,
            "error":         error,
        }

    # ── SPIFFE / SPIRE login ──────────────────────────────────────────────────
    if path == "auth/spiffe/login":
        if not agent:
            return None
        detail = f"policy: {', '.join(policies)}"
        return {
            "event_type":    "auth",
            "auth_method":   "spire",
            "agent":         agent,
            "color":         AGENT_COLORS.get(agent, "#64748b"),
            "policies":      policies,
            "entity_id":     entity_id,
            "authorized_by": "",
            "timestamp":     ts,
            "status":        "error" if error else "success",
            "detail":        detail,
            "error":         error,
        }

    # ── PKI cert issuance ─────────────────────────────────────────────────────
    if path.startswith("pki/issue/"):
        role      = path.split("/")[-1]
        resp_data = (raw.get("response") or {}).get("data") or {}
        serial    = resp_data.get("serial_number", "")
        detail    = f"CN={role}  ·  role={role}"
        # serial_number is HMAC-hashed in the audit log — only show if unhashed
        if serial and not serial.startswith("hmac-"):
            detail += f"  ·  serial={serial}"
        return {
            "event_type":  "pki",
            "auth_method": "pki",
            "agent":       agent or role,
            "color":       AGENT_COLORS.get(agent or role, "#64748b"),
            "policies":    policies,
            "entity_id":   entity_id,
            "timestamp":   ts,
            "status":      "error" if error else "success",
            "detail":      detail,
            "error":       error,
            "cn":          role,
            "role":        role,
            "serial":      serial,
        }

    # ── Skip unauthenticated / token renewal paths ────────────────────────────
    if not agent:
        return None
    if path in INTERNAL_PATHS:
        return {
            "event_type": "internal",
            "agent":      agent,
            "path":       path,
            "color":      AGENT_COLORS.get(agent, "#64748b"),
            "timestamp":  ts,
            "status":     "success",
            "entity_id":  entity_id,
        }
    if path.startswith("auth/token/"):
        return None

    # ── KV secret read / write / delete ──────────────────────────────────────
    mount = path.split("/")[0]
    if mount in KV_MOUNTS:
        # Strip KV v2 path prefixes for display
        display = path
        for prefix in (f"{mount}/data/", f"{mount}/metadata/"):
            display = display.replace(prefix, "")
        status = "denied" if "permission denied" in error else "success"
        return {
            "event_type": "read",
            "agent":      agent,
            "color":      AGENT_COLORS.get(agent, "#64748b"),
            "path":       display,
            "full_path":  path,
            "operation":  req.get("operation", ""),
            "policies":   policies,
            "entity_id":  entity_id,
            "timestamp":  ts,
            "status":     status,
            "detail":     display,
            "error":      error if status == "denied" else "",
        }

    return None


# ---------------------------------------------------------------------------
# Log tailing
# ---------------------------------------------------------------------------
async def tail_log():
    """Async generator: tail AUDIT_LOG, yielding parsed events as they arrive."""
    if not AUDIT_LOG.exists():
        yield {"event_type": "error", "message": f"Audit log not found at {AUDIT_LOG}"}
        return

    with open(AUDIT_LOG, "r") as f:
        f.seek(0, 2)   # start at EOF — only new events
        while True:
            line = f.readline()
            if line:
                ev = parse_vault_event(line.strip())
                if ev:
                    yield ev
            else:
                await asyncio.sleep(0.05)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/events")
async def sse_events(request: Request):
    """SSE stream — browser connects here and receives real-time events."""
    async def stream():
        try:
            async for ev in tail_log():
                if await request.is_disconnected():
                    break
                yield f"data: {json.dumps(ev)}\n\n"
        except asyncio.CancelledError:
            pass

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":    "no-cache",
            "X-Accel-Buffering": "no",
            "Connection":       "keep-alive",
        },
    )


@app.get("/history")
async def history():
    """Return the last HISTORY_LIMIT parsed events for page-load replay."""
    if not AUDIT_LOG.exists():
        return JSONResponse({"events": [], "error": f"Audit log not found at {AUDIT_LOG}"})
    events = []
    with open(AUDIT_LOG, "r") as f:
        for line in f:
            line = line.strip()
            if line:
                ev = parse_vault_event(line)
                if ev:
                    events.append(ev)
    return JSONResponse({"events": events[-HISTORY_LIMIT:]})


@app.get("/")
async def index():
    html = pathlib.Path("/app/static/index.html")
    if html.exists():
        return HTMLResponse(html.read_text())
    return HTMLResponse("<h1>Frontend missing — mount static/index.html</h1>", status_code=500)
