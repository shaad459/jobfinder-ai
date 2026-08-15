import json
import hashlib
from datetime import datetime, timedelta, timezone
from database import get_connection


def get_or_create_profile(resume_text: str, profile: dict) -> int:
    resume_hash = hashlib.sha256(resume_text.encode("utf-8")).hexdigest()

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT id FROM profiles WHERE resume_hash = ?", (resume_hash,))
    existing = cursor.fetchone()
    if existing:
        conn.close()
        return existing["id"]

    cursor.execute("""
        INSERT INTO profiles (
            resume_hash, skills, total_years_experience, domain, job_titles, summary,
            current_location, email, work_experience, certifications, projects, education,
            full_name, phone, links
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        resume_hash,
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
    cursor.execute("""
        SELECT id, skills, total_years_experience, domain, job_titles, summary,
               current_location, email, work_experience, certifications, projects, education,
               full_name, phone, links
        FROM profiles WHERE resume_hash = ?
    """, (resume_hash,))
    row = cursor.fetchone()
    conn.close()

    if not row:
        return None

    return {
        "id": row["id"],
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
    }


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
