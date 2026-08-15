"""Non-interactive entry point for the daily scheduled email digest (BUILD_PLAN.md item 9).

Runs the same search -> score -> save pipeline chat_assistant.py's "search all companies" flow
uses (BUILD_PLAN item 6), minus the chat loop, then emails only the jobs that were scored for
the FIRST time during this run. A job that was already scored on a previous run is never
re-emailed, even though it'll still show up if you later export a PDF or ask the chat
assistant for matches - "new since last check" is a property of this script's run, not of the
underlying data.

Invoked once a day by .github/workflows/daily-job-alert.yml on a fresh, disposable GitHub
Actions runner - see GITHUB_ACTIONS_SETUP.md for the one-time secrets setup this expects
(your resume as a base64 secret, your API keys, your Gmail app password). Nothing here is
JobScout-AI-hosted-service specific: this is exactly the same pipeline you already run
locally via chat_assistant.py, just kicked off by a scheduler instead of you typing "search
all companies".

Known limitation carried over from the existing 7-day staleness window (delete_stale_jobs):
if a job posting stays live for more than ~7-8 days, it can drop out of the "already scored"
set and get picked up - and emailed - again on a later run. Not new behavior introduced here,
just worth knowing about if you ever see the same posting twice a week or so apart.
"""
import os
import sys

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

RESUME_PATH = os.environ.get("RESUME_PATH", "resume.pdf")


def _load_profile():
    resume_text = extract_resume_text(RESUME_PATH)
    cached = get_profile_by_hash(resume_text)
    if cached:
        profile_id = cached.pop("id")
        print(f"Using cached profile id {profile_id} (skipped Gemini extraction)")
        return cached, profile_id

    profile = extract_structured_profile(resume_text)
    profile_id = get_or_create_profile(resume_text, profile)
    print(f"Extracted fresh profile id {profile_id}")
    return profile, profile_id


def _search_all_companies(profile: dict, profile_id: int) -> set:
    """Mirrors chat_assistant.py's _do_search_all_companies, minus the chat plumbing. Returns
    the set of job URLs that were scored for the first time across every configured company.
    """
    query = (profile.get("job_titles") or ["product owner"])[0]
    location = profile.get("current_location") or ""

    companies = get_all_companies()
    print(f"Searching {len(companies)} configured companies: {', '.join(companies)}")

    newly_scored_urls = set()

    def save_batch(batch_scored):
        for job in batch_scored:
            save_job(job)
            save_match(profile_id, job)
            newly_scored_urls.add(job["url"])

    for company in companies:
        try:
            jobs = fetch_company_jobs(company, query, location=location, relocation_ok=False,
                                       include_aggregators_for_workday=True)
        except Exception as e:
            print(f"Warning: search failed for {company}: {e}")
            continue

        already_scored = get_scored_job_urls(profile_id)
        new_jobs = [j for j in jobs if j["url"] not in already_scored]
        print(f"{company}: fetched {len(jobs)} job(s), {len(new_jobs)} new")

        if new_jobs:
            score_all_jobs(profile, new_jobs, batch_size=10, on_batch_scored=save_batch)

    return newly_scored_urls


def main():
    init_db()

    deleted = delete_stale_jobs(max_age_days=7)
    if deleted:
        print(f"Cleaned up {deleted} job(s) older than 7 days (and their matches).")

    profile, profile_id = _load_profile()
    newly_scored_urls = _search_all_companies(profile, profile_id)

    all_matches = get_matches(profile_id)
    new_matches = [
        m for m in all_matches
        if m["url"] in newly_scored_urls and m.get("match_tier") in ("Strong", "Good")
    ]

    print(f"\n{len(new_matches)} new Strong/Good match(es) this run.")

    if not new_matches:
        print("Nothing new to email today.")
        return

    pdf_path = export_matches_to_pdf(new_matches, "match_report.pdf")
    send_digest_email(new_matches, attachment_path=pdf_path)
    print("Digest email sent.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # Fail loudly - a silent failure here means you just stop getting emails with no
        # indication why. A non-zero exit makes the GitHub Actions run show as failed, which
        # you'll see (and can optionally get a GitHub notification for) in the repo's Actions tab.
        print(f"Scheduled search failed: {e}")
        sys.exit(1)
