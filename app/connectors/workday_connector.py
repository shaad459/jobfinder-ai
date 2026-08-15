import re
import requests
from bs4 import BeautifulSoup

try:
    from .date_utils import normalize_posted_date
except ImportError:
    from date_utils import normalize_posted_date

from repository import get_all_companies

WORKDAY_PAGE_SIZE = 20

# Matches a Workday careers URL like https://tesla.wd1.myworkdayjobs.com/TeslaCareers, with an
# optional locale segment (https://tesla.wd1.myworkdayjobs.com/en-US/TeslaCareers) some Workday
# sites use. Powers the "paste a careers URL" add-company flow in the Streamlit UI, so a user
# doesn't need to know Workday's internal company/datacenter/site naming to add one.
_WORKDAY_URL_PATTERN = re.compile(
    r"https?://([a-zA-Z0-9\-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-zA-Z]{2}-[A-Z]{2}/)?([a-zA-Z0-9_\-]+)",
    re.IGNORECASE,
)


def parse_workday_url(url: str) -> dict:
    """Parses a Workday careers site URL into {company, datacenter, site} - the same shape
    fetch_workday_jobs() needs. Raises ValueError with a human-readable message (meant to be
    shown directly in the UI, not just logged) if the URL doesn't match Workday's known shape.
    """
    match = _WORKDAY_URL_PATTERN.search((url or "").strip())
    if not match:
        raise ValueError(
            "That doesn't look like a Workday careers URL. Expected something like "
            "https://companyname.wd1.myworkdayjobs.com/SiteName - open the company's careers "
            "page and make sure it's the myworkdayjobs.com site before copying the URL."
        )
    company, datacenter, site = match.groups()
    return {"company": company.lower(), "datacenter": datacenter.lower(), "site": site}


def __getattr__(name):
    # Backward compatibility for anything still doing `from connectors.workday_connector import
    # WORKDAY_COMPANIES` (e.g. the standalone check_freshness.py / check_jsearch_adzuna.py dev
    # scripts) - this is a static snapshot at import time, not a live view, which is fine for a
    # short-lived script but NOT what fetch_workday_jobs()/fetch_workday_job_description() use
    # internally below (they call get_all_companies() fresh each time instead, so a company added
    # via the Streamlit UI is usable immediately, without restarting the app).
    if name == "WORKDAY_COMPANIES":
        return get_all_companies()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

# A plain requests.post with only Content-Type/Accept headers looks automated to Workday's
# bot-protection (Akamai/Cloudflare-style WAFs sit in front of most myworkdayjobs.com sites).
# A real browser also visits the career site page itself before ever calling the JSON API,
# which sets session cookies the WAF checks for. We approximate both: a browser-like
# User-Agent on every request, plus a one-time GET of the career site page (via a shared
# Session, so cookies persist) before the first POST to the jobs API.
_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _new_session(base_url: str, site: str) -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": _BROWSER_USER_AGENT,
        "Accept": "application/json",
        "Content-Type": "application/json",
    })
    try:
        # Best-effort - if this fails (network hiccup, WAF still blocks it, etc.) we still
        # attempt the real API calls below rather than aborting early. Not every Workday
        # site needs this handshake, but it doesn't hurt on the ones that do.
        session.get(base_url + "/" + site, timeout=15)
    except requests.exceptions.RequestException:
        pass
    return session


def fetch_workday_jobs(company_name: str, search_text: str = "", max_results: int = 60) -> list[dict]:
    config = get_all_companies()[company_name]
    company = config["company"]
    datacenter = config["datacenter"]
    site = config["site"]

    base_url = "https://" + company + "." + datacenter + ".myworkdayjobs.com"
    api_url = base_url + "/wday/cxs/" + company + "/" + site + "/jobs"

    session = _new_session(base_url, site)

    normalized = []
    offset = 0

    while offset < max_results:
        response = session.post(
            api_url,
            json={
                "appliedFacets": {},
                "limit": WORKDAY_PAGE_SIZE,
                "offset": offset,
                "searchText": search_text,
            },
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        raw_jobs = data.get("jobPostings", [])
        total_available = data.get("total", 0)

        if not raw_jobs:
            break

        for job in raw_jobs:
            normalized.append({
                "title": job.get("title"),
                "company": company_name,
                "location": job.get("locationsText"),
                "posted_date": normalize_posted_date(job.get("postedOn"), "workday"),
                "url": base_url + "/" + site + job.get("externalPath", ""),
                "description": None,
                "source": "workday",
            })

        offset += WORKDAY_PAGE_SIZE
        if offset >= total_available:
            break

    return normalized


def _html_to_text(html: str) -> str:
    soup = BeautifulSoup(html or "", "html.parser")
    text = soup.get_text(separator="\n")
    text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
    return text


def fetch_workday_job_description(job: dict) -> str:
    config = get_all_companies().get(job["company"])
    if not config:
        return ""

    company = config["company"]
    datacenter = config["datacenter"]
    site = config["site"]

    base_url = "https://" + company + "." + datacenter + ".myworkdayjobs.com"
    external_path = job["url"].replace(base_url + "/" + site, "")
    detail_url = base_url + "/wday/cxs/" + company + "/" + site + external_path

    session = _new_session(base_url, site)
    response = session.get(detail_url, timeout=30)
    response.raise_for_status()
    data = response.json()
    html_description = data.get("jobPostingInfo", {}).get("jobDescription", "")
    return _html_to_text(html_description)
