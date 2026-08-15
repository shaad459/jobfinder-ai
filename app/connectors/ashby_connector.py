import requests

try:
    from .date_utils import normalize_posted_date
except ImportError:
    from date_utils import normalize_posted_date

ASHBY_HOST = "api.ashbyhq.com"


def fetch_ashby_jobs(job_board_name: str, company_name: str, max_results: int = 60) -> list[dict]:
    """CAUTION: Ashby's exact field names here are a best guess, not verified against a live
    response. The first time you run this, if titles/locations come back empty or wrong, print
    response.json() once and check the real keys against what's used below.
    """
    url = f"https://{ASHBY_HOST}/posting-api/job-board/{job_board_name}"
    params = {"includeCompensation": "true"}

    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()
    raw_jobs = response.json().get("jobs", [])

    normalized = []
    for job in raw_jobs:
        normalized.append({
            "title": job.get("title"),
            "company": company_name,
            "location": job.get("location"),
            "posted_date": normalize_posted_date(job.get("publishedAt"), "ashby"),
            "url": job.get("jobUrl") or job.get("applyUrl"),
            "description": job.get("descriptionPlain") or job.get("descriptionHtml") or "",
            "source": "ashby",
        })
    return normalized[:max_results]


if __name__ == "__main__":
    jobs = fetch_ashby_jobs("REPLACE_WITH_BOARD_NAME", "Company Name", max_results=10)
    print(f"Fetched {len(jobs)} jobs")
    for job in jobs[:5]:
        print(f"  - {job['title']} ({job['location']}) posted {job['posted_date']}")