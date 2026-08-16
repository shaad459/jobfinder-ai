import json
import hashlib
from datetime import datetime, timedelta, timezone
from database import get_connection


_PROFILE_COLUMNS = (
    "id, resume_hash, label, active, resume_filename, skills, total_years_experience, domain, "
    "job_titles, summary, current_location, email, work_experience, certifications, projects, "
    "education, full_name, phone, links, created_at"
)


def _profile_row_to_dict(row) -> dict:
    """Shared row->dict mapping used by every profile read (get_profile_by_hash,
    get_profile_by_id, list_profiles) so the JSON-decoding boilerplate lives in exactly one
    place. `label`/`active`/`resume_filename` are the resume-library columns - see database.py's
    migration comment for why they exist (multiple saved resumes, one per role, instead of a
    single "current" resume).
    """
    return {
        "id": row["id"],
        "label": row["label"],
        "active": bool(row["active"]),
        "resume_filename": row["resume_filename"],
        "skills": json.loads(row["skills"] or '{"hands_on": [], "trained": []}'),
        "total_years_experience": row["total_years_experience"],
        "domain": row["domain"],
        "job_titles": json.loads(row["job_titles"] or "[]"),
        "summary": row["summary"],
        "current_location": row["current_location"],
        "email": row["email"],
        "work_experience": json.loads(row["work_experience"] or "[]"),
        "certifications": json.loads(row["certifications"] or "[]"),
        "projects": json.loads(row["projects"] or "[]"),
        "education": json.loads(row["education"] or "[]"),
        "full_name": row["full_name"],
        "phone": row["phone"],
        "links": json.loads(row["links"] or "[]"),
        "created_at": row["created_at"],
    }


def get_or_create_profile(resume_text: str, profile: dict, label: str = None,
                           resume_filename: str = None) -> int:
    """Creates a new profile row keyed by a hash of the resume text, or returns the existing
    profile_id if this exact resume content has been uploaded before (instant, no re-parsing -
    this is what makes re-uploading the same file free). `label` defaults to the resume's own
    first job_titles entry (e.g. "Business Analyst") when not given, since that's usually the
    most useful thing to show in a multi-resume library - falls back to "Resume" if the resume
    has no extracted job_titles at all. Only used on first creation; re-uploading an existing
    resume does NOT overwrite its label, so a label you've since edited via set_profile_label
    survives re-uploads of the same file.
    """
    resume_hash = hashlib.sha256(resume_text.encode("utf-8")).hexdigest()

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT id FROM profiles WHERE resume_hash = ?", (resume_hash,))
    existing = cursor.fetchone()
    if existing:
        conn.close()
        return existing["id"]

    if not label:
        titles = profile.get("job_titles") or []
        label = titles[0] if titles else "Resume"

    cursor.execute("""
        INSERT INTO profiles (
            resume_hash, label, active, resume_filename, skills, total_years_experience, domain,
            job_titles, summary, current_location, email, work_experience, certifications,
            projects, education, full_name, phone, links
        )
        VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        resume_hash,
        label,
        resume_filename,
        json.dumps(profile.get("skills", {"hands_on": [], "trained": []})),
        profile.get("total_years_experience"),
        profile.get("domain"),
        json.dumps(profile.get("job_titles", [])),
        profile.get("summary"),
        profile.get("current_location"),
        profile.get("email"),
        json.dumps(profile.get("work_experience", [])),
        json.dumps(profile.get("certifications", [])),
        json.dumps(profile.get("projects", [])),
        json.dumps(profile.get("education", [])),
        profile.get("full_name"),
        profile.get("phone"),
        json.dumps(profile.get("links", [])),
    ))
    conn.commit()
    profile_id = cursor.lastrowid
    conn.close()
    return profile_id


def get_profile_by_hash(resume_text: str) -> dict | None:
    resume_hash = hashlib.sha256(resume_text.encode("utf-8")).hexdigest()

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(f"SELECT {_PROFILE_COLUMNS} FROM profiles WHERE resume_hash = ?", (resume_hash,))
    row = cursor.fetchone()
    conn.close()

    return _profile_row_to_dict(row) if row else None


def get_profile_by_id(profile_id: int) -> dict | None:
    """Same shape as get_profile_by_hash, looked up by id instead - used everywhere the resume
    library or a multi-resume search needs to load a SPECIFIC saved profile (the hash-based
    lookup only makes sense right after parsing a freshly-uploaded file, when you have the raw
    text but not yet the id).
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(f"SELECT {_PROFILE_COLUMNS} FROM profiles WHERE id = ?", (profile_id,))
    row = cursor.fetchone()
    conn.close()

    return _profile_row_to_dict(row) if row else None


def list_profiles(active_only: bool = True) -> list[dict]:
    """Returns every saved resume in the library, newest first - the backing data for a
    multi-resume picker (e.g. "search as: [x] Business Analyst  [x] Product Owner  [ ] Project
    Manager"). active_only=True (the default) hides retired resumes without deleting their match
    history - see set_profile_active.
    """
    conn = get_connection()
    cursor = conn.cursor()
    query = f"SELECT {_PROFILE_COLUMNS} FROM profiles"
    if active_only:
        query += " WHERE active = 1"
    query += " ORDER BY created_at DESC"
    cursor.execute(query)
    rows = cursor.fetchall()
    conn.close()
    return [_profile_row_to_dict(row) for row in rows]


def set_profile_label(profile_id: int, label: str):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE profiles SET label = ? WHERE id = ?", (label, profile_id))
    conn.commit()
    conn.close()


def set_profile_active(profile_id: int, active: bool):
    """Archives (active=False) or restores (active=True) a saved resume. Deliberately never a
    hard delete: the `matches` table's rows for this profile_id (its entire scoring history - see
    get_scored_job_urls) stay intact either way, so re-activating a retired resume doesn't lose
    the "already scored" dedup state that's the whole point of scoping matches per profile.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE profiles SET active = ? WHERE id = ?", (1 if active else 0, profile_id))
    conn.commit()
    conn.close()


def save_job(job: dict):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO jobs (url, title, company, location, description, posted_date, source)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(url) DO UPDATE SET
            title=excluded.title, company=excluded.company, location=excluded.location,
            description=excluded.description, posted_date=excluded.posted_date, source=excluded.source
    """, (
        job.get("url"), job.get("title"), job.get("company"), job.get("location"),
        job.get("description"), job.get("posted_date"), job.get("source"),
    ))
    conn.commit()
    conn.close()


def save_match(profile_id: int, job: dict):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO matches (
            profile_id, job_url, match_tier, match_score, match_points, match_gaps,
            match_reasoning, dimension_breakdown
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(profile_id, job_url) DO UPDATE SET
            match_tier=excluded.match_tier, match_score=excluded.match_score,
            match_points=excluded.match_points, match_gaps=excluded.match_gaps,
            match_reasoning=excluded.match_reasoning,
            dimension_breakdown=excluded.dimension_breakdown, scored_at=CURRENT_TIMESTAMP
    """, (
        profile_id, job.get("url"), job.get("match_tier"), job.get("match_score"),
        json.dumps(job.get("match_points", [])), json.dumps(job.get("match_gaps", [])),
        job.get("match_reasoning"), json.dumps(job.get("dimension_breakdown", {})),
    ))
    conn.commit()
    conn.close()


def mark_job_opened(profile_id: int, job_url: str):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE matches SET opened_at = CURRENT_TIMESTAMP
        WHERE profile_id = ? AND job_url = ?
    """, (profile_id, job_url))
    conn.commit()
    conn.close()


def log_gemini_call(model: str, status: str):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO gemini_calls (model, status) VALUES (?, ?)", (model, status))
    conn.commit()
    conn.close()


def get_gemini_call_counts_today() -> dict:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT model, status, COUNT(*) as call_count
        FROM gemini_calls
        WHERE date(called_at) = date('now')
        GROUP BY model, status
    """)
    counts = {}
    for row in cursor.fetchall():
        counts.setdefault(row["model"], {})[row["status"]] = row["call_count"]
    conn.close()
    return counts


def get_gemini_call_log(model: str = None, limit: int = 100) -> list[dict]:
    conn = get_connection()
    cursor = conn.cursor()
    if model:
        cursor.execute("""
            SELECT model, status, called_at FROM gemini_calls
            WHERE model = ?
            ORDER BY called_at DESC
            LIMIT ?
        """, (model, limit))
    else:
        cursor.execute("""
            SELECT model, status, called_at FROM gemini_calls
            ORDER BY called_at DESC
            LIMIT ?
        """, (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def delete_stale_jobs(max_age_days: int = 7) -> int:
    """Deletes any job (and its matches, cascading) whose posted_date is older than
    max_age_days, or entirely unknown. Run this at the start of each session so nothing stale
    is ever displayed, matched, or exported. Returns the number of jobs deleted.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cutoff = (datetime.now(timezone.utc).date() - timedelta(days=max_age_days)).isoformat()

    cursor.execute("""
        DELETE FROM matches WHERE job_url IN (
            SELECT url FROM jobs WHERE posted_date IS NULL OR posted_date < ?
        )
    """, (cutoff,))
    cursor.execute("DELETE FROM jobs WHERE posted_date IS NULL OR posted_date < ?", (cutoff,))
    deleted_count = cursor.rowcount
    conn.commit()
    conn.close()
    return deleted_count


def get_scored_job_urls(profile_id: int) -> set:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT job_url FROM matches WHERE profile_id = ?", (profile_id,))
    urls = {row["job_url"] for row in cursor.fetchall()}
    conn.close()
    return urls


def get_cached_jobs_for_company(company: str) -> list[dict]:
    """Reads whatever's currently in the local `jobs` table for one company - populated either
    by a live fetch_company_jobs() call (as always) or by job_cache_sync.py merging in the
    GitHub Actions-refreshed cache. Matches company name the same loose way
    fetch_company_jobs()'s own aggregator-inclusion check does (substring, case-insensitive),
    since a job's stored `company` field is whatever text the source (Workday/JSearch/Adzuna)
    used, which doesn't always match the configured display name exactly.

    Returns jobs UNFILTERED by location/freshness - job_cache_reader.py runs these through
    job_aggregator.filter_by_location_and_freshness(), same as a live fetch would, so cached and
    live results go through identical downstream filtering.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT url, title, company, location, description, posted_date, source FROM jobs "
        "WHERE lower(company) LIKE '%' || lower(?) || '%'",
        (company,),
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_all_companies() -> dict:
    """Returns the configured Workday companies as {name: {company, datacenter, site}} - same
    shape the old hardcoded WORKDAY_COMPANIES dict had, but read fresh from the database on every
    call (not imported once), so a company added via the Streamlit UI is immediately usable in
    the same running session without a restart.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT name, company, datacenter, site FROM companies ORDER BY name")
    rows = cursor.fetchall()
    conn.close()
    return {row["name"]: {"company": row["company"], "datacenter": row["datacenter"],
                           "site": row["site"]} for row in rows}


def add_company(name: str, company: str, datacenter: str, site: str):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO companies (name, company, datacenter, site) VALUES (?, ?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET
            company=excluded.company, datacenter=excluded.datacenter, site=excluded.site
    """, (name, company, datacenter, site))
    conn.commit()
    conn.close()


def remove_company(name: str):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM companies WHERE name = ?", (name,))
    conn.commit()
    conn.close()


def get_matches(profile_id: int) -> list[dict]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT jobs.*, matches.match_tier, matches.match_score, matches.match_points,
               matches.match_gaps, matches.match_reasoning, matches.opened_at,
               matches.dimension_breakdown
        FROM matches
        JOIN jobs ON matches.job_url = jobs.url
        WHERE matches.profile_id = ?
        ORDER BY jobs.posted_date DESC, matches.match_score DESC
    """, (profile_id,))
    rows = cursor.fetchall()
    conn.close()

    results = []
    for row in rows:
        job = dict(row)
        job["match_points"] = json.loads(job["match_points"] or "[]")
        job["match_gaps"] = json.loads(job["match_gaps"] or "[]")
        job["dimension_breakdown"] = json.loads(job["dimension_breakdown"] or "{}")
        results.append(job)
    return results
