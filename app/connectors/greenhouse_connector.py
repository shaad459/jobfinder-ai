import re
import requests

try:
    from .date_utils import normalize_posted_date
except ImportError:
    from date_utils import normalize_posted_date

GREENHOUSE_HOST = "boards-api.greenhouse.io"


def fetch_greenhouse_jobs(board_token: str, company_name: str, max_results: int = 60) -> list[dict]:
    url = f"https://{GREENHOUSE_HOST}/v1/boards/{board_token}/jobs"
    params = {"content": "true"}

    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()
    raw_jobs = response.json().get("jobs", [])

    normalized = []
    for job in raw_jobs:
        normalized.append({
            "title": job.get("title"),
            "company": company_name,
            "location": (job.get("location") or {}).get("name"),
            "posted_date": normalize_posted_date(job.get("updated_at"), "greenhouse"),
            "url": job.get("absolute_url"),
            "description": _strip_html(job.get("content") or ""),
            "source": "greenhouse",
        })
    return normalized[:max_results]


def _strip_html(html: str) -> str:
    return re.sub(r"<[^>]+>", " ", html).strip()


if __name__ == "__main__":
    jobs = fetch_greenhouse_jobs("REPLACE_WITH_BOARD_TOKEN", "Company Name", max_results=10)
    print(f"Fetched {len(jobs)} jobs")
    for job in jobs[:5]:
        print(f"  - {job['title']} ({job['location']}) posted {job['posted_date']}")