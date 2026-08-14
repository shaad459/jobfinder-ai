import math
import os
import requests
from dotenv import load_dotenv

load_dotenv()

JSEARCH_HOST = "jsearch.p.rapidapi.com"


def fetch_jsearch_jobs(query: str, max_results: int = 60, country: str = "in", date_posted: str = "all") -> list[dict]:
    num_pages = max(1, math.ceil(max_results / 10))  # roughly 10 results per page
    url = f"https://{JSEARCH_HOST}/search-v2"
    headers = {
        "X-RapidAPI-Key": os.environ["JSEARCH_API_KEY"],
        "X-RapidAPI-Host": JSEARCH_HOST,
    }
    params = {
        "query": query,
        "num_pages": str(num_pages),
        "country": country,
        "date_posted": date_posted,
    }

    response = requests.get(url, headers=headers, params=params)
    response.raise_for_status()
    raw_jobs = response.json().get("data", {}).get("jobs", [])

    normalized = []
    for job in raw_jobs:
        normalized.append({
            "title": job.get("job_title"),
            "company": job.get("employer_name"),
            "location": job.get("job_location") or job.get("job_city"),
            "posted_date": job.get("job_posted_at_datetime_utc") or job.get("job_posted_at"),
            "url": job.get("job_apply_link"),
            "description": job.get("job_description"),
            "source": "jsearch",
        })
    return normalized[:max_results]