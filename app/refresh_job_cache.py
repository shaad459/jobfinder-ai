"""Fetches the latest open postings for every configured company and writes them to a JSON
file - the raw material for the shared job cache (see job_cache_sync.py for how the Streamlit
app pulls this down, and .github/workflows/refresh-job-cache.yml for what runs this on a
schedule).

Deliberately does NOT call Gemini at all - this only refreshes what jobs EXIST, not how well
any of them match a resume. Match scoring is inherently per-profile and still happens locally
in Streamlit (or in the separate daily-job-alert.yml workflow), against whatever's in the cache
by the time you search. Keeping this step scoring-free is what makes it safe/cheap to run every
few hours instead of once a day: there's no rate-limited model quota being spent here, just plain
API calls to Workday/JSearch/Adzuna.

Fetches with relocation_ok=True (no location filter) and a wide max_age_days, so the cache is
broad - narrowing to "near you, still fresh enough" happens locally at read time in
job_aggregator.get_company_jobs_from_cache(), the same way it always narrowed a live fetch.
That keeps this cache useful regardless of what location you search from on a given day.

Companies come from company_sync.load_companies_config() (companies_config.json, checked into
git) rather than the local `companies` SQLite table when running here - this script mostly runs
inside GitHub Actions, which checks out a fresh copy of the repo with no access to your actual
jobfinder.db (see GITHUB_ACTIONS_SETUP.md's "one thing worth knowing" section). Falls back to
repository.get_all_companies() only if companies_config.json doesn't exist yet, so a manual local
run before you've ever added/removed a company through the synced path still works.

NEW-POSTING ALERT: before overwriting --output, this reads whatever was already there (the
previous refresh's snapshot) and diffs job URLs to find postings that are genuinely new since
last time. If any are found, it emails a lightweight, UNSCORED heads-up via
email_sender.send_new_postings_alert() - the point is speed (be first to apply), not judgment;
full Gemini scoring still only happens in the daily digest or when you search from the app. A
failure sending that email is logged, not fatal - refreshing the cache is this script's actual
job, and the alert is a bonus that shouldn't block it.

IMPORTANT CAVEAT, honestly flagged rather than silently assumed: each source's own search
(Workday's career-site search box, JSearch, Adzuna) is keyword/title-based, not "give me
everything" - there's no verified "no filter, return all openings" mode for any of these
connectors as of this writing. So this cache is only as broad as DEFAULT_QUERIES below. It's
currently set to the role families actually relevant to this project (product/program/business
analyst titles) rather than one guessed generic word - update DEFAULT_QUERIES if your own job
search broadens into other role families, or the cache will quietly miss postings outside them,
the same way a live search with an unrelated title would.
"""
import argparse
import json
import os

import company_sync
from database import init_db
from job_aggregator import fetch_company_jobs
from repository import get_all_companies

DEFAULT_QUERIES = ["product owner", "product manager", "business analyst"]


def _load_companies() -> dict:
    companies = company_sync.load_companies_config()
    if companies:
        return companies
    # Fallback only - the local DB is authoritative here just for a manual run before
    # companies_config.json has ever been pushed (see company_sync.py's docstring).
    init_db()
    return get_all_companies()


def refresh_all_jobs(queries: list[str] = None, max_age_days: int = 14) -> list[dict]:
    queries = queries or DEFAULT_QUERIES
    companies = _load_companies()
    print(f"Refreshing cache for {len(companies)} configured companies, "
          f"{len(queries)} query term(s) each: {', '.join(companies)}")

    all_jobs = []
    for company in companies:
        for query in queries:
            try:
                jobs = fetch_company_jobs(
                    company, query, relocation_ok=True, max_age_days=max_age_days,
                    include_aggregators_for_workday=True,
                )
                print(f"{company} / '{query}': {len(jobs)} job(s)")
                all_jobs.extend(jobs)
            except Exception as e:
                print(f"Warning: refresh failed for {company} / '{query}': {e}")

    # De-dup by URL - the same posting can turn up under more than one query term, or via both
    # a company's direct Workday feed and the JSearch/Adzuna aggregator pass
    # (include_aggregators_for_workday=True above).
    deduped = list({j["url"]: j for j in all_jobs}.values())
    print(f"{len(deduped)} unique job(s) total after de-dup.")
    return deduped


def _load_previous_urls(output_path: str) -> set:
    """Reads whatever's already at output_path (the previous run's snapshot, if any) purely to
    know which URLs were already known - used to find what's NEW this run for the email alert.
    Missing file or unreadable JSON just means "no history yet" (e.g. this workflow's very first
    run), not an error.
    """
    if not os.path.exists(output_path):
        return set()
    try:
        with open(output_path, "r", encoding="utf-8") as f:
            previous = json.load(f)
        return {j["url"] for j in previous if j.get("url")}
    except (json.JSONDecodeError, OSError, KeyError) as e:
        print(f"Warning: couldn't read previous cache at {output_path} to diff for new postings: {e}")
        return set()


def _send_new_postings_alert(new_jobs: list[dict]):
    if not new_jobs:
        return
    try:
        from email_sender import send_new_postings_alert
        send_new_postings_alert(new_jobs)
        print(f"Sent new-postings alert for {len(new_jobs)} job(s).")
    except Exception as e:
        # Alerting is a bonus on top of this script's real job (refreshing the cache) - a
        # missing/expired Gmail secret or a transient SMTP error shouldn't fail the whole run.
        print(f"Warning: couldn't send new-postings alert: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="job_cache.json",
                         help="Path to write the JSON job dump to. Also read first (if it "
                              "already exists) to detect which jobs are new since last time.")
    parser.add_argument("--query", action="append", dest="queries",
                         help="Search term to pass to each source's own search - repeatable "
                              "(--query \"product owner\" --query \"business analyst\"). "
                              "Defaults to DEFAULT_QUERIES if not given at all.")
    parser.add_argument("--max-age-days", type=int, default=14,
                         help="Widest freshness window to cache - narrower filtering happens "
                              "later, at read time, against whatever a specific search asks for.")
    parser.add_argument("--no-alert", action="store_true",
                         help="Skip the new-postings email even if new jobs are found - useful "
                              "for a manual/local run where you don't want to email yourself.")
    args = parser.parse_args()

    previous_urls = _load_previous_urls(args.output)

    jobs = refresh_all_jobs(queries=args.queries, max_age_days=args.max_age_days)

    new_jobs = [j for j in jobs if j.get("url") and j["url"] not in previous_urls]
    print(f"{len(new_jobs)} job(s) are new since the last refresh.")
    if new_jobs and not args.no_alert:
        _send_new_postings_alert(new_jobs)

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(jobs, f, indent=2)

    print(f"Wrote {len(jobs)} job(s) to {args.output}")
