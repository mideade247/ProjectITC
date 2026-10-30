# Roadmap

Planned work on the Email MCP App (`email_mcp_app/`). Each item is a bounded
task with a clear outcome; estimates are rough. Done items are ticked.

## Done

- [x] Gmail and PostgreSQL MCP servers, CLI client
- [x] FastAPI web app with JWT auth, per-user Gmail OAuth and password reset
- [x] Docker image and AWS EKS + RDS deployment
- [x] Email address and request validation (`email_validation.py`)
- [x] Validation enforced in `gmail_server.send_email`
- [x] Pytest suite with mocked Gmail service
- [x] GitHub Actions CI (Ruff + Pytest)
- [x] Deployment secrets moved out of `deploy.sh` into gitignored `deploy.env`

## Phase 1 — Codebase cleanup (~5 h)

- [ ] Split `web_app.py` (~1,000 lines) into modules: auth, Gmail OAuth, chat,
      database access, HTML templates
- [ ] Move inline HTML/JS out of Python strings into template files
- [ ] Replace hard-coded AWS account, region and load-balancer hostnames in
      `deploy.sh`, `k8s/` and `web_app.py` with configuration
- [ ] Fail fast when `SECRET_KEY` is unset in production instead of generating
      a random key per process
- [ ] Only enable `OAUTHLIB_INSECURE_TRANSPORT` for local development

## Phase 2 — Validation (~5 h)

- [ ] Validate CC addresses and multiple recipients
- [ ] Subject and body length limits
- [ ] Apply validation to every HTTP endpoint via Pydantic models
- [ ] Consistent, structured validation error responses

## Phase 3 — Workflow tracking (~6 h)

- [ ] Record a status for each send request (pending → sent / failed)
- [ ] Detect duplicate submissions (idempotency key)
- [ ] Dry-run mode that validates and logs without sending
- [ ] Endpoint to view request history and status

## Phase 4 — Persistence (~6 h)

- [ ] Schema migrations instead of ad-hoc `CREATE TABLE IF NOT EXISTS`
- [ ] Store minimal metadata only; make body previews optional and off by default
- [ ] Retention setting to purge old log entries
- [ ] Repository layer so database code is testable without a live PostgreSQL

## Phase 5 — Reliability (~6 h)

- [ ] Configurable retry with backoff for transient Gmail API errors
- [ ] Record failure reasons on each request
- [ ] Structured logging (JSON) with request IDs
- [ ] Graceful handling of expired or revoked OAuth tokens

## Phase 6 — Tests (~6 h)

- [ ] Unit tests for `postgres_server` tools (mocked connection)
- [ ] API tests for `web_app` auth, reset-password and chat endpoints
- [ ] Retry and duplicate-detection tests
- [ ] Coverage report in CI

## Phase 7 — Docs and CI (~3 h)

- [ ] Architecture diagram and configuration reference
- [ ] Lint the whole package in CI, not only `email_validation.py`
- [ ] Docker build check in CI

**Total remaining: ~37 hours**
