import re
from datetime import datetime, timezone
import requests

try:
    from .date_utils import normalize_posted_date
except ImportError:
    from date_utils import normalize_posted_date

from repository import get_all_lever_companies

LEVER_HOST = "api.lever.co"

# Matches a Lever job board URL like https://jobs.lever.co/tesla, optionally followed by a
# specific posting id (https://jobs.lever.co/tesla/abc123-def456) - only the site slug matters,
# the same piece fetch_lever_jobs() needs. Powers the "paste a careers URL" add-company flow in
# the Streamlit UI, mirroring workday_connector.parse_workday_url.
_LEVER_URL_PATTERN = re.compile(
    r"https?://jobs\.lever\.co/([a-zA-Z0-9\-_]+)",
    re.IGNORECASE,
)


def parse_lever_url(url: str) -> dict:
    """Parses a Lever job board URL into {site} - the piece fetch_lever_jobs() needs. Raises
    ValueError with a human-readable message (meant to be shown directly in the UI, not just
    logged) if the URL doesn't match Lever's known shape.
    """
    match = _LEVER_URL_PATTERN.search((url or "").strip())
    if not match:
        raise ValueError(
            "That doesn't look like a Lever job board URL. Expected something like "
            "https://jobs.lever.co/companyname - open the company's careers page and check it's "
            "hosted on jobs.lever.co before copying the URL."
        )
    return {"site": match.group(1)}


def fetch_lever_jobs(company_name: str, search_text: str = "", max_results: int = 60) -> list[dict]:
    """Fetches every open posting on one company's Lever board, resolving company_name to a site
    slug via the lever_companies table (get_all_lever_companies) the same way
    fetch_workday_jobs() resolves a company via get_all_companies() - a KeyError here means
    company_name isn't a configured Lever company.

    Like Greenhouse, Lever's public postings API has no server-side search - search_text is
    applied client-side as a case-insensitive title substring match, optional (default "" = no
    filtering) for the same reason noted in greenhouse_connector.fetch_greenhouse_jobs. The full
    plain-text description comes back with the listing itself (descriptionPlain), so - also like
    Greenhouse, unlike Workday - there's no separate detail-fetch call needed.
    """
    site = get_all_lever_companies()[company_name]["site"]
    url = f"https://{LEVER_HOST}/v0/postings/{site}"

    response = requests.get(url, params={"mode": "json"}, timeout=30)
    response.raise_for_status()
    raw_jobs = response.json()

    search_lower = (search_text or "").strip().lower()
    normalized = []
    for job in raw_jobs:
        title = job.get("text") or ""
        if search_lower and search_lower not in title.lower():
            continue
        categories = job.get("categories") or {}
        posted_at_ms = job.get("createdAt")
        raw_posted = (
            datetime.fromtimestamp(posted_at_ms / 1000, tz=timezone.utc).isoformat()
            if posted_at_ms else None
        )
        normalized.append({
            "title": title,
            "company": company_name,
            "location": categories.get("location"),
            "posted_date": normalize_posted_date(raw_posted, "lever"),
            "url": job.get("hostedUrl"),
            "description": job.get("descriptionPlain") or job.get("description") or "",
            "source": "lever",
        })
        if len(normalized) >= max_results:
            break
    return normalized


if __name__ == "__main__":
    # Zero-Gemini-cost sanity check - add a company via the Streamlit "Manage companies" UI
    # first (or repository.add_lever_company), then swap the name in below.
    jobs = fetch_lever_jobs("Stripe")
    print(f"Fetched {len(jobs)} jobs")
    for job in jobs[:5]:
        print(f"  - {job['title']} ({job['location']}) posted {job['posted_date']}")
