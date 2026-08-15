from datetime import datetime, timezone
import requests

try:
    from .date_utils import normalize_posted_date
except ImportError:
    from date_utils import normalize_posted_date

LEVER_HOST = "api.lever.co"


def fetch_lever_jobs(site: str, company_name: str, max_results: int = 60) -> list[dict]:
    url = f"https://{LEVER_HOST}/v0/postings/{site}"
    params = {"mode": "json"}

    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()
    raw_jobs = response.json()

    normalized = []
    for job in raw_jobs:
        categories = job.get("categories") or {}
        posted_at_ms = job.get("createdAt")
        raw_posted = (
            datetime.fromtimestamp(posted_at_ms / 1000, tz=timezone.utc).isoformat()
            if posted_at_ms else None
        )

        normalized.append({
            "title": job.get("text"),
            "company": company_name,
            "location": categories.get("location"),
            "posted_date": normalize_posted_date(raw_posted, "lever"),
            "url": job.get("hostedUrl"),
            "description": job.get("descriptionPlain") or job.get("description") or "",
            "source": "lever",
        })
    return normalized[:max_results]


if __name__ == "__main__":
    jobs = fetch_lever_jobs("REPLACE_WITH_SITE_SLUG", "Company Name", max_results=10)
    print(f"Fetched {len(jobs)} jobs")
    for job in jobs[:5]:
        print(f"  - {job['title']} ({job['location']}) posted {job['posted_date']}")