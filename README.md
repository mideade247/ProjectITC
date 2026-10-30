# ProjectITC — Email MCP App

A personal email assistant that lets me send, read and search Gmail through
natural language. It is built on the **Model Context Protocol (MCP)**: two small
MCP servers expose Gmail and a PostgreSQL email log as tools, and a client
(CLI or FastAPI web app) uses the Anthropic API to decide which tools to call.

It is becoming a **local-first, self-hosted** tool: each user runs their own
instance with their own Gmail account, their mail is stored in a database on
their own machine, and the local web UI keeps working offline. No data is sent
to a server run by this project. Ongoing work is conventional backend
engineering — storage, sync, offline support, validation, testing and
packaging — not building or training AI models.

## What it does

- **Send, list and read email** through the Gmail API (OAuth2).
- **Log every email event** (sent/received metadata) to PostgreSQL and query
  the log: history, keyword search and stats.
- **Two front ends:**
  - `app.py` — interactive command-line client.
  - `web_app.py` — FastAPI web app with JWT login, per-user Gmail OAuth,
    password reset and a browser chat UI.
- **Input validation** before anything is sent (`email_validation.py`).
- **Containerised** with Docker and deployable to AWS EKS + RDS
  (`deploy.sh`, `k8s/`).

## Built with

| Technology | Purpose |
|------------|---------|
| Python 3.11+ | Application code |
| MCP Python SDK (`mcp`) | Gmail and PostgreSQL tool servers |
| Anthropic Python SDK | Client that orchestrates the MCP tools |
| FastAPI + Uvicorn | Web app and HTTP API |
| PostgreSQL + psycopg2 | Email event log and user accounts |
| Google API client | Gmail OAuth2 and API access |
| bcrypt, python-jose | Password hashing and JWT auth |
| Docker, Kubernetes (EKS), RDS | Deployment |
| Pytest, Ruff, GitHub Actions | Tests, linting and CI |

## Repository layout

```text
ProjectITC/
├── .github/workflows/python-ci.yml   # Lint + tests on every push / PR
├── email_mcp_app/
│   ├── app.py                        # CLI client
│   ├── web_app.py                    # FastAPI web app
│   ├── email_validation.py           # Address and request validation
│   ├── servers/
│   │   ├── gmail_server.py           # MCP server: send / list / read Gmail
│   │   └── postgres_server.py        # MCP server: log / search / stats
│   ├── schema.sql, setup_db.py       # Database schema and initialisation
│   ├── tests/                        # Pytest suite (mocked Gmail, synthetic data)
│   ├── k8s/                          # Kubernetes manifests
│   ├── deploy.sh                     # AWS EKS + RDS deployment script
│   ├── Dockerfile
│   ├── .env.example, deploy.env.example
│   └── README.md                     # Detailed setup and deployment guide
├── requirements-dev.txt              # pytest, ruff
├── ROADMAP.md                        # Planned work
└── LICENSE                           # MIT
```

## Getting started

### Prerequisites

- Python 3.11 or later
- A PostgreSQL database (local or hosted)
- A Google Cloud OAuth client (`credentials.json`) with the Gmail API enabled
- An Anthropic API key
- Docker (optional)

### Install

```bash
git clone https://github.com/mideade247/ProjectITC.git
cd ProjectITC/email_mcp_app
python -m venv .venv
```

Activate the environment:

```bash
# Windows
.venv\Scripts\activate
# Linux/macOS
source .venv/bin/activate
```

```bash
pip install -r requirements.txt
```

### Configure

```bash
cp .env.example .env          # PowerShell: Copy-Item .env.example .env
```

Fill in `ANTHROPIC_API_KEY`, `DATABASE_URL` and `GMAIL_CREDENTIALS_PATH`, and
place `credentials.json` in `email_mcp_app/`. `.env`, `credentials.json`,
`token.json` and `deploy.env` are gitignored — never commit real values.

### Run

```bash
python setup_db.py            # create tables
python app.py                 # CLI client
# or
python web_app.py             # web app on http://localhost:8080
```

Docker and AWS EKS deployment are documented in
[email_mcp_app/README.md](email_mcp_app/README.md). The deploy script reads the
database password from `deploy.env` (copy `deploy.env.example`) or the
`DB_PASSWORD` environment variable.

## Testing and code quality

From the repository root:

```bash
python -m pip install -r email_mcp_app/requirements.txt -r requirements-dev.txt
python -m pytest email_mcp_app/tests -v
ruff check email_mcp_app/email_validation.py email_mcp_app/tests
```

Tests use synthetic `example.com` addresses and a mocked Gmail service; no real
email is sent. GitHub Actions runs the same lint and test commands on every
push and pull request.

## Security and privacy

- No credentials are stored in the repository; configuration comes from
  environment variables and gitignored files.
- Tests and examples use made-up data only.
- The Docker image copies application files explicitly and contains no secrets.

## Roadmap

Planned work — local SQLite storage, Gmail sync, offline mode with an outbox
queue, privacy controls, packaging and tests, roughly 50 hours — is tracked in
[ROADMAP.md](ROADMAP.md).

## License

[MIT](LICENSE)
