# Roadmap

## Direction: local-first and self-hosted

The Email MCP App is moving from a single hosted deployment to a **local-first,
self-hosted** tool:

- **Every user runs their own instance** and connects their own Gmail account.
- **Every user owns their data.** Mail and logs are stored in a database on the
  user's own machine (SQLite by default, PostgreSQL optional). Nothing is sent
  to a server run by the project.
- **Works offline.** The local web UI, search and history keep working without
  an internet connection; outgoing mail is queued and sent when the connection
  returns.
- **AI chat stays optional.** It uses the user's own Anthropic API key and needs
  internet. Every core feature also works without it.

Each item below is a bounded task with a clear outcome. Estimates are rough.

## Done

- [x] Gmail and PostgreSQL MCP servers, CLI client
- [x] FastAPI web app with login, Gmail OAuth and password reset
- [x] Docker image
- [x] Email address and request validation (`email_validation.py`)
- [x] Validation enforced in `gmail_server.send_email`
- [x] Pytest suite with a mocked Gmail service; CI with Ruff and Pytest
- [x] Deployment secrets moved out of `deploy.sh` into gitignored `deploy.env`

## Phase 1 — Codebase cleanup (~6 h)

- [ ] Split `web_app.py` (~1,000 lines) into modules: auth, Gmail OAuth, chat,
      storage, HTML templates
- [ ] Move inline HTML/JS out of Python strings into static files
- [ ] Central settings module (environment variables + defaults) instead of
      scattered `os.getenv` calls
- [ ] Fail fast when `SECRET_KEY` is unset instead of generating one per process
- [ ] Bind the web server to `127.0.0.1` by default

## Phase 2 — Local storage layer (~8 h)

- [ ] Storage interface used by the app and MCP servers, so database code is
      not tied to psycopg2
- [ ] SQLite backend as the default, stored in a per-user data directory
- [ ] Keep PostgreSQL as an optional backend via `DATABASE_URL`
- [ ] Schema migrations that run on both backends
- [ ] Unit tests for both backends (SQLite in-memory, PostgreSQL in CI service)

## Phase 3 — Mailbox sync (~8 h)

- [ ] Initial import of recent messages into the local database
- [ ] Incremental sync using the Gmail history API
- [ ] Configurable sync scope (labels, date range, maximum messages)
- [ ] Store headers and plain-text body; attachments downloaded on demand
- [ ] Sync status and last-synced time in the UI
- [ ] Tests with a mocked Gmail service and recorded synthetic fixtures

## Phase 4 — Offline mode (~8 h)

- [ ] Detect connectivity and switch the UI to offline mode
- [ ] Read and search the local mailbox offline (SQLite full-text search)
- [ ] Outbox queue: mail composed offline is stored and sent when back online
- [ ] Validation, duplicate detection and retry with backoff for queued mail
- [ ] Record a status and failure reason for each outgoing message
- [ ] Serve the web UI's assets locally so it loads without internet
- [ ] Tests for the outbox, retries and offline/online transitions

## Phase 5 — Single-user local web app (~5 h)

- [ ] Local mode: one Gmail account per instance; app accounts and the
      email-based password reset become optional
- [ ] Inbox, message view, compose and history pages that work without the
      AI chat
- [ ] AI chat panel disabled cleanly when no API key or no internet

## Phase 6 — Privacy and data ownership (~4 h)

- [ ] Gmail OAuth token stored in the user's data directory, not `/tmp`
- [ ] Export the local mailbox and logs (JSON / mbox)
- [ ] "Delete all my data" command and retention setting
- [ ] Logging that never writes message bodies or tokens
- [ ] `PRIVACY.md`: what is stored, where, and what is sent to Gmail and,
      only when chat is used, to the Anthropic API

## Phase 7 — Packaging and setup (~6 h)

- [ ] Installable package with a single `email-mcp` command
      (`init`, `sync`, `serve`, `chat`)
- [ ] First-run setup guiding the user through Google OAuth credentials
- [ ] Docker Compose file for self-hosting with persistent local volumes
- [ ] Move the AWS EKS deployment to `deploy/aws/` as an optional
      "self-host on your own cloud" example with placeholder settings

## Phase 8 — Tests, docs and CI (~5 h)

- [ ] API tests for the web endpoints (FastAPI test client)
- [ ] Lint the whole package in CI, not only `email_validation.py`
- [ ] Coverage report and Docker build check in CI
- [ ] Update README and setup guide for the local-first workflow
- [ ] Architecture diagram

**Total remaining: ~50 hours**
