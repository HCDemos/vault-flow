import asyncio, base64, collections, gzip, json, os, pathlib
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse

# ---------------------------------------------------------------------------
# Configuration — override via environment variables
# ---------------------------------------------------------------------------
AUDIT_LOG     = pathlib.Path(os.getenv("AUDIT_LOG_PATH", "/vault/audit.log"))
HISTORY_LIMIT = int(os.getenv("HISTORY_LIMIT", "200"))

# Comma-separated KV mount names to watch for secret read/write/delete events.
_kv_env   = os.getenv("KV_MOUNTS", "secret")
KV_MOUNTS = {m.strip() for m in _kv_env.split(",") if m.strip()}

# Optional auth for the /ingest endpoint.
# Set INGEST_BEARER_TOKEN for Bearer token auth (recommended).
# Set INGEST_USERNAME + INGEST_PASSWORD for HTTP Basic auth.
# If neither is set, /ingest accepts all requests (suitable for trusted networks).
INGEST_BEARER_TOKEN = os.getenv("INGEST_BEARER_TOKEN", "")
INGEST_USERNAME     = os.getenv("INGEST_USERNAME", "")
INGEST_PASSWORD     = os.getenv("INGEST_PASSWORD", "")

# ---------------------------------------------------------------------------
# Agent colours — key is the Vault role name assigned to each agent.
# Any agent not listed gets default grey (#64748b).
# ---------------------------------------------------------------------------
AGENT_COLORS = {
    "n8n":            "#3b82f6",   # blue
    "claude-desktop": "#f97316",   # orange
    "claude-code":    "#22c55e",   # green
    "chatgpt":        "#ef4444",   # red
    "gemini":         "#a855f7",   # purple
    "python-agent":   "#14b8a6",   # teal
}

# Vault internal housekeeping paths — hidden by default in the UI
INTERNAL_PATHS = {"sys/mounts", "sys/capabilities-self", "auth/token/lookup-self"}

# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------
_http_buffer: collections.deque   = collections.deque(maxlen=HISTORY_LIMIT)
_combined_queue: asyncio.Queue    = asyncio.Queue()
_subscribers: list[asyncio.Queue] = []


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
# Background tasks
# ---------------------------------------------------------------------------
async def _run_file_tail() -> None:
    """Tail AUDIT_LOG and push parsed events into _combined_queue."""
    if not AUDIT_LOG.exists():
        return   # HCP-only mode — no local file to tail
    with open(AUDIT_LOG, "r") as f:
        f.seek(0, 2)   # start at EOF
        while True:
            line = f.readline()
            if line:
                ev = parse_vault_event(line.strip())
                if ev:
                    await _combined_queue.put(ev)
            else:
                await asyncio.sleep(0.05)


async def _broadcast() -> None:
    """Drain _combined_queue and fan-out to all SSE subscriber queues."""
    while True:
        ev = await _combined_queue.get()
        dead = []
        for q in list(_subscribers):
            try:
                q.put_nowait(ev)
            except asyncio.QueueFull:
                dead.append(q)   # client too slow / disconnected
        for q in dead:
            if q in _subscribers:
                _subscribers.remove(q)


@asynccontextmanager
async def lifespan(app: FastAPI):
    asyncio.create_task(_run_file_tail())
    asyncio.create_task(_broadcast())
    yield


app = FastAPI(lifespan=lifespan)


# ---------------------------------------------------------------------------
# Auth helper for /ingest
# ---------------------------------------------------------------------------
def _verify_ingest_auth(request: Request) -> None:
    """Raise HTTP 401 if the ingest request fails authentication."""
    if INGEST_BEARER_TOKEN:
        if request.headers.get("Authorization") != f"Bearer {INGEST_BEARER_TOKEN}":
            raise HTTPException(status_code=401, detail="Unauthorized")
    elif INGEST_USERNAME and INGEST_PASSWORD:
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Basic "):
            raise HTTPException(status_code=401, detail="Unauthorized")
        try:
            decoded = base64.b64decode(auth[6:]).decode()
        except Exception:
            raise HTTPException(status_code=401, detail="Unauthorized")
        if decoded != f"{INGEST_USERNAME}:{INGEST_PASSWORD}":
            raise HTTPException(status_code=401, detail="Unauthorized")
    # No auth configured — accept all (trusted network / dev mode)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.post("/ingest")
@app.put("/ingest")
@app.patch("/ingest")
async def ingest(request: Request) -> Response:
    """
    Receive Vault audit log events pushed by HCP Vault Dedicated (or any
    HTTP log sink). Accepts JSON array or NDJSON, with optional gzip encoding.

    Configure HCP Vault Dedicated to POST to: https://<your-host>/ingest
    Recommended HCP settings: Method=POST, Encoding=NDJSON, Auth=Bearer.
    """
    _verify_ingest_auth(request)

    body = await request.body()
    if request.headers.get("Content-Encoding") == "gzip":
        try:
            body = gzip.decompress(body)
        except Exception:
            raise HTTPException(status_code=400, detail="Failed to decompress gzip body")

    text = body.decode("utf-8", errors="replace").strip()
    if not text:
        return Response(status_code=200)

    # Auto-detect encoding: JSON array vs NDJSON
    if text.startswith("["):
        try:
            items = json.loads(text)
        except json.JSONDecodeError as e:
            raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}")
        lines = [json.dumps(item) for item in items]
    else:
        lines = [ln for ln in text.splitlines() if ln.strip()]

    for line in lines:
        ev = parse_vault_event(line)
        if ev:
            _http_buffer.append(ev)
            await _combined_queue.put(ev)

    return Response(status_code=200)


@app.get("/events")
async def sse_events(request: Request) -> StreamingResponse:
    """SSE stream — browser connects here and receives real-time events."""
    q: asyncio.Queue = asyncio.Queue(maxsize=256)
    _subscribers.append(q)

    async def stream():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=1.0)
                    yield f"data: {json.dumps(ev)}\n\n"
                except asyncio.TimeoutError:
                    pass   # keep-alive loop — check disconnect and continue
        except asyncio.CancelledError:
            pass
        finally:
            if q in _subscribers:
                _subscribers.remove(q)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":     "no-cache",
            "X-Accel-Buffering": "no",
            "Connection":        "keep-alive",
        },
    )


@app.get("/history")
async def history() -> JSONResponse:
    """
    Return recent parsed events for page-load replay.

    Local file mode:  reads the last HISTORY_LIMIT events from AUDIT_LOG.
    HCP / HTTP mode:  returns the in-memory buffer populated by /ingest.
    """
    events = []
    if AUDIT_LOG.exists():
        with open(AUDIT_LOG, "r") as f:
            for line in f:
                ev = parse_vault_event(line.strip())
                if ev:
                    events.append(ev)
        events = events[-HISTORY_LIMIT:]
    if not events:
        # HCP mode or empty file — use the in-memory HTTP buffer
        events = list(_http_buffer)
    return JSONResponse({"events": events})


@app.get("/")
async def index() -> HTMLResponse:
    html = pathlib.Path("/app/static/index.html")
    if html.exists():
        return HTMLResponse(html.read_text())
    return HTMLResponse("<h1>Frontend missing — mount static/index.html</h1>", status_code=500)
