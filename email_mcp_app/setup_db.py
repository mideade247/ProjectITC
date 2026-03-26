"""
Run this once to create the email_logs table in your PostgreSQL database.
Usage: python setup_db.py
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

DATABASE_URL = os.getenv("DATABASE_URL")
SCHEMA = (Path(__file__).parent / "schema.sql").read_text()


def main():
    if not DATABASE_URL:
        print("ERROR: DATABASE_URL is not set. Add it to your .env file first.")
        return

    try:
        import psycopg2
    except ImportError:
        print("ERROR: psycopg2 not installed. Run: pip install -r requirements.txt")
        return

    print(f"Connecting to database…")
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute(SCHEMA)
        conn.commit()
        cur.close()
        conn.close()
        print("✓ Schema applied — email_logs table is ready.")
    except Exception as e:
        print(f"ERROR: {e}")


if __name__ == "__main__":
    main()
