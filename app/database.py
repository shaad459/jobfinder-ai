import sqlite3
import json
from pathlib import Path

DB_PATH = Path(__file__).parent / "jobfinder.db"


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            resume_hash TEXT UNIQUE,
            skills TEXT,
            total_years_experience REAL,
            domain TEXT,
            job_titles TEXT,
            summary TEXT,
            current_location TEXT,
            email TEXT,
            work_experience TEXT,
            certifications TEXT,
            projects TEXT,
            education TEXT,
            full_name TEXT,
            phone TEXT,
            links TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            url TEXT PRIMARY KEY,
            title TEXT,
            company TEXT,
            location TEXT,
            description TEXT,
            posted_date TEXT,
            source TEXT,
            fetched_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_id INTEGER NOT NULL,
            job_url TEXT NOT NULL,
            match_tier TEXT,
            match_score INTEGER,
            match_points TEXT,
            match_gaps TEXT,
            match_reasoning TEXT,
            dimension_breakdown TEXT,
            opened_at TEXT,
            scored_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (profile_id) REFERENCES profiles (id),
            FOREIGN KEY (job_url) REFERENCES jobs (url),
            UNIQUE (profile_id, job_url)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS gemini_calls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            model TEXT,
            status TEXT,
            called_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Configured Workday companies - previously a hardcoded dict in connectors/workday_connector.py
    # (WORKDAY_COMPANIES). Moved here so the Streamlit "manage companies" UI can add/remove rows
    # without a code change or restart. `name` is the display name used everywhere else in the
    # app (job_aggregator.py, chat_assistant.py, the UI); company/datacenter/site are the three
    # pieces of a Workday careers URL (https://{company}.{datacenter}.myworkdayjobs.com/{site}).
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS companies (
            name TEXT PRIMARY KEY,
            company TEXT NOT NULL,
            datacenter TEXT NOT NULL,
            site TEXT NOT NULL,
            added_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Seed with the original 5 hardcoded companies on first run only - INSERT OR IGNORE means
    # this is a no-op on every later init_db() call once the table already has rows (including
    # if the user has since removed one of these 5 - it won't silently come back).
    default_companies = [
        ("Mastercard", "mastercard", "wd1", "CorporateCareers"),
        ("Barclays", "barclays", "wd3", "External_Career_Site_Barclays"),
        ("Deutsche Bank", "db", "wd3", "DBWebsite"),
        ("Apex Group", "theapexgroup", "wd3", "apexgroupcareers"),
        ("Citi", "citi", "wd5", "2"),
    ]
    cursor.execute("SELECT COUNT(*) as c FROM companies")
    if cursor.fetchone()["c"] == 0:
        cursor.executemany(
            "INSERT OR IGNORE INTO companies (name, company, datacenter, site) VALUES (?, ?, ?, ?)",
            default_companies,
        )

    # Idempotent migrations - CREATE TABLE IF NOT EXISTS doesn't retroactively alter a table
    # that already existed before a column was added, so each of these is a no-op once the
    # column is already there (SQLite raises OperationalError on a duplicate column, which we
    # just swallow).
    for statement in (
        "ALTER TABLE profiles ADD COLUMN current_location TEXT",
        "ALTER TABLE profiles ADD COLUMN email TEXT",
        "ALTER TABLE profiles ADD COLUMN work_experience TEXT",
        "ALTER TABLE profiles ADD COLUMN certifications TEXT",
        "ALTER TABLE profiles ADD COLUMN projects TEXT",
        "ALTER TABLE profiles ADD COLUMN education TEXT",
        "ALTER TABLE profiles ADD COLUMN full_name TEXT",
        "ALTER TABLE profiles ADD COLUMN phone TEXT",
        "ALTER TABLE profiles ADD COLUMN links TEXT",
        "ALTER TABLE matches ADD COLUMN opened_at TEXT",
        "ALTER TABLE matches ADD COLUMN dimension_breakdown TEXT",
        "ALTER TABLE gemini_calls ADD COLUMN status TEXT",
    ):
        try:
            cursor.execute(statement)
        except sqlite3.OperationalError:
            pass

    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()
    print(f"Database initialized at {DB_PATH}")
