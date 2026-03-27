"""
Email MCP Web App — FastAPI with per-user Gmail OAuth, JWT auth, and reset-password.
"""

import asyncio
import base64
import hashlib
import json
import os
import secrets
import sys
import uuid
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from pathlib import Path

import bcrypt
import psycopg2
import psycopg2.extras
from anthropic import Anthropic
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Header, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from jose import JWTError, jwt
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pydantic import BaseModel

load_dotenv(Path(__file__).parent / ".env")
# Allow OAuth over HTTP (EKS is behind ALB over HTTP)
os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")

ROOT = Path(__file__).parent
GMAIL_SERVER = ROOT / "servers" / "gmail_server.py"
POSTGRES_SERVER = ROOT / "servers" / "postgres_server.py"

# ── Auth config ────────────────────────────────────────────────────────────────
SECRET_KEY = os.getenv("SECRET_KEY", secrets.token_hex(32))
ALGORITHM = "HS256"
TOKEN_EXPIRE_HOURS = 24

GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
]
CREDS_PATH = Path(os.getenv("GMAIL_CREDENTIALS_PATH", str(ROOT / "credentials.json")))

# IMPORTANT: Register this URI in Google Cloud Console → Credentials → OAuth Client → Authorised redirect URIs
GMAIL_REDIRECT_URI = os.getenv(
    "GMAIL_REDIRECT_URI",
    "http://a498699022add45d19a4d4345b452839-377251697.eu-west-1.elb.amazonaws.com/auth/google/callback",
)
APP_BASE_URL = os.getenv(
    "APP_BASE_URL",
    "http://a498699022add45d19a4d4345b452839-377251697.eu-west-1.elb.amazonaws.com",
)


def _hash_pw(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def _verify_pw(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


# ── DB helpers ─────────────────────────────────────────────────────────────────

def _db():
    return psycopg2.connect(os.environ["DATABASE_URL"])


def ensure_schema():
    conn = _db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id                 SERIAL PRIMARY KEY,
            email              VARCHAR(255) UNIQUE NOT NULL,
            password_hash      VARCHAR(255) NOT NULL,
            created_at         TIMESTAMP NOT NULL DEFAULT NOW(),
            gmail_token        TEXT,
            reset_token        VARCHAR(64),
            reset_token_expiry TIMESTAMP
        )
    """)
    # Safely add columns to existing tables
    for col, defn in [
        ("gmail_token", "TEXT"),
        ("reset_token", "VARCHAR(64)"),
        ("reset_token_expiry", "TIMESTAMP"),
    ]:
        cur.execute(f"ALTER TABLE users ADD COLUMN IF NOT EXISTS {col} {defn}")
    conn.commit()
    cur.close()
    conn.close()


def db_get_user(email: str):
    conn = _db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM users WHERE email = %s", (email,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return dict(row) if row else None


def db_create_user(email: str, password: str):
    conn = _db()
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO users (email, password_hash) VALUES (%s, %s)",
            (email, _hash_pw(password)),
        )
        conn.commit()
    except psycopg2.errors.UniqueViolation:
        conn.rollback()
        raise HTTPException(status_code=400, detail="Email already registered")
    finally:
        cur.close()
        conn.close()


def db_save_gmail_token(email: str, token_json: str):
    conn = _db()
    cur = conn.cursor()
    cur.execute("UPDATE users SET gmail_token = %s WHERE email = %s", (token_json, email))
    conn.commit()
    cur.close()
    conn.close()


def db_set_reset_token(email: str, token: str, expiry: datetime):
    conn = _db()
    cur = conn.cursor()
    cur.execute(
        "UPDATE users SET reset_token = %s, reset_token_expiry = %s WHERE email = %s",
        (token, expiry, email),
    )
    conn.commit()
    cur.close()
    conn.close()


def db_get_user_by_reset_token(token: str):
    conn = _db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        "SELECT * FROM users WHERE reset_token = %s AND reset_token_expiry > NOW()",
        (token,),
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    return dict(row) if row else None


def db_update_password(email: str, new_password: str):
    conn = _db()
    cur = conn.cursor()
    cur.execute(
        "UPDATE users SET password_hash = %s, reset_token = NULL, reset_token_expiry = NULL WHERE email = %s",
        (_hash_pw(new_password), email),
    )
    conn.commit()
    cur.close()
    conn.close()


# ── JWT helpers ────────────────────────────────────────────────────────────────

def create_token(email: str) -> str:
    expires = datetime.utcnow() + timedelta(hours=TOKEN_EXPIRE_HOURS)
    return jwt.encode({"sub": email, "exp": expires}, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> str:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email = payload.get("sub")
        if not email:
            raise HTTPException(status_code=401, detail="Invalid token")
        return email
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")


def get_current_user(authorization: str = Header(...)) -> str:
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid authorization header")
    return decode_token(authorization[7:])


# ── Gmail token file helpers ───────────────────────────────────────────────────

def _token_path_for(email: str) -> Path:
    h = hashlib.md5(email.encode()).hexdigest()
    return Path(f"/tmp/gmail_{h}.json")


def ensure_token_file(email: str):
    """Write user's stored Gmail token to a temp file. Returns Path or None."""
    user = db_get_user(email)
    if not user or not user.get("gmail_token"):
        return None
    p = _token_path_for(email)
    p.write_text(user["gmail_token"])
    return p


# ── Email sending helper (for password reset) ──────────────────────────────────

def _send_reset_email(to_email: str, reset_link: str):
    """Send a password-reset email using the app's own Gmail credentials."""
    if not CREDS_PATH.exists():
        return
    try:
        from google.auth.transport.requests import Request as GRequest
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        app_token = Path(os.getenv("GMAIL_TOKEN_PATH", str(ROOT / "token.json")))
        if not app_token.exists():
            return
        creds = Credentials.from_authorized_user_file(str(app_token), GMAIL_SCOPES)
        if creds.expired and creds.refresh_token:
            creds.refresh(GRequest())
        service = build("gmail", "v1", credentials=creds)
        body = (
            "Hi,\n\n"
            "You requested a password reset for your Email MCP Assistant account.\n\n"
            f"Click the link below to reset your password (valid for 1 hour):\n\n{reset_link}\n\n"
            "If you did not request this, you can safely ignore this email.\n\n"
            "-- Email MCP Assistant\n"
        )
        msg = MIMEText(body)
        msg["to"] = to_email
        msg["subject"] = "Password Reset — Email MCP Assistant"
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        service.users().messages().send(userId="me", body={"raw": raw}).execute()
    except Exception as exc:
        print(f"[reset-email] Failed to send email: {exc}")


# ── MCP app (per-user instances) ───────────────────────────────────────────────

SYSTEM_PROMPT_TEMPLATE = """You are an intelligent email assistant for {user_email}.

You have two capabilities:
1. **Gmail** — send emails, list inbox, read full email content.
2. **PostgreSQL** — log every email event and query the log.

The user's email address is: {user_email}
When the user asks "what is my email" or "what is my email address", always reply with: {user_email}

### Rules you MUST follow:

SENDING EMAIL:
  Step 1 → call send_email(to, subject, body)
  Step 2 → call log_email(direction="sent", from_address="{user_email}", to_address=<to>,
            subject=<subject>, gmail_message_id=<id from step 1>, body_preview=<first 300 chars>)
  Step 3 → confirm to the user.

READING / LISTING EMAILS:
  Step 1 → call list_emails() or get_email()
  Step 2 → call log_email(direction="received", ...) for each email.
  Step 3 → present results clearly.

CHECKING LOGS / STATS:
  → call get_email_logs(), get_email_stats(), or search_emails_in_db() directly.

Always be concise, friendly, and confirm every action."""


class EmailMCPApp:
    def __init__(self):
        self.client = Anthropic()
        self.exit_stack = AsyncExitStack()
        self.tools: list[dict] = []
        self._sessions: dict[str, ClientSession] = {}
        self.ready = False

    async def connect(self, gmail_token_path=None):
        python = sys.executable

        async def _connect(label, command, args, env):
            merged = {**os.environ, **env}
            params = StdioServerParameters(command=command, args=args, env=merged)
            transport = await self.exit_stack.enter_async_context(stdio_client(params))
            session = await self.exit_stack.enter_async_context(
                ClientSession(transport[0], transport[1])
            )
            await session.initialize()
            for tool in (await session.list_tools()).tools:
                self.tools.append({
                    "name": tool.name,
                    "description": tool.description or "",
                    "input_schema": tool.inputSchema,
                })
                self._sessions[tool.name] = session
            print(f"  [MCP] {label} connected — {len((await session.list_tools()).tools)} tools")

        if gmail_token_path and Path(gmail_token_path).exists():
            await _connect("Gmail", python, [str(GMAIL_SERVER)], {
                "GMAIL_CREDENTIALS_PATH": os.getenv("GMAIL_CREDENTIALS_PATH", str(ROOT / "credentials.json")),
                "GMAIL_TOKEN_PATH": str(gmail_token_path),
            })
        else:
            print("  [MCP] Gmail skipped — no token (user must connect Gmail)")

        await _connect("PostgreSQL", python, [str(POSTGRES_SERVER)], {
            "DATABASE_URL": os.getenv("DATABASE_URL", "")
        })
        self.ready = True
        print(f"[App] Ready — {len(self.tools)} tools")

    async def _call_tool(self, name: str, arguments: dict) -> str:
        session = self._sessions.get(name)
        if not session:
            return json.dumps({"error": f"unknown tool: {name}"})
        result = await session.call_tool(name, arguments)
        return result.content[0].text if result.content else ""

    async def chat(self, user_email: str, message: str, history: list) -> tuple:
        system = SYSTEM_PROMPT_TEMPLATE.format(user_email=user_email)
        history.append({"role": "user", "content": message})
        while True:
            response = self.client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=4096,
                system=system,
                messages=history,
                tools=self.tools,
            )
            history.append({"role": "assistant", "content": response.content})
            if response.stop_reason != "tool_use":
                text = " ".join(b.text for b in response.content if hasattr(b, "text"))
                return text.strip(), history
            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                result_text = await self._call_tool(block.name, block.input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result_text,
                })
            history.append({"role": "user", "content": tool_results})

    async def shutdown(self):
        await self.exit_stack.aclose()


# Per-user state
user_mcp_apps: dict[str, EmailMCPApp] = {}
_mcp_lock = asyncio.Lock()
chat_sessions: dict[str, list] = {}


async def get_user_mcp(user_email: str) -> EmailMCPApp:
    """Get (or lazily create) the MCP app for a user."""
    if user_email in user_mcp_apps and user_mcp_apps[user_email].ready:
        return user_mcp_apps[user_email]
    async with _mcp_lock:
        if user_email in user_mcp_apps and user_mcp_apps[user_email].ready:
            return user_mcp_apps[user_email]
        token_path = ensure_token_file(user_email)
        instance = EmailMCPApp()
        await instance.connect(gmail_token_path=token_path)
        user_mcp_apps[user_email] = instance
        return instance


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_schema()
    yield
    for instance in list(user_mcp_apps.values()):
        try:
            await instance.shutdown()
        except Exception:
            pass


app = FastAPI(title="Email MCP Assistant", lifespan=lifespan)


# ── Pydantic models ────────────────────────────────────────────────────────────

class AuthRequest(BaseModel):
    email: str
    password: str


class ChatRequest(BaseModel):
    message: str
    session_id: str = ""


class ForgotPasswordRequest(BaseModel):
    email: str


class ResetPasswordRequest(BaseModel):
    token: str
    password: str


# ── Auth routes ────────────────────────────────────────────────────────────────

@app.post("/auth/register")
def register(req: AuthRequest):
    if len(req.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    email = req.email.lower().strip()
    db_create_user(email, req.password)
    token = create_token(email)
    return {"token": token, "email": email, "gmail_connected": False}


@app.post("/auth/login")
def login(req: AuthRequest):
    email = req.email.lower().strip()
    user = db_get_user(email)
    if not user or not _verify_pw(req.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    token = create_token(email)
    return {"token": token, "email": email, "gmail_connected": bool(user.get("gmail_token"))}


# ── Gmail OAuth routes ─────────────────────────────────────────────────────────

@app.get("/auth/google/url")
def get_google_auth_url(user_email: str = Depends(get_current_user)):
    """Return Google OAuth URL for the frontend to redirect to."""
    if not CREDS_PATH.exists():
        raise HTTPException(status_code=503, detail="Google credentials not configured on this server")
    try:
        from google_auth_oauthlib.flow import Flow
        flow = Flow.from_client_secrets_file(
            str(CREDS_PATH), scopes=GMAIL_SCOPES, redirect_uri=GMAIL_REDIRECT_URI
        )
        # Embed the user's JWT in `state` so we can identify them in the callback
        state_token = create_token(user_email)
        auth_url, _ = flow.authorization_url(
            access_type="offline", prompt="consent", state=state_token
        )
        return {"auth_url": auth_url}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"OAuth setup error: {exc}")


@app.get("/auth/google/callback")
async def google_callback(
    request: Request,
    code: str = None,
    state: str = None,
    error: str = None,
):
    """Handle Google's redirect after the user authorises (or denies) access."""
    if error:
        return RedirectResponse("/?error=gmail_denied")
    if not code or not state:
        return RedirectResponse("/?error=invalid_callback")
    try:
        user_email = decode_token(state)
    except HTTPException:
        return RedirectResponse("/?error=invalid_oauth_state")
    try:
        from google_auth_oauthlib.flow import Flow
        flow = Flow.from_client_secrets_file(
            str(CREDS_PATH), scopes=GMAIL_SCOPES, redirect_uri=GMAIL_REDIRECT_URI, state=state
        )
        flow.fetch_token(code=code)
        db_save_gmail_token(user_email, flow.credentials.to_json())
        # Invalidate stale MCP session so it's recreated with the new token
        if user_email in user_mcp_apps:
            try:
                await user_mcp_apps[user_email].shutdown()
            except Exception:
                pass
            del user_mcp_apps[user_email]
        return RedirectResponse("/?gmail_connected=1")
    except Exception as exc:
        print(f"[OAuth callback error] {exc}")
        return RedirectResponse("/?error=oauth_failed")


# ── Reset-password routes ──────────────────────────────────────────────────────

@app.post("/auth/forgot-password")
def forgot_password(req: ForgotPasswordRequest):
    email = req.email.lower().strip()
    user = db_get_user(email)
    if user:
        token = secrets.token_urlsafe(32)
        expiry = datetime.utcnow() + timedelta(hours=1)
        db_set_reset_token(email, token, expiry)
        reset_link = f"{APP_BASE_URL}/reset-password/{token}"
        _send_reset_email(email, reset_link)
        print(f"[reset] Link for {email}: {reset_link}")  # fallback log
    # Always return success to prevent email enumeration
    return {"message": "If that email is registered, a reset link has been sent."}


@app.get("/reset-password/{token}", response_class=HTMLResponse)
def reset_password_page(token: str):
    user = db_get_user_by_reset_token(token)
    if not user:
        return HTMLResponse(_reset_invalid_html())
    return HTMLResponse(_reset_form_html(token))


@app.post("/auth/reset-password")
def reset_password(req: ResetPasswordRequest):
    user = db_get_user_by_reset_token(req.token)
    if not user:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")
    if len(req.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    db_update_password(user["email"], req.password)
    return {"message": "Password updated. You can now sign in."}


def _reset_form_html(token: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>Reset Password — Email MCP</title>
  <style>
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{font-family:'Segoe UI',sans-serif;background:#0f172a;color:#e2e8f0;height:100vh;display:flex;align-items:center;justify-content:center}}
    .box{{background:#1e293b;border:1px solid #334155;border-radius:16px;padding:2rem;width:100%;max-width:400px}}
    h1{{font-size:1.2rem;font-weight:700;text-align:center;margin-bottom:1.5rem;color:#f8fafc}}
    .field{{display:flex;flex-direction:column;gap:0.4rem;margin-bottom:1rem}}
    .field label{{font-size:0.8rem;color:#94a3b8;font-weight:500}}
    .field input{{background:#0f172a;border:1px solid #334155;color:#e2e8f0;padding:0.75rem 0.9rem;border-radius:8px;font-size:0.95rem;outline:none;width:100%}}
    .field input:focus{{border-color:#3b82f6}}
    .btn{{width:100%;background:#3b82f6;color:#fff;border:none;padding:0.8rem;border-radius:8px;font-size:1rem;font-weight:600;cursor:pointer;margin-top:0.25rem}}
    .btn:hover{{background:#2563eb}}
    .err{{display:none;background:#450a0a;border:1px solid #f87171;color:#fca5a5;font-size:0.85rem;padding:0.6rem 0.8rem;border-radius:6px;margin-top:0.75rem}}
    .ok{{display:none;background:#052e16;border:1px solid #4ade80;color:#86efac;font-size:0.85rem;padding:0.6rem 0.8rem;border-radius:6px;margin-top:0.75rem}}
  </style>
</head>
<body>
  <div class="box">
    <h1>Reset Your Password</h1>
    <div class="field">
      <label>New password (min 8 characters)</label>
      <input id="pw" type="password" placeholder="New password" autocomplete="new-password"/>
    </div>
    <div class="field">
      <label>Confirm new password</label>
      <input id="pw2" type="password" placeholder="Confirm password" autocomplete="new-password"/>
    </div>
    <button class="btn" type="button" id="submitBtn">Set New Password</button>
    <div class="err" id="errMsg"></div>
    <div class="ok" id="okMsg"></div>
  </div>
  <script>
    document.getElementById('submitBtn').addEventListener('click', function() {{
      var pw = document.getElementById('pw').value;
      var pw2 = document.getElementById('pw2').value;
      var errEl = document.getElementById('errMsg');
      var okEl = document.getElementById('okMsg');
      errEl.style.display = 'none'; okEl.style.display = 'none';
      if (pw.length < 8) {{ errEl.textContent = 'Password must be at least 8 characters.'; errEl.style.display = 'block'; return; }}
      if (pw !== pw2) {{ errEl.textContent = 'Passwords do not match.'; errEl.style.display = 'block'; return; }}
      document.getElementById('submitBtn').disabled = true;
      fetch('/auth/reset-password', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{token: '{token}', password: pw}})
      }})
      .then(function(r) {{ return r.json().then(function(d) {{ return {{ok: r.ok, data: d}}; }}); }})
      .then(function(r) {{
        if (r.ok) {{
          okEl.textContent = 'Password updated! Redirecting to sign in...';
          okEl.style.display = 'block';
          setTimeout(function() {{ window.location.href = '/'; }}, 2000);
        }} else {{
          errEl.textContent = r.data.detail || 'Something went wrong.';
          errEl.style.display = 'block';
          document.getElementById('submitBtn').disabled = false;
        }}
      }})
      .catch(function() {{
        errEl.textContent = 'Could not connect. Please try again.';
        errEl.style.display = 'block';
        document.getElementById('submitBtn').disabled = false;
      }});
    }});
  </script>
</body>
</html>"""


def _reset_invalid_html() -> str:
    return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/><title>Link Expired</title>
  <style>body{font-family:'Segoe UI',sans-serif;background:#0f172a;color:#e2e8f0;height:100vh;display:flex;align-items:center;justify-content:center}
  .box{background:#1e293b;border:1px solid #334155;border-radius:16px;padding:2rem;max-width:400px;text-align:center}
  h1{font-size:1.2rem;color:#f87171;margin-bottom:1rem} p{color:#94a3b8;margin-bottom:1.5rem}
  a{color:#3b82f6;text-decoration:none;font-weight:600}</style>
</head>
<body>
  <div class="box">
    <h1>Link Expired or Invalid</h1>
    <p>This password reset link has expired or already been used. Links are valid for 1 hour.</p>
    <a href="/">Back to Sign In</a>
  </div>
</body>
</html>"""


# ── App routes ─────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "active_sessions": len(user_mcp_apps)}


@app.post("/chat")
async def chat(req: ChatRequest, user_email: str = Depends(get_current_user)):
    mcp = await get_user_mcp(user_email)
    if not mcp.ready:
        raise HTTPException(status_code=503, detail="MCP servers not ready")
    session_key = f"{user_email}:{req.session_id or str(uuid.uuid4())}"
    history = chat_sessions.get(session_key, [])
    reply, history = await mcp.chat(user_email, req.message, history)
    chat_sessions[session_key] = history
    sid = session_key.split(":", 1)[1]
    return {"reply": reply, "session_id": sid}


@app.delete("/session/{session_id}")
def clear_session(session_id: str, user_email: str = Depends(get_current_user)):
    chat_sessions.pop(f"{user_email}:{session_id}", None)
    return {"cleared": session_id}


# ── Frontend ───────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(content="""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>Email MCP Assistant</title>
  <style>
    *{box-sizing:border-box;margin:0;padding:0}
    body{font-family:'Segoe UI',sans-serif;background:#0f172a;color:#e2e8f0;height:100vh;display:flex;flex-direction:column}

    /* ── Auth screen ── */
    #authScreen{flex:1;display:flex;align-items:center;justify-content:center;padding:1rem}
    .authBox{background:#1e293b;border:1px solid #334155;border-radius:16px;padding:2rem;width:100%;max-width:420px}
    .authBox h1{font-size:1.3rem;font-weight:700;color:#f8fafc;text-align:center;margin-bottom:1.5rem}
    .tabs{display:flex;background:#0f172a;border-radius:8px;padding:4px;margin-bottom:1.5rem;gap:4px}
    .tab{flex:1;padding:0.55rem;border:none;border-radius:6px;font-size:0.9rem;font-weight:600;cursor:pointer;transition:background 0.15s,color 0.15s;background:transparent;color:#64748b}
    .tab.active{background:#3b82f6;color:#fff}
    .tab:hover:not(.active){color:#e2e8f0}
    .field{display:flex;flex-direction:column;gap:0.4rem;margin-bottom:1rem}
    .field label{font-size:0.8rem;color:#94a3b8;font-weight:500}
    .field input{background:#0f172a;border:1px solid #334155;color:#e2e8f0;padding:0.75rem 0.9rem;border-radius:8px;font-size:0.95rem;outline:none;width:100%}
    .field input:focus{border-color:#3b82f6}
    .btnPrimary{width:100%;background:#3b82f6;color:#fff;border:none;padding:0.8rem;border-radius:8px;font-size:1rem;font-weight:600;cursor:pointer;margin-top:0.25rem}
    .btnPrimary:hover{background:#2563eb}
    .btnPrimary:disabled{background:#475569;cursor:not-allowed}
    .forgotLink{display:block;text-align:right;font-size:0.8rem;color:#64748b;cursor:pointer;margin-top:0.5rem;background:none;border:none;width:100%}
    .forgotLink:hover{color:#3b82f6}
    .errMsg{display:none;background:#450a0a;border:1px solid #f87171;color:#fca5a5;font-size:0.85rem;padding:0.6rem 0.8rem;border-radius:6px;margin-top:0.75rem}
    .okMsg{display:none;background:#052e16;border:1px solid #4ade80;color:#86efac;font-size:0.85rem;padding:0.6rem 0.8rem;border-radius:6px;margin-top:0.75rem}

    /* ── Connect Gmail screen ── */
    #gmailScreen{flex:1;display:none;align-items:center;justify-content:center;padding:1rem}
    .gmailBox{background:#1e293b;border:1px solid #334155;border-radius:16px;padding:2.5rem;width:100%;max-width:440px;text-align:center}
    .gmailBox h2{font-size:1.3rem;font-weight:700;color:#f8fafc;margin-bottom:0.75rem}
    .gmailBox p{color:#94a3b8;font-size:0.9rem;line-height:1.6;margin-bottom:1.75rem}
    .gmailBox .gmailBtn{display:inline-flex;align-items:center;gap:0.6rem;background:#fff;color:#1e293b;border:none;padding:0.8rem 1.5rem;border-radius:8px;font-size:1rem;font-weight:600;cursor:pointer}
    .gmailBox .gmailBtn:hover{background:#f1f5f9}
    .gmailBox .skipLink{display:block;margin-top:1rem;font-size:0.8rem;color:#475569;cursor:pointer;background:none;border:none}
    .gmailBox .skipLink:hover{color:#94a3b8}
    .gmailErr{display:none;background:#450a0a;border:1px solid #f87171;color:#fca5a5;font-size:0.85rem;padding:0.6rem 0.8rem;border-radius:6px;margin-top:1rem}

    /* ── Chat screen ── */
    #chatScreen{flex:1;display:none;flex-direction:column}
    header{background:#1e293b;padding:0.9rem 1.5rem;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #334155}
    .headerLeft{display:flex;align-items:center;gap:0.75rem}
    header h1{font-size:1.1rem;font-weight:600;color:#f8fafc}
    header .badge{font-size:0.72rem;background:#22c55e;color:#fff;padding:2px 8px;border-radius:999px}
    .userInfo{display:flex;align-items:center;gap:0.75rem;font-size:0.85rem;color:#94a3b8}
    .userInfo span{color:#e2e8f0;font-weight:500}
    .btnLogout{background:transparent;border:1px solid #334155;color:#94a3b8;padding:5px 12px;border-radius:6px;cursor:pointer;font-size:0.8rem}
    .btnLogout:hover{border-color:#f87171;color:#f87171}
    #chat{flex:1;overflow-y:auto;padding:1.5rem;display:flex;flex-direction:column;gap:1rem}
    .msg{max-width:75%;padding:0.75rem 1rem;border-radius:12px;line-height:1.6;font-size:0.95rem;white-space:pre-wrap;word-wrap:break-word}
    .user{align-self:flex-end;background:#3b82f6;color:#fff;border-bottom-right-radius:4px}
    .assistant{align-self:flex-start;background:#1e293b;border:1px solid #334155;border-bottom-left-radius:4px}
    .typing{color:#94a3b8;font-style:italic;font-size:0.85rem;align-self:flex-start;padding:0.5rem}
    footer{background:#1e293b;padding:1rem 1.5rem;border-top:1px solid #334155}
    .chips{display:flex;flex-wrap:wrap;gap:0.5rem;margin-bottom:0.75rem}
    .chip{background:#0f172a;border:1px solid #334155;color:#94a3b8;font-size:0.78rem;padding:4px 10px;border-radius:999px;cursor:pointer}
    .chip:hover{border-color:#3b82f6;color:#3b82f6}
    .inputRow{display:flex;gap:0.75rem}
    #msgInput{flex:1;background:#0f172a;border:1px solid #334155;color:#e2e8f0;padding:0.75rem 1rem;border-radius:8px;font-size:0.95rem;outline:none}
    #msgInput:focus{border-color:#3b82f6}
    #sendBtn{background:#3b82f6;color:#fff;border:none;padding:0.75rem 1.25rem;border-radius:8px;cursor:pointer;font-weight:600}
    #sendBtn:hover{background:#2563eb}
    #sendBtn:disabled{background:#475569;cursor:not-allowed}

    /* ── Forgot password overlay ── */
    #forgotOverlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,0.6);align-items:center;justify-content:center;z-index:100}
    #forgotOverlay.show{display:flex}
    .forgotBox{background:#1e293b;border:1px solid #334155;border-radius:16px;padding:2rem;width:100%;max-width:400px}
    .forgotBox h2{font-size:1.1rem;font-weight:700;color:#f8fafc;margin-bottom:1.25rem}
    .forgotBox .closeBtn{float:right;background:none;border:none;color:#64748b;font-size:1.2rem;cursor:pointer;margin-top:-0.25rem}
    .forgotBox .closeBtn:hover{color:#e2e8f0}
  </style>
</head>
<body>

<!-- ── Auth Screen ── -->
<div id="authScreen">
  <div class="authBox">
    <h1>Email MCP Assistant</h1>

    <div class="tabs">
      <button type="button" class="tab active" id="tabSignIn">Sign In</button>
      <button type="button" class="tab" id="tabRegister">Create Account</button>
    </div>

    <div class="field">
      <label>Email address</label>
      <input id="authEmail" type="email" placeholder="you@example.com" autocomplete="email"/>
    </div>
    <div class="field">
      <label id="pwLabel">Password</label>
      <input id="authPassword" type="password" placeholder="&#x2022;&#x2022;&#x2022;&#x2022;&#x2022;&#x2022;&#x2022;&#x2022;" autocomplete="current-password"/>
    </div>

    <button type="button" class="btnPrimary" id="authBtn">Sign In</button>
    <button type="button" class="forgotLink" id="forgotBtn">Forgot password?</button>
    <div class="errMsg" id="authErr"></div>
    <div class="okMsg" id="authOk"></div>
  </div>
</div>

<!-- ── Connect Gmail Screen ── -->
<div id="gmailScreen">
  <div class="gmailBox">
    <h2>Connect Your Gmail</h2>
    <p>
      To read and send emails from <strong id="gmailUserEmail"></strong>, you need to
      authorise this app to access your Gmail account via Google.
    </p>
    <button type="button" class="gmailBtn" id="connectGmailBtn">
      <svg width="20" height="20" viewBox="0 0 48 48"><path fill="#EA4335" d="M24 5C13.5 5 5 13.5 5 24s8.5 19 19 19 19-8.5 19-19S34.5 5 24 5z"/><path fill="#fff" d="M11 16.5l13 8.5 13-8.5V15L24 23.5 11 15v1.5z"/><path fill="#fff" d="M11 15v18h26V15"/><path fill="#EA4335" d="M11 15l13 8.5L37 15"/></svg>
      Connect Gmail Account
    </button>
    <button type="button" class="skipLink" id="skipGmailBtn">Skip for now (email features unavailable)</button>
    <div class="gmailErr" id="gmailErr"></div>
  </div>
</div>

<!-- ── Chat Screen ── -->
<div id="chatScreen">
  <header>
    <div class="headerLeft">
      <h1>Email MCP Assistant</h1>
      <span class="badge">Powered by Claude</span>
    </div>
    <div class="userInfo">
      Signed in as <span id="userEmailDisplay"></span>
      <button type="button" class="btnLogout" id="logoutBtn">Sign out</button>
    </div>
  </header>

  <div id="chat">
    <div class="msg assistant">Hi! I'm your personal email assistant. I can send emails, read your inbox, and log everything to your database. What would you like to do?</div>
  </div>

  <footer>
    <div class="chips">
      <span class="chip" data-msg="What is my email address?">What is my email?</span>
      <span class="chip" data-msg="List my last 5 emails">List last 5 emails</span>
      <span class="chip" data-msg="Show email stats">Email stats</span>
      <span class="chip" data-msg="Show sent emails in the log">Sent email logs</span>
    </div>
    <div class="inputRow">
      <input id="msgInput" placeholder="Ask me anything about your emails..." autocomplete="off"/>
      <button id="sendBtn" type="button">Send</button>
    </div>
  </footer>
</div>

<!-- ── Forgot Password Overlay ── -->
<div id="forgotOverlay">
  <div class="forgotBox">
    <button type="button" class="closeBtn" id="forgotClose">&#x2715;</button>
    <h2>Reset Password</h2>
    <div class="field">
      <label>Your email address</label>
      <input id="forgotEmail" type="email" placeholder="you@example.com" autocomplete="email"/>
    </div>
    <button type="button" class="btnPrimary" id="forgotSubmit">Send Reset Link</button>
    <div class="errMsg" id="forgotErr"></div>
    <div class="okMsg" id="forgotOk"></div>
  </div>
</div>

<script>
  var token = ''; var userEmail = ''; var sessionId = ''; var isRegister = false;
  try { token = localStorage.getItem('mcp_token') || ''; userEmail = localStorage.getItem('mcp_email') || ''; } catch(e) {}

  // ── URL params (after OAuth redirect) ───────────────────────────────────────
  var urlParams = new URLSearchParams(window.location.search);
  var gmailConnected = urlParams.get('gmail_connected') === '1';
  var oauthError = urlParams.get('error');
  if (gmailConnected || oauthError) {
    window.history.replaceState({}, '', '/');
  }

  // ── Helpers ─────────────────────────────────────────────────────────────────
  function showErr(id, msg) {
    var el = document.getElementById(id);
    el.textContent = msg;
    el.style.display = msg ? 'block' : 'none';
  }
  function showOk(id, msg) {
    var el = document.getElementById(id);
    el.textContent = msg;
    el.style.display = msg ? 'block' : 'none';
  }

  function setTab(reg) {
    isRegister = reg;
    document.getElementById('tabSignIn').className = 'tab' + (reg ? '' : ' active');
    document.getElementById('tabRegister').className = 'tab' + (reg ? ' active' : '');
    document.getElementById('authBtn').textContent = reg ? 'Create Account' : 'Sign In';
    document.getElementById('pwLabel').textContent = reg ? 'Password (min 8 chars)' : 'Password';
    document.getElementById('authPassword').autocomplete = reg ? 'new-password' : 'current-password';
    document.getElementById('authPassword').value = '';
    document.getElementById('forgotBtn').style.display = reg ? 'none' : 'block';
    showErr('authErr', '');
    showOk('authOk', '');
  }

  function showScreen(name) {
    document.getElementById('authScreen').style.display = name === 'auth' ? 'flex' : 'none';
    document.getElementById('gmailScreen').style.display = name === 'gmail' ? 'flex' : 'none';
    document.getElementById('chatScreen').style.display = name === 'chat' ? 'flex' : 'none';
  }

  function showChat() {
    document.getElementById('userEmailDisplay').textContent = userEmail;
    document.getElementById('gmailUserEmail').textContent = userEmail;
    showScreen('chat');
  }

  function showGmailConnect(err) {
    document.getElementById('gmailUserEmail').textContent = userEmail;
    showScreen('gmail');
    if (err) {
      showErr('gmailErr', err === 'gmail_denied' ? 'Gmail access was denied. Please try again.' :
              err === 'oauth_failed' ? 'Something went wrong with Gmail authorisation. Please try again.' :
              'Gmail connection error. Please try again.');
    }
  }

  function logout() {
    try { localStorage.removeItem('mcp_token'); localStorage.removeItem('mcp_email'); } catch(e) {}
    token = ''; userEmail = ''; sessionId = '';
    setTab(false);
    document.getElementById('authEmail').value = '';
    document.getElementById('chat').innerHTML = "<div class='msg assistant'>Hi! I'm your personal email assistant. I can send emails, read your inbox, and log everything to your database. What would you like to do?</div>";
    showScreen('auth');
  }

  function afterAuth(data) {
    token = data.token;
    userEmail = data.email;
    try { localStorage.setItem('mcp_token', token); localStorage.setItem('mcp_email', userEmail); } catch(e) {}
    if (data.gmail_connected) {
      showChat();
    } else {
      showScreen('gmail');
      document.getElementById('gmailUserEmail').textContent = userEmail;
    }
  }

  // ── Sign in / Register ───────────────────────────────────────────────────────
  function submitAuth() {
    var email = document.getElementById('authEmail').value.trim();
    var password = document.getElementById('authPassword').value;
    var btn = document.getElementById('authBtn');
    showErr('authErr', '');
    showOk('authOk', '');
    if (!email || !password) { showErr('authErr', 'Please fill in all fields.'); return; }
    if (isRegister && password.length < 8) { showErr('authErr', 'Password must be at least 8 characters.'); return; }
    btn.disabled = true;
    btn.textContent = isRegister ? 'Creating account...' : 'Signing in...';
    fetch(isRegister ? '/auth/register' : '/auth/login', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({email: email, password: password})
    })
    .then(function(res) { return res.json().then(function(d) { return {ok: res.ok, data: d}; }); })
    .then(function(r) {
      if (!r.ok) { showErr('authErr', r.data.detail || 'Something went wrong.'); return; }
      afterAuth(r.data);
    })
    .catch(function() { showErr('authErr', 'Could not connect to server. Please try again.'); })
    .finally(function() {
      btn.disabled = false;
      btn.textContent = isRegister ? 'Create Account' : 'Sign In';
    });
  }

  // ── Connect Gmail ────────────────────────────────────────────────────────────
  document.getElementById('connectGmailBtn').addEventListener('click', function() {
    var btn = document.getElementById('connectGmailBtn');
    btn.disabled = true;
    btn.textContent = 'Loading...';
    showErr('gmailErr', '');
    fetch('/auth/google/url', {headers: {'Authorization': 'Bearer ' + token}})
    .then(function(res) { return res.json().then(function(d) { return {ok: res.ok, data: d}; }); })
    .then(function(r) {
      if (!r.ok) { showErr('gmailErr', r.data.detail || 'Could not get Google auth URL.'); btn.disabled = false; btn.textContent = 'Connect Gmail Account'; return; }
      window.location.href = r.data.auth_url;
    })
    .catch(function() { showErr('gmailErr', 'Could not connect to server.'); btn.disabled = false; btn.textContent = 'Connect Gmail Account'; });
  });

  document.getElementById('skipGmailBtn').addEventListener('click', function() {
    showChat();
  });

  // ── Forgot password ──────────────────────────────────────────────────────────
  document.getElementById('forgotBtn').addEventListener('click', function() {
    document.getElementById('forgotEmail').value = document.getElementById('authEmail').value;
    showErr('forgotErr', '');
    showOk('forgotOk', '');
    document.getElementById('forgotOverlay').classList.add('show');
  });

  document.getElementById('forgotClose').addEventListener('click', function() {
    document.getElementById('forgotOverlay').classList.remove('show');
  });

  document.getElementById('forgotSubmit').addEventListener('click', function() {
    var email = document.getElementById('forgotEmail').value.trim();
    var btn = document.getElementById('forgotSubmit');
    showErr('forgotErr', '');
    showOk('forgotOk', '');
    if (!email) { showErr('forgotErr', 'Please enter your email address.'); return; }
    btn.disabled = true;
    btn.textContent = 'Sending...';
    fetch('/auth/forgot-password', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({email: email})
    })
    .then(function(res) { return res.json(); })
    .then(function(d) { showOk('forgotOk', d.message); })
    .catch(function() { showErr('forgotErr', 'Could not connect. Please try again.'); })
    .finally(function() { btn.disabled = false; btn.textContent = 'Send Reset Link'; });
  });

  // ── Chat ─────────────────────────────────────────────────────────────────────
  function fill(text) {
    document.getElementById('msgInput').value = text;
    document.getElementById('msgInput').focus();
  }

  function addMsg(text, role) {
    var chat = document.getElementById('chat');
    var div = document.createElement('div');
    div.className = 'msg ' + role;
    div.textContent = text;
    chat.appendChild(div);
    chat.scrollTop = chat.scrollHeight;
    return div;
  }

  function sendMessage() {
    var input = document.getElementById('msgInput');
    var btn = document.getElementById('sendBtn');
    var msg = input.value.trim();
    if (!msg || btn.disabled) return;
    addMsg(msg, 'user');
    input.value = '';
    btn.disabled = true;
    var chat = document.getElementById('chat');
    var typing = document.createElement('div');
    typing.className = 'typing';
    typing.textContent = 'Assistant is thinking...';
    chat.appendChild(typing);
    chat.scrollTop = chat.scrollHeight;
    fetch('/chat', {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token},
      body: JSON.stringify({message: msg, session_id: sessionId})
    })
    .then(function(res) {
      if (res.status === 401) { typing.remove(); logout(); addMsg('Session expired. Please sign in again.', 'assistant'); return null; }
      return res.json().then(function(d) { return {ok: res.ok, data: d}; });
    })
    .then(function(r) {
      if (!r) return;
      typing.remove();
      if (r.ok) { sessionId = r.data.session_id; addMsg(r.data.reply, 'assistant'); }
      else { addMsg('Error: ' + (r.data.detail || 'Something went wrong.'), 'assistant'); }
    })
    .catch(function() { typing.remove(); addMsg('Error: Could not reach the server.', 'assistant'); })
    .finally(function() { btn.disabled = false; input.focus(); });
  }

  // ── Wire up events ───────────────────────────────────────────────────────────
  document.getElementById('tabSignIn').addEventListener('click', function() { setTab(false); });
  document.getElementById('tabRegister').addEventListener('click', function() { setTab(true); });
  document.getElementById('authBtn').addEventListener('click', submitAuth);
  document.getElementById('sendBtn').addEventListener('click', sendMessage);
  document.getElementById('logoutBtn').addEventListener('click', logout);
  document.getElementById('authPassword').addEventListener('keydown', function(e) { if (e.key === 'Enter') submitAuth(); });
  document.getElementById('msgInput').addEventListener('keydown', function(e) { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); } });
  document.querySelectorAll('.chip').forEach(function(c) { c.addEventListener('click', function() { fill(c.dataset.msg); }); });

  // ── Initial state ────────────────────────────────────────────────────────────
  if (token && userEmail) {
    if (gmailConnected) {
      showChat();
    } else if (oauthError) {
      showGmailConnect(oauthError);
    } else {
      // Check Gmail status from server
      fetch('/auth/login', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({email: userEmail, password: '__token_check__'})
      }).catch(function() {});
      // Trust the localStorage token — show chat directly (Gmail check on first message)
      showChat();
    }
  }
</script>
</body>
</html>""")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
