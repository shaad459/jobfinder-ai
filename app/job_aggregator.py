from datetime import datetime, timezone
from connectors.workday_connector import fetch_workday_jobs
from connectors.greenhouse_connector import fetch_greenhouse_jobs
from connectors.lever_connector import fetch_lever_jobs
from connectors.jsearch_connector import fetch_jsearch_jobs
from connectors.adzuna_connector import fetch_adzuna_jobs
from repository import (
    get_all_companies, get_all_greenhouse_companies, get_all_lever_companies,
    get_cached_jobs_for_company,
)


def fetch_all_jobs(query: str, location: str = "", country: str = "in", max_results_per_source: int = 60) -> list[dict]:
    all_jobs = []

    for company in get_all_companies():
        try:
            jobs = fetch_workday_jobs(company, search_text=query, max_results=max_results_per_source)
            all_jobs.extend(jobs)
        except Exception as e:
            print(f"Warning: Workday fetch failed for {company}: {e}")

    for company in get_all_greenhouse_companies():
        try:
            jobs = fetch_greenhouse_jobs(company, search_text=query, max_results=max_results_per_source)
            all_jobs.extend(jobs)
        except Exception as e:
            print(f"Warning: Greenhouse fetch failed for {company}: {e}")

    for company in get_all_lever_companies():
        try:
            jobs = fetch_lever_jobs(company, search_text=query, max_results=max_results_per_source)
            all_jobs.extend(jobs)
        except Exception as e:
            print(f"Warning: Lever fetch failed for {company}: {e}")

    try:
        jsearch_query = f"{query} in {location}" if location else query
        jobs = fetch_jsearch_jobs(jsearch_query, max_results=max_results_per_source, country=country)
        all_jobs.extend(jobs)
    except Exception as e:
        print(f"Warning: JSearch fetch failed: {e}")

    try:
        jobs = fetch_adzuna_jobs(query, location=location, country=country, max_results=max_results_per_source)
        all_jobs.extend(jobs)
    except Exception as e:
        print(f"Warning: Adzuna fetch failed: {e}")

    return all_jobs


def filter_by_location_and_freshness(jobs: list[dict], company: str, location: str = "",
                                      relocation_ok: bool = False, max_age_days: int = 7) -> list[dict]:
    """Location + freshness filtering, factored out of fetch_company_jobs so job_cache_reader.py
    can apply the EXACT same rules to jobs read from the local cache instead of a live API
    response - the cache stores raw, unfiltered postings (see refresh_job_cache.py), and this is
    what narrows them down to "near you, still fresh" at read time, same as it always did for a
    live fetch. `company` is only used for the diagnostic print statements below.
    """
    fetched_count = len(jobs)

    if location and not relocation_ok:
        # Match on just the city (the first comma-separated segment of the typed location)
        # rather than requiring the whole typed string to appear verbatim in a job's location
        # field. Job boards rarely list "City, State, Country" in full - "Pune, India",
        # "Pune, Maharashtra", or just "Pune" are all common - so requiring the full three-part
        # string as a literal substring was silently rejecting real, nearby jobs.
        location_key = location.split(",")[0].strip().lower()
        jobs = [j for j in jobs
                if (location_key and location_key in (j.get("location") or "").lower())
                or "remote" in (j.get("location") or "").lower()]

        if fetched_count and not jobs:
            print(f"Note: found {fetched_count} job(s) at {company}, but none near '{location}' "
                  f"(or remote). Try a different location, or answer 'y' to the relocation "
                  f"prompt to see all of them regardless of location.")

    after_location_count = len(jobs)

    today = datetime.now(timezone.utc).date()
    dated_jobs = []
    for j in jobs:
        raw = j.get("posted_date")
        if not raw:
            continue
        try:
            posted = datetime.strptime(raw, "%Y-%m-%d").date()
        except ValueError:
            continue
        age_days = (today - posted).days
        if 0 <= age_days <= max_age_days:
            dated_jobs.append((age_days, j))

    dated_jobs.sort(key=lambda pair: pair[0])
    jobs = [j for _, j in dated_jobs]

    if after_location_count and not jobs:
        print(f"Note: found {after_location_count} job(s) at {company} near your location, but "
              f"none were posted within the last {max_age_days} days (or their posting date "
              f"couldn't be determined).")

    return jobs


def fetch_company_jobs(company: str, query: str, location: str = "", relocation_ok: bool = False,
                        country: str = "in", max_results: int = 60, max_age_days: int = 7,
                        include_aggregators_for_workday: bool = False) -> list[dict]:
    """Fetches jobs for ONE target company, filtered to your location (unless relocation_ok)
    and to postings within the last max_age_days, freshest first. A job whose posting date is
    missing or unparseable is excluded rather than assumed fresh, since we can't confirm it
    satisfies the age limit.

    For companies not in any configured connector's list, the query and company name are searched
    together via JSearch/Adzuna's own relevance ranking, then narrowed client-side to just that
    employer. No client-side title-relevance filtering beyond that - a query/company mismatch
    (e.g. noisy or loosely-related results) is left for the Gemini prescreen stage to judge,
    rather than a hand-rolled keyword heuristic trying to approximate the same judgment.

    A company can be configured under at most one connector type - Workday is checked first,
    then Greenhouse, then Lever, and whichever matches first wins (this only matters if you
    somehow add the same display name under two connector types, which nothing currently
    prevents). Greenhouse's and Lever's own postings APIs have no server-side search, so `query`
    is applied there as a client-side title substring match instead - see the docstrings on
    fetch_greenhouse_jobs/fetch_lever_jobs.

    include_aggregators_for_workday: normally a configured company (Workday, Greenhouse, or
    Lever) is looked up ONLY via its own direct connector - JSearch/Adzuna aren't queried for it
    at all, since that connector's own feed is treated as authoritative. Set this to True to ALSO
    run the JSearch/Adzuna search for such a company and merge the results in - broader coverage
    in case the direct feed is missing something an aggregator has indexed, at the cost of 2
    extra API calls per company. Off by default so a normal single-company search keeps its
    existing, cheaper behavior; chat_assistant.py turns it on specifically for "search all
    companies." Named "_for_workday" for historical reasons (it predates Greenhouse/Lever
    support) - kept as-is rather than renamed, since search_service.py and chat_assistant.py
    already pass it by this name.
    """
    workday_key = next((key for key in get_all_companies() if key.lower() == company.lower()), None)
    greenhouse_key = None if workday_key else next(
        (key for key in get_all_greenhouse_companies() if key.lower() == company.lower()), None)
    lever_key = None if (workday_key or greenhouse_key) else next(
        (key for key in get_all_lever_companies() if key.lower() == company.lower()), None)
    matching_key = workday_key or greenhouse_key or lever_key

    jobs = []

    if workday_key:
        try:
            jobs.extend(fetch_workday_jobs(workday_key, search_text=query, max_results=max_results))
        except Exception as e:
            print(f"Warning: Workday fetch failed for {workday_key}: {e}")
    elif greenhouse_key:
        try:
            jobs.extend(fetch_greenhouse_jobs(greenhouse_key, search_text=query, max_results=max_results))
        except Exception as e:
            print(f"Warning: Greenhouse fetch failed for {greenhouse_key}: {e}")
    elif lever_key:
        try:
            jobs.extend(fetch_lever_jobs(lever_key, search_text=query, max_results=max_results))
        except Exception as e:
            print(f"Warning: Lever fetch failed for {lever_key}: {e}")

    if not matching_key or include_aggregators_for_workday:
        try:
            fetched = fetch_jsearch_jobs(f"{query} at {company}", max_results=max_results, country=country)
            jobs.extend(j for j in fetched if company.lower() in (j.get("company") or "").lower())
        except Exception as e:
            print(f"Warning: JSearch fetch failed: {e}")
        try:
            fetched = fetch_adzuna_jobs(f"{query} {company}", location=location, country=country, max_results=max_results)
            jobs.extend(j for j in fetched if company.lower() in (j.get("company") or "").lower())
        except Exception as e:
            print(f"Warning: Adzuna fetch failed: {e}")

    # De-dup by URL - relevant now that a Workday company's results can be merged with
    # JSearch/Adzuna's, which sometimes index the same posting Workday's own feed already has.
    jobs = list({j["url"]: j for j in jobs}.values())

    return filter_by_location_and_freshness(jobs, company, location, relocation_ok, max_age_days)


def get_company_jobs_from_cache(company: str, location: str = "", relocation_ok: bool = False,
                                 max_age_days: int = 7) -> list[dict]:
    """Cache-first counterpart to fetch_company_jobs() - reads whatever's already in the local
    `jobs` table (kept fresh by job_cache_sync.py pulling from the GitHub Actions job-cache repo
    every ~12h, see refresh_job_cache.py) instead of hitting Workday/JSearch/Adzuna live. Applies
    the exact same location/freshness filtering a live fetch would, via the shared helper above,
    so results are identical in shape regardless of source.

    Returns an empty list (not an error) if the cache has nothing for this company - the caller
    (streamlit_app.py's _run_search) is expected to fall back to a live fetch_company_jobs() call
    when this comes back empty, e.g. for a company you just added locally that the shared cache
    doesn't know about yet.
    """
    jobs = get_cached_jobs_for_company(company)
    return filter_by_location_and_freshness(jobs, company, location, relocation_ok, max_age_days)


if __name__ == "__main__":
    jobs = fetch_all_jobs("product owner", location="Pune, India")
    print(f"\nTotal combined jobs: {len(jobs)}")

    by_source = {}
    for job in jobs:
        by_source[job["source"]] = by_source.get(job["source"], 0) + 1
    print("Breakdown by source:", by_source)

    print("\n--- Testing fetch_company_jobs ---")
    company_jobs = fetch_company_jobs("Mastercard", "product owner", location="Pune, India", relocation_ok=False)
    print(f"Mastercard jobs near Pune (within 7 days): {len(company_jobs)}")
    for job in company_jobs[:5]:
        print(f"  - {job['title']} ({job['location']}) posted {job['posted_date']}")
