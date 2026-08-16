"""Fetches the latest open postings for every configured company and writes them to a JSON
file - the raw material for the shared job cache (see job_cache_sync.py for how the Streamlit
app pulls this down, and .github/workflows/refresh-job-cache.yml for what runs this on a
schedule).

Deliberately does NOT call Gemini at all - this only refreshes what jobs EXIST, not how well
any of them match a resume. Match scoring is inherently per-profile and still happens locally
in Streamlit (or in the separate daily-job-alert.yml workflow), against whatever's in the cache
by the time you search. Keeping this step scoring-free is what makes it safe/cheap to run every
12 hours instead of once a day: there's no rate-limited model quota being spent here, just plain
API calls to Workday/JSearch/Adzuna.

Fetches with relocation_ok=True (no location filter) and a wide max_age_days, so the cache is
broad - narrowing to "near you, still fresh enough" happens locally at read time in
job_aggregator.get_company_jobs_from_cache(), the same way it always narrowed a live fetch.
That keeps this cache useful regardless of what location you search from on a given day.

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

from database import init_db
from job_aggregator import fetch_company_jobs
from repository import get_all_companies

DEFAULT_QUERIES = ["product owner", "product manager", "business analyst"]


def refresh_all_jobs(queries: list[str] = None, max_age_days: int = 14) -> list[dict]:
    queries = queries or DEFAULT_QUERIES
    init_db()
    companies = get_all_companies()
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="job_cache.json",
                         help="Path to write the JSON job dump to.")
    parser.add_argument("--query", action="append", dest="queries",
                         help="Search term to pass to each source's own search - repeatable "
                              "(--query \"product owner\" --query \"business analyst\"). "
                              "Defaults to DEFAULT_QUERIES if not given at all.")
    parser.add_argument("--max-age-days", type=int, default=14,
                         help="Widest freshness window to cache - narrower filtering happens "
                              "later, at read time, against whatever a specific search asks for.")
    args = parser.parse_args()

    jobs = refresh_all_jobs(queries=args.queries, max_age_days=args.max_age_days)

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(jobs, f, indent=2)

    print(f"Wrote {len(jobs)} job(s) to {args.output}")
