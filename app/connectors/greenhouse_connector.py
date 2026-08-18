import re
import requests
from bs4 import BeautifulSoup

try:
    from .date_utils import normalize_posted_date
except ImportError:
    from date_utils import normalize_posted_date

from repository import get_all_greenhouse_companies

GREENHOUSE_HOST = "boards-api.greenhouse.io"

# Matches a Greenhouse job board URL like https://boards.greenhouse.io/tesla or
# https://job-boards.greenhouse.io/tesla (Greenhouse migrated newer boards to the job-boards.*
# host; boards.greenhouse.io still works for older ones - either way the board token, the only
# piece fetch_greenhouse_jobs() needs, is the same path segment). Powers the "paste a careers
# URL" add-company flow in the Streamlit UI, mirroring workday_connector.parse_workday_url.
_GREENHOUSE_URL_PATTERN = re.compile(
    r"https?://(?:job-boards|boards)\.greenhouse\.io/([a-zA-Z0-9\-_]+)",
    re.IGNORECASE,
)


def parse_greenhouse_url(url: str) -> dict:
    """Parses a Greenhouse job board URL into {board_token} - the piece fetch_greenhouse_jobs()
    needs. Raises ValueError with a human-readable message (meant to be shown directly in the
    UI, not just logged) if the URL doesn't match Greenhouse's known shape.
    """
    match = _GREENHOUSE_URL_PATTERN.search((url or "").strip())
    if not match:
        raise ValueError(
            "That doesn't look like a Greenhouse job board URL. Expected something like "
            "https://boards.greenhouse.io/companyname or https://job-boards.greenhouse.io/"
            "companyname - open the company's careers page and check it's hosted on "
            "greenhouse.io before copying the URL."
        )
    return {"board_token": match.group(1)}


def fetch_greenhouse_jobs(company_name: str, search_text: str = "", max_results: int = 60) -> list[dict]:
    """Fetches every open posting on one company's Greenhouse job board, resolving company_name
    to a board_token via the greenhouse_companies table (get_all_greenhouse_companies) the same
    way fetch_workday_jobs() resolves a company via get_all_companies() - a KeyError here means
    company_name isn't a configured Greenhouse company.

    Unlike Workday, Greenhouse's public postings API has no server-side search - `search_text` is
    applied client-side as a simple case-insensitive title substring match instead. Left optional
    (default "" = no filtering, i.e. every open posting) since matcher.py's own Stage 0a
    title-keyword prefilter already does a more careful pass downstream anyway; this is just here
    for parity with fetch_workday_jobs()'s signature.

    content=true on the request is what includes each job's full HTML description in the SAME
    response - without it you'd need a second request per job, the way Workday's list endpoint
    forces fetch_workday_job_description() to exist as a separate call. Greenhouse and Lever don't
    need that second call at all, since the description comes back with the listing itself.
    """
    board_token = get_all_greenhouse_companies()[company_name]["board_token"]
    url = f"https://{GREENHOUSE_HOST}/v1/boards/{board_token}/jobs"

    response = requests.get(url, params={"content": "true"}, timeout=30)
    response.raise_for_status()
    raw_jobs = response.json().get("jobs", [])

    search_lower = (search_text or "").strip().lower()
    normalized = []
    for job in raw_jobs:
        title = job.get("title") or ""
        if search_lower and search_lower not in title.lower():
            continue
        normalized.append({
            "title": title,
            "company": company_name,
            "location": (job.get("location") or {}).get("name"),
            "posted_date": normalize_posted_date(job.get("updated_at"), "greenhouse"),
            "url": job.get("absolute_url"),
            "description": _html_to_text(job.get("content") or ""),
            "source": "greenhouse",
        })
        if len(normalized) >= max_results:
            break
    return normalized


def _html_to_text(html: str) -> str:
    soup = BeautifulSoup(html or "", "html.parser")
    text = soup.get_text(separator="\n")
    text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
    return text


if __name__ == "__main__":
    # Zero-Gemini-cost sanity check - add a company via the Streamlit "Manage companies" UI
    # first (or repository.add_greenhouse_company), then swap the name in below.
    jobs = fetch_greenhouse_jobs("Stripe")
    print(f"Fetched {len(jobs)} jobs")
    for job in jobs[:5]:
        print(f"  - {job['title']} ({job['location']}) posted {job['posted_date']}")
