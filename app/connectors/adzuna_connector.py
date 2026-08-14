import os
import requests
from dotenv import load_dotenv

load_dotenv()


def fetch_adzuna_jobs(query: str, location: str = "", country: str = "in", results_per_page: int = 20) -> list[dict]:
    url = f"https://api.adzuna.com/v1/api/jobs/{country}/search/1"
    params = {
        "app_id": os.environ["ADZUNA_APP_ID"],
        "app_key": os.environ["ADZUNA_APP_KEY"],
        "results_per_page": results_per_page,
        "what": query,
        "where": location,
        "content-type": "application/json",
    }

    response = requests.get(url, params=params)
    response.raise_for_status()
    raw_jobs = response.json().get("results", [])

    normalized = []
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
    return normalized


if __name__ == "__main__":
    jobs = fetch_adzuna_jobs("product owner", location="Pune")
    print(f"{len(jobs)} jobs found")
    for job in jobs[:3]:
        print(job)