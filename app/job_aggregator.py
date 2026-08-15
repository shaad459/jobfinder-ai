from datetime import datetime, timezone
from connectors.workday_connector import fetch_workday_jobs
from connectors.jsearch_connector import fetch_jsearch_jobs
from connectors.adzuna_connector import fetch_adzuna_jobs
from repository import get_all_companies


def fetch_all_jobs(query: str, location: str = "", country: str = "in", max_results_per_source: int = 60) -> list[dict]:
    all_jobs = []

    for company in get_all_companies():
        try:
            jobs = fetch_workday_jobs(company, search_text=query, max_results=max_results_per_source)
            all_jobs.extend(jobs)
        except Exception as e:
            print(f"Warning: Workday fetch failed for {company}: {e}")

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


def fetch_company_jobs(company: str, query: str, location: str = "", relocation_ok: bool = False,
                        country: str = "in", max_results: int = 60, max_age_days: int = 7,
                        include_aggregators_for_workday: bool = False) -> list[dict]:
    """Fetches jobs for ONE target company, filtered to your location (unless relocation_ok)
    and to postings within the last max_age_days, freshest first. A job whose posting date is
    missing or unparseable is excluded rather than assumed fresh, since we can't confirm it
    satisfies the age limit.

    For companies not in the configured companies list, the query and company name are searched together
    via JSearch/Adzuna's own relevance ranking, then narrowed client-side to just that
    employer. No client-side title-relevance filtering beyond that - a query/company mismatch
    (e.g. noisy or loosely-related results) is left for the Gemini prescreen stage to judge,
    rather than a hand-rolled keyword heuristic trying to approximate the same judgment.

    include_aggregators_for_workday: normally a configured Workday company is looked up ONLY via
    its direct Workday connector - JSearch/Adzuna aren't queried for it at all, since Workday's
    own feed is treated as authoritative. Set this to True to ALSO run the JSearch/Adzuna search
    for such a company and merge the results in - broader coverage in case Workday's own feed is
    missing something an aggregator has indexed, at the cost of 2 extra API calls per company.
    Off by default so a normal single-company search keeps its existing, cheaper behavior;
    chat_assistant.py turns it on specifically for "search all companies."
    """
    matching_key = next((key for key in get_all_companies() if key.lower() == company.lower()), None)

    jobs = []

    if matching_key:
        try:
            jobs.extend(fetch_workday_jobs(matching_key, search_text=query, max_results=max_results))
        except Exception as e:
            print(f"Warning: Workday fetch failed for {matching_key}: {e}")

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
