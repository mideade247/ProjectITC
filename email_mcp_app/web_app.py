"""
Email MCP Web App — FastAPI with login/register auth + Claude MCP email assistant.
"""

import asyncio
import json
import os
import secrets
import sys
import uuid
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path

import bcrypt
import psycopg2
import psycopg2.extras
from anthropic import Anthropic
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Header
from fastapi.responses import HTMLResponse
from jose import JWTError, jwt
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pydantic import BaseModel

load_dotenv(Path(__file__).parent / ".env")

ROOT = Path(__file__).parent
GMAIL_SERVER = ROOT / "servers" / "gmail_server.py"
POSTGRES_SERVER = ROOT / "servers" / "postgres_server.py"

# ── Auth config ───────────────────────────────────────────────────────────────
SECRET_KEY = os.getenv("SECRET_KEY", secrets.token_hex(32))
ALGORITHM = "HS256"
TOKEN_EXPIRE_HOURS = 24

def _hash_pw(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

def _verify_pw(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())

# ── DB helpers ────────────────────────────────────────────────────────────────

def _db():
    return psycopg2.connect(os.environ["DATABASE_URL"])


def ensure_users_table():
    conn = _db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id            SERIAL PRIMARY KEY,
            email         VARCHAR(255) UNIQUE NOT NULL,
            password_hash VARCHAR(255) NOT NULL,
            created_at    TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """)
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


# ── JWT helpers ───────────────────────────────────────────────────────────────

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


# ── MCP app ───────────────────────────────────────────────────────────────────

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

    async def connect(self):
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

        await _connect("Gmail", python, [str(GMAIL_SERVER)], {
            "GMAIL_CREDENTIALS_PATH": os.getenv("GMAIL_CREDENTIALS_PATH", str(ROOT / "credentials.json"))
        })
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

    async def chat(self, user_email: str, message: str, history: list) -> tuple[str, list]:
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


mcp_app = EmailMCPApp()
# chat history: keyed by (user_email, session_id)
chat_sessions: dict[str, list] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_users_table()
    await mcp_app.connect()
    yield
    await mcp_app.shutdown()


app = FastAPI(title="Email MCP Assistant", lifespan=lifespan)


# ── Models ────────────────────────────────────────────────────────────────────

class AuthRequest(BaseModel):
    email: str
    password: str


class ChatRequest(BaseModel):
    message: str
    session_id: str = ""


# ── Auth routes ───────────────────────────────────────────────────────────────

@app.post("/auth/register")
def register(req: AuthRequest):
    if len(req.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    db_create_user(req.email.lower().strip(), req.password)
    token = create_token(req.email.lower().strip())
    return {"token": token, "email": req.email.lower().strip()}


@app.post("/auth/login")
def login(req: AuthRequest):
    user = db_get_user(req.email.lower().strip())
    if not user or not _verify_pw(req.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    token = create_token(req.email.lower().strip())
    return {"token": token, "email": req.email.lower().strip()}


# ── App routes ────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "tools": len(mcp_app.tools), "ready": mcp_app.ready}


@app.post("/chat")
async def chat(req: ChatRequest, user_email: str = Depends(get_current_user)):
    if not mcp_app.ready:
        raise HTTPException(status_code=503, detail="MCP servers not ready yet")
    session_key = f"{user_email}:{req.session_id or str(uuid.uuid4())}"
    history = chat_sessions.get(session_key, [])
    reply, history = await mcp_app.chat(user_email, req.message, history)
    chat_sessions[session_key] = history
    sid = session_key.split(":", 1)[1]
    return {"reply": reply, "session_id": sid}


@app.delete("/session/{session_id}")
def clear_session(session_id: str, user_email: str = Depends(get_current_user)):
    chat_sessions.pop(f"{user_email}:{session_id}", None)
    return {"cleared": session_id}


# ── Frontend ──────────────────────────────────────────────────────────────────

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
    #authScreen{flex:1;display:flex;align-items:center;justify-content:center}
    .authBox{background:#1e293b;border:1px solid #334155;border-radius:16px;padding:2.5rem;width:100%;max-width:420px}
    .authBox h1{font-size:1.4rem;margin-bottom:0.25rem;color:#f8fafc}
    .authBox p{color:#94a3b8;font-size:0.875rem;margin-bottom:1.75rem}
    .field{display:flex;flex-direction:column;gap:0.4rem;margin-bottom:1rem}
    .field label{font-size:0.8rem;color:#94a3b8;font-weight:500}
    .field input{background:#0f172a;border:1px solid #334155;color:#e2e8f0;padding:0.7rem 0.9rem;border-radius:8px;font-size:0.95rem;outline:none}
    .field input:focus{border-color:#3b82f6}
    .btnPrimary{width:100%;background:#3b82f6;color:#fff;border:none;padding:0.75rem;border-radius:8px;font-size:1rem;font-weight:600;cursor:pointer;margin-top:0.5rem}
    .btnPrimary:hover{background:#2563eb}
    .btnPrimary:disabled{background:#475569;cursor:not-allowed}
    .switchLink{text-align:center;margin-top:1.25rem;font-size:0.875rem;color:#94a3b8}
    .switchLink a{color:#3b82f6;cursor:pointer;text-decoration:none}
    .switchLink a:hover{text-decoration:underline}
    .errMsg{color:#f87171;font-size:0.82rem;margin-top:0.5rem;min-height:1.2rem}

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
  </style>
</head>
<body>

<!-- ── Auth Screen ── -->
<div id="authScreen">
  <div class="authBox">
    <h1>📧 Email MCP Assistant</h1>
    <p id="authSubtitle">Sign in to access your personal email assistant</p>

    <div class="field">
      <label>Email address</label>
      <input id="authEmail" type="email" placeholder="you@example.com" autocomplete="email"/>
    </div>
    <div class="field">
      <label>Password</label>
      <input id="authPassword" type="password" placeholder="••••••••" autocomplete="current-password"/>
    </div>
    <div class="errMsg" id="authErr"></div>
    <button class="btnPrimary" id="authBtn" onclick="submitAuth()">Sign In</button>

    <div class="switchLink">
      <span id="switchText">Don't have an account?</span>
      <a id="switchLink" onclick="toggleMode()">Create one</a>
    </div>
  </div>
</div>

<!-- ── Chat Screen ── -->
<div id="chatScreen">
  <header>
    <div class="headerLeft">
      <h1>📧 Email MCP Assistant</h1>
      <span class="badge">Powered by Claude</span>
    </div>
    <div class="userInfo">
      Signed in as <span id="userEmailDisplay"></span>
      <button class="btnLogout" onclick="logout()">Sign out</button>
    </div>
  </header>

  <div id="chat">
    <div class="msg assistant">Hi! I'm your personal email assistant. I can send emails, read your inbox, and log everything to your database. What would you like to do?</div>
  </div>

  <footer>
    <div class="chips">
      <span class="chip" onclick="fill('What is my email address?')">What is my email?</span>
      <span class="chip" onclick="fill('List my last 5 emails')">List last 5 emails</span>
      <span class="chip" onclick="fill('Show email stats')">Email stats</span>
      <span class="chip" onclick="fill('Show sent emails in the log')">Sent email logs</span>
    </div>
    <div class="inputRow">
      <input id="msgInput" placeholder="Ask me anything about your emails…" autocomplete="off"/>
      <button id="sendBtn" type="button" onclick="sendMessage()">Send</button>
    </div>
  </footer>
</div>

<script>
  // ── State ──────────────────────────────────────────────────────────────────
  let token = localStorage.getItem('mcp_token') || '';
  let userEmail = localStorage.getItem('mcp_email') || '';
  let sessionId = '';
  let isRegister = false;

  // ── Init ───────────────────────────────────────────────────────────────────
  if (token && userEmail) showChat();

  // ── Auth mode toggle ───────────────────────────────────────────────────────
  function toggleMode() {
    isRegister = !isRegister;
    document.getElementById('authSubtitle').textContent = isRegister
      ? 'Create an account to get started'
      : 'Sign in to access your personal email assistant';
    document.getElementById('authBtn').textContent = isRegister ? 'Create Account' : 'Sign In';
    document.getElementById('switchText').textContent = isRegister ? 'Already have an account?' : "Don't have an account?";
    document.getElementById('switchLink').textContent = isRegister ? 'Sign in' : 'Create one';
    document.getElementById('authPassword').autocomplete = isRegister ? 'new-password' : 'current-password';
    document.getElementById('authErr').textContent = '';
  }

  // ── Submit login / register ────────────────────────────────────────────────
  async function submitAuth() {
    const email = document.getElementById('authEmail').value.trim();
    const password = document.getElementById('authPassword').value;
    const errEl = document.getElementById('authErr');
    const btn = document.getElementById('authBtn');
    errEl.textContent = '';

    if (!email || !password) { errEl.textContent = 'Please fill in all fields.'; return; }
    if (isRegister && password.length < 8) { errEl.textContent = 'Password must be at least 8 characters.'; return; }

    btn.disabled = true;
    btn.textContent = isRegister ? 'Creating account…' : 'Signing in…';

    try {
      const endpoint = isRegister ? '/auth/register' : '/auth/login';
      const res = await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password })
      });
      const data = await res.json();
      if (!res.ok) { errEl.textContent = data.detail || 'Something went wrong.'; return; }

      token = data.token;
      userEmail = data.email;
      localStorage.setItem('mcp_token', token);
      localStorage.setItem('mcp_email', userEmail);
      showChat();
    } catch (e) {
      errEl.textContent = 'Could not connect to server. Please try again.';
    } finally {
      btn.disabled = false;
      btn.textContent = isRegister ? 'Create Account' : 'Sign In';
    }
  }

  // ── Show/hide screens ──────────────────────────────────────────────────────
  function showChat() {
    document.getElementById('authScreen').style.display = 'none';
    document.getElementById('chatScreen').style.display = 'flex';
    document.getElementById('userEmailDisplay').textContent = userEmail;
  }

  function logout() {
    localStorage.removeItem('mcp_token');
    localStorage.removeItem('mcp_email');
    token = ''; userEmail = ''; sessionId = '';
    document.getElementById('authScreen').style.display = 'flex';
    document.getElementById('chatScreen').style.display = 'none';
    document.getElementById('authEmail').value = '';
    document.getElementById('authPassword').value = '';
    document.getElementById('authErr').textContent = '';
    // Clear chat history visually
    const chat = document.getElementById('chat');
    chat.innerHTML = '<div class="msg assistant">Hi! I\'m your personal email assistant. I can send emails, read your inbox, and log everything to your database. What would you like to do?</div>';
    sessionId = '';
  }

  // ── Chat ───────────────────────────────────────────────────────────────────
  function fill(text) {
    document.getElementById('msgInput').value = text;
    document.getElementById('msgInput').focus();
  }

  function addMsg(text, role) {
    const chat = document.getElementById('chat');
    const div = document.createElement('div');
    div.className = 'msg ' + role;
    div.textContent = text;
    chat.appendChild(div);
    chat.scrollTop = chat.scrollHeight;
    return div;
  }

  async function sendMessage() {
    const input = document.getElementById('msgInput');
    const btn = document.getElementById('sendBtn');
    const msg = input.value.trim();
    if (!msg || btn.disabled) return;

    addMsg(msg, 'user');
    input.value = '';
    btn.disabled = true;

    const chat = document.getElementById('chat');
    const typing = document.createElement('div');
    typing.className = 'typing';
    typing.textContent = 'Assistant is thinking…';
    chat.appendChild(typing);
    chat.scrollTop = chat.scrollHeight;

    try {
      const res = await fetch('/chat', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': 'Bearer ' + token
        },
        body: JSON.stringify({ message: msg, session_id: sessionId })
      });

      if (res.status === 401) {
        typing.remove();
        logout();
        addMsg('Your session has expired. Please sign in again.', 'assistant');
        return;
      }

      const data = await res.json();
      typing.remove();
      if (res.ok) {
        sessionId = data.session_id;
        addMsg(data.reply, 'assistant');
      } else {
        addMsg('Error: ' + (data.detail || 'Something went wrong.'), 'assistant');
      }
    } catch (e) {
      typing.remove();
      addMsg('Error: Could not reach the server. Please try again.', 'assistant');
    }

    btn.disabled = false;
    input.focus();
  }

  document.addEventListener('DOMContentLoaded', function () {
    document.getElementById('msgInput').addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
    });
    document.getElementById('authPassword').addEventListener('keydown', function (e) {
      if (e.key === 'Enter') submitAuth();
    });
  });
</script>
</body>
</html>""")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
