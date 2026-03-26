"""
Email MCP Web App — FastAPI wrapper around the Claude MCP email assistant.
Exposes a chat interface anyone can access via browser.
"""

import asyncio
import json
import os
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from anthropic import Anthropic
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from contextlib import AsyncExitStack
from pydantic import BaseModel

load_dotenv(Path(__file__).parent / ".env")

ROOT = Path(__file__).parent
GMAIL_SERVER = ROOT / "servers" / "gmail_server.py"
POSTGRES_SERVER = ROOT / "servers" / "postgres_server.py"

SYSTEM_PROMPT = """You are an intelligent email assistant with two capabilities:

1. **Gmail** — send emails, list inbox, read full email content.
2. **PostgreSQL** — log every email event and query the log.

### Rules you MUST follow every time:

SENDING EMAIL:
  Step 1 → call send_email(to, subject, body)
  Step 2 → immediately call log_email(direction="sent", from_address=<your gmail>, to_address=<to>,
            subject=<subject>, gmail_message_id=<id from step 1>, body_preview=<first 300 chars of body>)
  Step 3 → confirm to the user what you did.

READING / LISTING EMAILS:
  Step 1 → call list_emails() or get_email()
  Step 2 → for each email retrieved, call log_email(direction="received", ...) so it is recorded.
  Step 3 → present the results clearly to the user.

CHECKING LOGS / STATS:
  → call get_email_logs() or get_email_stats() or search_emails_in_db() directly.

Always be concise, friendly, and confirm every action you take."""


# ── Shared MCP app instance ───────────────────────────────────────────────────

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
            merged_env = {**os.environ, **env}
            params = StdioServerParameters(command=command, args=args, env=merged_env)
            transport = await self.exit_stack.enter_async_context(stdio_client(params))
            read_stream, write_stream = transport
            session = await self.exit_stack.enter_async_context(
                ClientSession(read_stream, write_stream)
            )
            await session.initialize()
            response = await session.list_tools()
            for tool in response.tools:
                self.tools.append({
                    "name": tool.name,
                    "description": tool.description or "",
                    "input_schema": tool.inputSchema,
                })
                self._sessions[tool.name] = session
            print(f"  [MCP] {label} connected — {len(response.tools)} tools")

        await _connect("Gmail", python, [str(GMAIL_SERVER)], {
            "GMAIL_CREDENTIALS_PATH": os.getenv(
                "GMAIL_CREDENTIALS_PATH", str(ROOT / "credentials.json")
            )
        })
        await _connect("PostgreSQL", python, [str(POSTGRES_SERVER)], {
            "DATABASE_URL": os.getenv("DATABASE_URL", "")
        })
        self.ready = True
        print(f"[App] Ready — {len(self.tools)} tools available")

    async def _call_tool(self, name: str, arguments: dict) -> str:
        session = self._sessions.get(name)
        if not session:
            return json.dumps({"error": f"unknown tool: {name}"})
        result = await session.call_tool(name, arguments)
        if result.content:
            return result.content[0].text
        return ""

    async def chat(self, user_message: str, history: list) -> tuple[str, list]:
        history.append({"role": "user", "content": user_message})
        while True:
            response = self.client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                messages=history,
                tools=self.tools,
            )
            history.append({"role": "assistant", "content": response.content})
            if response.stop_reason != "tool_use":
                text = " ".join(
                    block.text for block in response.content if hasattr(block, "text")
                )
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


# ── FastAPI lifespan ──────────────────────────────────────────────────────────

mcp_app = EmailMCPApp()
sessions: dict[str, list] = {}   # session_id → conversation history


@asynccontextmanager
async def lifespan(app: FastAPI):
    await mcp_app.connect()
    yield
    await mcp_app.shutdown()


app = FastAPI(title="Email MCP Assistant", lifespan=lifespan)


# ── Models ────────────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    session_id: str = ""


class ChatResponse(BaseModel):
    reply: str
    session_id: str


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "tools": len(mcp_app.tools), "ready": mcp_app.ready}


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    if not mcp_app.ready:
        raise HTTPException(status_code=503, detail="MCP servers not ready yet")

    session_id = req.session_id or str(uuid.uuid4())
    history = sessions.get(session_id, [])

    reply, history = await mcp_app.chat(req.message, history)
    sessions[session_id] = history

    return ChatResponse(reply=reply, session_id=session_id)


@app.delete("/session/{session_id}")
async def clear_session(session_id: str):
    sessions.pop(session_id, None)
    return {"cleared": session_id}


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse(content="""
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>Email MCP Assistant</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: 'Segoe UI', sans-serif; background: #0f172a; color: #e2e8f0; height: 100vh; display: flex; flex-direction: column; }
    header { background: #1e293b; padding: 1rem 1.5rem; display: flex; align-items: center; gap: 0.75rem; border-bottom: 1px solid #334155; }
    header h1 { font-size: 1.2rem; font-weight: 600; color: #f8fafc; }
    header span { font-size: 0.75rem; background: #22c55e; color: #fff; padding: 2px 8px; border-radius: 999px; }
    #chat { flex: 1; overflow-y: auto; padding: 1.5rem; display: flex; flex-direction: column; gap: 1rem; }
    .msg { max-width: 75%; padding: 0.75rem 1rem; border-radius: 12px; line-height: 1.6; font-size: 0.95rem; white-space: pre-wrap; }
    .user { align-self: flex-end; background: #3b82f6; color: #fff; border-bottom-right-radius: 4px; }
    .assistant { align-self: flex-start; background: #1e293b; border: 1px solid #334155; border-bottom-left-radius: 4px; }
    .typing { color: #94a3b8; font-style: italic; font-size: 0.85rem; align-self: flex-start; padding: 0.5rem; }
    footer { background: #1e293b; padding: 1rem 1.5rem; border-top: 1px solid #334155; }
    #form { display: flex; gap: 0.75rem; }
    #input { flex: 1; background: #0f172a; border: 1px solid #334155; color: #e2e8f0; padding: 0.75rem 1rem; border-radius: 8px; font-size: 0.95rem; outline: none; }
    #input:focus { border-color: #3b82f6; }
    #send { background: #3b82f6; color: #fff; border: none; padding: 0.75rem 1.25rem; border-radius: 8px; cursor: pointer; font-weight: 600; }
    #send:hover { background: #2563eb; }
    #send:disabled { background: #475569; cursor: not-allowed; }
    .examples { display: flex; flex-wrap: wrap; gap: 0.5rem; margin-bottom: 0.75rem; }
    .chip { background: #1e293b; border: 1px solid #334155; color: #94a3b8; font-size: 0.8rem; padding: 4px 10px; border-radius: 999px; cursor: pointer; }
    .chip:hover { border-color: #3b82f6; color: #3b82f6; }
  </style>
</head>
<body>
  <header>
    <h1>📧 Email MCP Assistant</h1>
    <span>Powered by Claude</span>
  </header>

  <div id="chat">
    <div class="msg assistant">Hi! I'm your AI email assistant. I can send emails, read your inbox, and log everything to your database. What would you like to do?</div>
  </div>

  <footer>
    <div class="examples">
      <span class="chip" onclick="fill('List my last 5 emails')">List my last 5 emails</span>
      <span class="chip" onclick="fill('Show email stats')">Show email stats</span>
      <span class="chip" onclick="fill('Show sent emails in the log')">Show sent email logs</span>
      <span class="chip" onclick="fill('What is my email address?')">What is my email address?</span>
    </div>
    <form id="form" onsubmit="send(event)">
      <input id="input" placeholder="Ask me anything about your emails…" autocomplete="off"/>
      <button id="send" type="submit">Send</button>
    </form>
  </footer>

  <script>
    let sessionId = '';

    function fill(text) {
      document.getElementById('input').value = text;
      document.getElementById('input').focus();
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

    async function send(e) {
      e.preventDefault();
      const input = document.getElementById('input');
      const btn = document.getElementById('send');
      const msg = input.value.trim();
      if (!msg) return;

      addMsg(msg, 'user');
      input.value = '';
      btn.disabled = true;

      const typing = document.createElement('div');
      typing.className = 'typing';
      typing.textContent = 'Assistant is thinking…';
      document.getElementById('chat').appendChild(typing);
      document.getElementById('chat').scrollTop = document.getElementById('chat').scrollHeight;

      try {
        const res = await fetch('/chat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ message: msg, session_id: sessionId })
        });
        const data = await res.json();
        sessionId = data.session_id;
        typing.remove();
        addMsg(data.reply, 'assistant');
      } catch (err) {
        typing.remove();
        addMsg('Error: Could not reach the server. Please try again.', 'assistant');
      }

      btn.disabled = false;
      input.focus();
    }

    document.getElementById('input').addEventListener('keydown', e => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        document.getElementById('form').dispatchEvent(new Event('submit'));
      }
    });
  </script>
</body>
</html>
""")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
