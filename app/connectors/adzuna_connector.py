import os
import requests
from dotenv import load_dotenv

load_dotenv()

ADZUNA_PAGE_SIZE = 20


def fetch_adzuna_jobs(query: str, location: str = "", country: str = "in", max_results: int = 60) -> list[dict]:
    normalized = []
    page = 1

    while len(normalized) < max_results:
        url = f"https://api.adzuna.com/v1/api/jobs/{country}/search/{page}"
        params = {
            "app_id": os.environ["ADZUNA_APP_ID"],
            "app_key": os.environ["ADZUNA_APP_KEY"],
            "results_per_page": ADZUNA_PAGE_SIZE,
            "what": query,
            "where": location,
            "content-type": "application/json",
        }

        response = requests.get(url, params=params)
        response.raise_for_status()
        data = response.json()
        raw_jobs = data.get("results", [])
        total_count = data.get("count", 0)

        if not raw_jobs:
            break

        for job in raw_jobs:
            normalized.append({
                "title": job.get("title"),
                "company": (job.get("company") or {}).get("display_name"),
                "location": (job.get("location") or {}).get("display_name"),
                "posted_date": job.get("created"),
                "url": job.get("redirect_url"),
                "description": job.get("description"),
                "source": "adzuna",
            })

        page += 1
        if (page - 1) * ADZUNA_PAGE_SIZE >= total_count:
            break

    return normalized[:max_results]