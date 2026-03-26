# ── Stage: runtime ────────────────────────────────────────────────────────────
FROM python:3.11-slim

# Keeps Python from buffering stdout/stderr (important for the interactive CLI)
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# ── System deps ───────────────────────────────────────────────────────────────
# Only what psycopg2-binary needs at runtime on slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libpq5 \
    && rm -rf /var/lib/apt/lists/*

# ── Non-root user (security best practice) ────────────────────────────────────
RUN useradd --create-home --shell /bin/bash appuser

WORKDIR /app

# ── Dependencies (cached layer — only re-run when requirements change) ────────
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Application code ──────────────────────────────────────────────────────────
COPY app.py .
COPY schema.sql .
COPY setup_db.py .
COPY servers/ ./servers/
# .env.example is safe to include — it has no real secrets
COPY .env.example .

# Switch to non-root user
USER appuser

# ── Runtime notes ─────────────────────────────────────────────────────────────
# credentials.json and token.json must be mounted at runtime, e.g.:
#   docker run -it \
#     -v $(pwd)/credentials.json:/app/credentials.json:ro \
#     -v $(pwd)/token.json:/app/token.json \
#     --env-file .env \
#     email-mcp-app
#
# The app is an interactive CLI — always run with -it.

CMD ["python", "app.py"]
