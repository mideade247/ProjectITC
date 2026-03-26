-- Email MCP App — PostgreSQL Schema
-- Run once: psql $DATABASE_URL -f schema.sql

CREATE TABLE IF NOT EXISTS users (
    id            SERIAL PRIMARY KEY,
    email         VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    created_at    TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS email_logs (
    id               SERIAL PRIMARY KEY,
    direction        VARCHAR(10)  NOT NULL CHECK (direction IN ('sent', 'received')),
    from_address     VARCHAR(255) NOT NULL,
    to_address       VARCHAR(255) NOT NULL,
    subject          TEXT,
    body_preview     TEXT,
    gmail_message_id VARCHAR(255) UNIQUE,          -- prevents duplicate inserts
    status           VARCHAR(50)  DEFAULT 'sent',
    logged_at        TIMESTAMP    NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_email_direction  ON email_logs (direction);
CREATE INDEX IF NOT EXISTS idx_email_logged_at  ON email_logs (logged_at DESC);
CREATE INDEX IF NOT EXISTS idx_email_from       ON email_logs (from_address);
CREATE INDEX IF NOT EXISTS idx_email_to         ON email_logs (to_address);
