from connectors.workday_connector import WORKDAY_COMPANIES, fetch_workday_jobs
from connectors.jsearch_connector import fetch_jsearch_jobs
from connectors.adzuna_connector import fetch_adzuna_jobs


def fetch_all_jobs(query: str, location: str = "", country: str = "in", max_results_per_source: int = 60) -> list[dict]:
    all_jobs = []

    for company in WORKDAY_COMPANIES:
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


if __name__ == "__main__":
    jobs = fetch_all_jobs("product owner", location="Pune, India")
    print(f"\nTotal combined jobs: {len(jobs)}")

    by_source = {}
    for job in jobs:
        by_source[job["source"]] = by_source.get(job["source"], 0) + 1
    print("Breakdown by source:", by_source)