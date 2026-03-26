"""
Email MCP App — Claude-powered email assistant
Connects to Gmail MCP Server + PostgreSQL MCP Server via stdio transport.
"""

import asyncio
import json
import os
import sys
from contextlib import AsyncExitStack
from pathlib import Path

from anthropic import Anthropic
from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

load_dotenv(Path(__file__).parent / ".env")

# ─── Paths ───────────────────────────────────────────────────────────────────
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


# ─── App ─────────────────────────────────────────────────────────────────────

class EmailMCPApp:
    def __init__(self):
        self.client = Anthropic()
        self.exit_stack = AsyncExitStack()
        self.tools: list[dict] = []           # Anthropic-formatted tool defs
        self._sessions: dict[str, ClientSession] = {}  # tool_name → session

    # ── Server connection ──────────────────────────────────────────────────
    async def _connect(self, label: str, command: str, args: list[str], env: dict):
        """Start an MCP server subprocess and register its tools."""
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

    async def _call_tool(self, name: str, arguments: dict) -> str:
        session = self._sessions.get(name)
        if not session:
            return json.dumps({"error": f"unknown tool: {name}"})
        result = await session.call_tool(name, arguments)
        if result.content:
            return result.content[0].text
        return ""

    # ── Agentic loop ──────────────────────────────────────────────────────
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
                # Extract final text
                text = " ".join(
                    block.text for block in response.content
                    if hasattr(block, "text")
                )
                return text.strip(), history

            # Execute all tool calls in this turn
            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                print(f"\n  [tool] {block.name}({json.dumps(block.input, separators=(',', ':'))})")
                result_text = await self._call_tool(block.name, block.input)
                print(f"  [result] {result_text[:120]}{'…' if len(result_text) > 120 else ''}")
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result_text,
                })

            history.append({"role": "user", "content": tool_results})

    # ── Main loop ─────────────────────────────────────────────────────────
    async def run(self):
        print("\n" + "═" * 56)
        print("  Email MCP Assistant — powered by Claude")
        print("═" * 56)
        print("\nConnecting to MCP servers…")

        python = sys.executable  # use same Python interpreter

        await self._connect(
            "Gmail",
            python,
            [str(GMAIL_SERVER)],
            {
                "GMAIL_CREDENTIALS_PATH": os.getenv(
                    "GMAIL_CREDENTIALS_PATH",
                    str(ROOT / "credentials.json"),
                )
            },
        )
        await self._connect(
            "PostgreSQL",
            python,
            [str(POSTGRES_SERVER)],
            {"DATABASE_URL": os.getenv("DATABASE_URL", "")},
        )

        print(f"\nReady — {len(self.tools)} tools available")
        print("\nExamples:")
        print("  • Send email to alice@example.com about the project update")
        print("  • List my last 5 emails")
        print("  • Show email stats")
        print("  • Search emails about invoice")
        print("  • Show sent emails in the log")
        print("\nType 'quit' or Ctrl-C to exit.\n")
        print("─" * 56)

        history: list = []

        while True:
            try:
                user_input = input("\nYou: ").strip()
            except (EOFError, KeyboardInterrupt):
                break

            if not user_input:
                continue
            if user_input.lower() in {"quit", "exit", "bye"}:
                break

            try:
                reply, history = await self.chat(user_input, history)
                print(f"\nAssistant: {reply}")
            except Exception as exc:
                print(f"\n[error] {exc}")

        print("\nGoodbye!")
        await self.exit_stack.aclose()


# ─── Entry point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    asyncio.run(EmailMCPApp().run())
