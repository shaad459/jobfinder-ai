"""Non-interactive entry point for the daily scheduled email digest (BUILD_PLAN.md item 9).

Runs the same search -> score -> save pipeline chat_assistant.py's "search all companies" flow
uses (BUILD_PLAN item 6), minus the chat loop, then emails only the jobs that were scored for
the FIRST time during this run. A job that was already scored on a previous run is never
re-emailed, even though it'll still show up if you later export a PDF or ask the chat
assistant for matches - "new since last check" is a property of this script's run, not of the
underlying data.

Invoked once a day by .github/workflows/daily-job-alert.yml on a fresh, disposable GitHub
Actions runner - see GITHUB_ACTIONS_SETUP.md for the one-time secrets setup this expects
(your resume(s) as a private-repo checkout, your API keys, your Gmail app password). Nothing
here is JobScout-AI-hosted-service specific: this is exactly the same pipeline you already run
locally via chat_assistant.py / streamlit_app.py, just kicked off by a scheduler instead of you
clicking Search.

Searches EVERY resume it finds, not just one: RESUME_DIR (default "private-data", matching the
"Checkout private resume repo" step's `path:` in daily-job-alert.yml) is scanned for
resume_<id>.<ext> files - the naming resume_sync.sync_all_active_resumes writes when you click
"Sync my active resumes for scheduled email alerts" in the Streamlit resume library. A legacy
single resume.pdf/resume.docx (from the old one-resume-only sync flow) is also picked up for
backward compatibility, so a repo that hasn't been re-synced since this change still works.
Each resume gets its own profile extraction (cached by content hash, same as the interactive
app - a resume identical to one already scored in THIS workflow's db costs no extra Gemini
call) and its own independent search/score pass across every configured company, mirroring
search_service.run_search_for_profiles' per-profile semantics: a job irrelevant to one resume
doesn't get filtered out for another, and a job already scored for one resume is scored fresh
the first time a different resume searches it.

relocation_ok defaults to False (matching the Streamlit "I'm open to relocating / any location"
checkbox's own unchecked default) - set the RELOCATION_OK env var (in daily-job-alert.yml, or
your local .env) to "true" if you want the scheduled search to ignore location the same way you
might manually when relocation is on the table.

Known limitation carried over from the existing 7-day staleness window (delete_stale_jobs):
if a job posting stays live for more than ~7-8 days, it can drop out of the "already scored"
set and get picked up - and emailed - again on a later run. Not new behavior introduced here,
just worth knowing about if you ever see the same posting twice a week or so apart.
"""
import os
import sys
from pathlib import Path

from database import init_db
from email_sender import send_digest_email
from job_aggregator import fetch_company_jobs
from matcher import score_all_jobs
from pdf_export import export_matches_to_pdf
from profile_extractor import extract_structured_profile
from repository import (
    delete_stale_jobs,
    get_all_companies,
    get_matches,
    get_or_create_profile,
    get_profile_by_hash,
    get_scored_job_urls,
    save_job,
    save_match,
)
from resume_parser import extract_resume_text

# Matches the `path:` of daily-job-alert.yml's "Checkout private resume repo" step (that step
# runs with working-directory: app, so this relative path resolves the same way RESUME_PATH used
# to). Overridable via env for local testing against a different checkout.
RESUME_DIR = os.environ.get("RESUME_DIR", "private-data")

# Same default as the Streamlit "I'm open to relocating / any location" checkbox (unchecked).
RELOCATION_OK = os.environ.get("RELOCATION_OK", "false").strip().lower() in ("1", "true", "yes")


def _discover_resume_files() -> list[Path]:
    """Every resume_<id>.<ext> file (the multi-resume naming sync_all_active_resumes writes),
    plus - for backward compatibility with a private repo that hasn't been re-synced since this
    script started supporting more than one resume - a legacy resume.<ext> file if present.
    Sorted for deterministic run-to-run ordering (matters for log readability, not correctness).
    """
    base = Path(RESUME_DIR)
    if not base.is_dir():
        return []

    files = sorted(base.glob("resume_*.pdf")) + sorted(base.glob("resume_*.docx"))
    for legacy_ext in ("pdf", "docx"):
        legacy = base / f"resume.{legacy_ext}"
        if legacy.exists():
            files.append(legacy)
    return files


def _load_profile(resume_path: Path):
    resume_text = extract_resume_text(str(resume_path))
    cached = get_profile_by_hash(resume_text)
    if cached:
        profile_id = cached.pop("id")
        print(f"  Using cached profile id {profile_id} (skipped Gemini extraction)")
        return cached, profile_id

    profile = extract_structured_profile(resume_text)
    profile_id = get_or_create_profile(resume_text, profile, resume_filename=resume_path.name)
    print(f"  Extracted fresh profile id {profile_id}")
    return profile, profile_id


def _search_all_companies(profile: dict, profile_id: int) -> set:
    """Mirrors chat_assistant.py's _do_search_all_companies, minus the chat plumbing. Returns
    the set of job URLs that were scored for the first time across every configured company,
    for THIS profile.
    """
    query = (profile.get("job_titles") or ["product owner"])[0]
    location = profile.get("current_location") or ""

    companies = get_all_companies()
    print(f"  Searching {len(companies)} configured companies: {', '.join(companies)}"
          f" (relocation_ok={RELOCATION_OK})")

    newly_scored_urls = set()

    def save_batch(batch_scored):
        for job in batch_scored:
            save_job(job)
            save_match(profile_id, job)
            newly_scored_urls.add(job["url"])

    for company in companies:
        try:
            jobs = fetch_company_jobs(company, query, location=location, relocation_ok=RELOCATION_OK,
                                       include_aggregators_for_workday=True)
        except Exception as e:
            print(f"  Warning: search failed for {company}: {e}")
            continue

        already_scored = get_scored_job_urls(profile_id)
        new_jobs = [j for j in jobs if j["url"] not in already_scored]
        print(f"  {company}: fetched {len(jobs)} job(s), {len(new_jobs)} new")

        if new_jobs:
            score_all_jobs(profile, new_jobs, batch_size=10, on_batch_scored=save_batch)

    return newly_scored_urls


def main():
    init_db()

    deleted = delete_stale_jobs(max_age_days=7)
    if deleted:
        print(f"Cleaned up {deleted} job(s) older than 7 days (and their matches).")

    resume_files = _discover_resume_files()
    if not resume_files:
        print(
            f"No resume files found under {RESUME_DIR}/ (looked for resume_<id>.pdf, "
            f"resume_<id>.docx, resume.pdf, resume.docx). Nothing to search - see "
            f"GITHUB_ACTIONS_SETUP.md."
        )
        sys.exit(1)

    print(f"Found {len(resume_files)} resume(s): {', '.join(f.name for f in resume_files)}")

    all_new_matches = []
    for resume_path in resume_files:
        print(f"\n=== {resume_path.name} ===")
        try:
            profile, profile_id = _load_profile(resume_path)
        except Exception as e:
            print(f"  Warning: couldn't extract a profile from {resume_path.name}: {e}")
            continue

        newly_scored_urls = _search_all_companies(profile, profile_id)

        profile_matches = get_matches(profile_id)
        resume_label = profile.get("label") or (profile.get("job_titles") or [resume_path.stem])[0]
        new_matches = [
            {**m, "resume_label": resume_label}
            for m in profile_matches
            if m["url"] in newly_scored_urls and m.get("match_tier") in ("Strong", "Good")
        ]
        print(f"  {len(new_matches)} new Strong/Good match(es) for this resume.")
        all_new_matches.extend(new_matches)

    # A job that matches more than one resume in this run appears once per resume (each carries
    # its own score/reasoning and resume_label) rather than being deduped down to one - a
    # Business-Analyst-only match and a Product-Manager-only match on the same posting are
    # genuinely different information, same as the Streamlit multi-resume search results.
    print(f"\n{len(all_new_matches)} new Strong/Good match(es) this run, across "
          f"{len(resume_files)} resume(s).")

    if not all_new_matches:
        print("Nothing new to email today.")
        return

    pdf_path = export_matches_to_pdf(all_new_matches, "match_report.pdf")
    send_digest_email(all_new_matches, attachment_path=pdf_path)
    print("Digest email sent.")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        # Fail loudly - a silent failure here means you just stop getting emails with no
        # indication why. A non-zero exit makes the GitHub Actions run show as failed, which
        # you'll see (and can optionally get a GitHub notification for) in the repo's Actions tab.
        print(f"Scheduled search failed: {e}")
        sys.exit(1)
