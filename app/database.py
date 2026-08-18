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

    # external_id (added as a migration below, since this table pre-dates it on an existing
    # install): the source ATS's OWN stable job/requisition identifier (Workday's "R-288316",
    # Greenhouse's/Lever's numeric/UUID job id, Oracle's requisition Id, the trailing numeric id
    # in an Avature JobDetail URL) - extracted per-connector (see each connectors/*_connector.py)
    # and used as a secondary, more reliable dedup key alongside `url` (still this table's actual
    # PRIMARY KEY, so existing FK relationships from `matches.job_url` are untouched). The gap
    # this closes: `url` alone can drift for the SAME underlying posting - a title edit changes
    # Avature's slug, a tracking query param gets added/dropped - which would otherwise look like
    # a brand-new job and get sent through prescreen/scoring again for no reason. Nullable and
    # NOT unique-constrained at the DB level (existing rows from before this column existed, and
    # any source where extraction fails, just have external_id=NULL and fall back to url-only
    # dedup exactly as before - see job_similarity.find_history_action's exact-id path).
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            url TEXT PRIMARY KEY,
            title TEXT,
            company TEXT,
            location TEXT,
            description TEXT,
            posted_date TEXT,
            source TEXT,
            external_id TEXT,
            fetched_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # pm_archetype (added as a migration below, since this table pre-dates it on an existing
    # install): matcher.py's PM ROLE CLASSIFICATION verdict for this JOB - "Technical PM",
    # "Growth PM", or "Generalist PM" - from the PM SEMANTIC TAXONOMY baked into
    # MATCH_PROMPT_TEMPLATE. Classifies the job posting itself, not the candidate, so it's stable
    # across every resume that gets matched against the same job. NULL for anything that never
    # reached Stage 2 Gemini scoring (a Stage 0 pre-filter exclusion, a prescreen reject, or a
    # job_similarity "skip_weak" inference against a different posting - see matcher.py's
    # SCREENED_OUT_PLACEHOLDER/_prefiltered_placeholder and job_similarity.apply_skip_weak) - the
    # UI/PDF should treat NULL as "not classified", not silently default to one archetype.
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
            pm_archetype TEXT,
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

    # Greenhouse and Lever configured companies - same purpose and shape as `companies` above
    # (Workday), kept as separate tables rather than folding into `companies` because each ATS
    # needs a different, single config value (a board_token / a site slug) instead of Workday's
    # three-part company/datacenter/site, and a shared polymorphic schema would mean every reader
    # of `companies` (get_all_companies, workday_connector.fetch_workday_jobs, and anything that
    # assumes its fixed 3-column shape) would need to start branching on a "type" column it never
    # had before. Two small, source-specific tables keep the existing Workday path completely
    # untouched. No default seed rows - unlike Workday's original 5 hardcoded companies, these
    # start empty and are populated entirely through the Streamlit "Manage companies" UI.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS greenhouse_companies (
            name TEXT PRIMARY KEY,
            board_token TEXT NOT NULL,
            added_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS lever_companies (
            name TEXT PRIMARY KEY,
            site TEXT NOT NULL,
            added_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Avature and Oracle Cloud Recruiting configured companies - same registry pattern as
    # Workday/Greenhouse/Lever above, but flagged here because their connectors carry real
    # caveats (see connectors/avature_connector.py and connectors/oracle_connector.py):
    # avature_connector scrapes rendered HTML rather than calling a documented API (no confirmed
    # public API exists for Avature job-seeker sites), and oracle_connector calls a real public
    # API but with field names sourced from third-party reverse-engineering rather than official
    # docs. `careers_url` (Avature) is the full careers/search page URL as pasted; `base_url` +
    # `site_number` (Oracle) are the two pieces parsed out of a Candidate Experience URL by
    # parse_oracle_url.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS avature_companies (
            name TEXT PRIMARY KEY,
            careers_url TEXT NOT NULL,
            added_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS oracle_companies (
            name TEXT PRIMARY KEY,
            base_url TEXT NOT NULL,
            site_number TEXT NOT NULL,
            added_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

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
        # Resume-library support (multiple saved resumes, e.g. one per role you target - see
        # search_service.py): `label` is the user-facing name shown in the library ("Business
        # Analyst", "Product Owner - Citi"), defaulting to the resume's own first job_titles
        # entry at creation time if the caller doesn't supply one. `active` controls whether a
        # profile shows up in the library / gets included in a "search all my resumes" call -
        # 1 (the default, via the CURRENT NOT NULL DEFAULT below) rather than a hard delete, so
        # retiring a resume never loses its match history. `resume_filename` is stored purely for
        # display (e.g. "Shaad Khan - BA.pdf") since job_titles alone isn't always a great label.
        "ALTER TABLE profiles ADD COLUMN label TEXT",
        "ALTER TABLE profiles ADD COLUMN active INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE profiles ADD COLUMN resume_filename TEXT",
        # jobs.external_id - see the comment above the `jobs` CREATE TABLE for what this is and
        # why. Added as a migration (rather than only in the CREATE TABLE body) because `jobs`
        # already existed on any install from before this column was introduced.
        "ALTER TABLE jobs ADD COLUMN external_id TEXT",
        # matches.pm_archetype - see the comment above the `matches` CREATE TABLE for what this
        # is and why. Same reasoning as external_id above: `matches` predates this column on any
        # existing install, so CREATE TABLE IF NOT EXISTS alone wouldn't add it retroactively.
        "ALTER TABLE matches ADD COLUMN pm_archetype TEXT",
    ):
        try:
            cursor.execute(statement)
        except sqlite3.OperationalError:
            pass

    # Index creation happens AFTER the migration above, not inside the CREATE TABLE block, so it
    # never runs against a table that doesn't have external_id yet - the ALTER TABLE just above
    # always executes first within this same call, guaranteeing the column exists by this point
    # even on an existing install that predates it.
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_jobs_external_id ON jobs (external_id)")

    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()
    print(f"Database initialized at {DB_PATH}")
