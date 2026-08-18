import re
import requests

try:
    from .date_utils import normalize_posted_date
except ImportError:
    from date_utils import normalize_posted_date

from repository import get_all_oracle_companies

# Matches an Oracle Fusion Cloud Recruiting ("Oracle Cloud HCM") careers URL like
# https://fa-ewjt-saasfaprod1.fa.ocs.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_2/jobs
# - every tenant runs its own subdomain (like Avature, unlike Greenhouse/Lever's shared host), so
# this matches the /hcmUI/CandidateExperience/.../sites/<siteNumber>/jobs PATH shape rather than
# a fixed host, and pulls the host + siteNumber out of whatever URL is pasted in.
_JOBS_PATH_PATTERN = re.compile(
    r"https?://([^/]+)/hcmUI/CandidateExperience/[^/]+/sites/([a-zA-Z0-9_\-]+)/jobs",
    re.IGNORECASE,
)

_API_PATH = "/hcmRestApi/resources/latest/recruitingCEJobRequisitions"


def parse_oracle_url(url: str) -> dict:
    """Parses an Oracle Cloud Recruiting careers URL into {base_url, site_number} - the two
    pieces fetch_oracle_jobs() needs. Raises ValueError with a human-readable message (meant to be
    shown directly in the UI, not just logged) if the URL doesn't match Oracle Cloud Recruiting's
    known shape.
    """
    match = _JOBS_PATH_PATTERN.search((url or "").strip())
    if not match:
        raise ValueError(
            "That doesn't look like an Oracle Cloud Recruiting careers URL. Expected something "
            "like https://<tenant>.oraclecloud.com/hcmUI/CandidateExperience/en/sites/"
            "<siteNumber>/jobs - open the company's careers page and check the URL contains "
            "/hcmUI/CandidateExperience/.../sites/<siteNumber>/jobs before copying it."
        )
    host, site_number = match.group(1), match.group(2)
    return {"base_url": f"https://{host}", "site_number": site_number}


def fetch_oracle_jobs(company_name: str, search_text: str = "", max_results: int = 60) -> list[dict]:
    """Fetches open postings from one company's Oracle Fusion Cloud Recruiting site, resolving
    company_name to {base_url, site_number} via the oracle_companies table
    (get_all_oracle_companies) - same resolution pattern as every other connector.

    Unlike avature_connector, Oracle Cloud Recruiting DOES have a real public JSON REST endpoint
    (recruitingCEJobRequisitions) that career sites' own front-end JS calls directly - this
    connector calls the same endpoint rather than scraping rendered HTML, closer in spirit to the
    Greenhouse/Lever connectors than to Avature's scrape.

    CAVEAT worth flagging plainly: the field names below (Id/Title/PrimaryLocation/PostedDate/
    ShortDescriptionStr, nested under items[0].requisitionList) come from third-party
    reverse-engineering references, not from Oracle's own official published schema (Oracle's docs
    confirm the endpoint and HTTP methods exist but don't publish an example response body) - this
    has NOT been verified against a live response, since this sandbox's outbound network is
    proxy-blocked to oraclecloud.com the same way it's blocked to greenhouse.io/lever.co. Test this
    against a real configured company once you have one added; if fields come back empty or wrong,
    temporarily print(raw) right after the response.json() call below to see Oracle's actual
    response shape and adjust the field names accordingly.

    search_text is applied client-side as a case-insensitive title substring match, same as
    Greenhouse/Lever/Avature (no confirmed server-side keyword-search parameter for this finder).
    """
    company = get_all_oracle_companies()[company_name]
    base_url, site_number = company["base_url"], company["site_number"]
    url = f"{base_url}{_API_PATH}"
    limit = min(max_results, 200)

    response = requests.get(
        url,
        params={
            "onlyData": "true",
            "expand": "requisitionList.workLocation,requisitionList.secondaryLocations",
            "finder": f"findReqs;siteNumber={site_number},limit={limit},offset=0,"
                      f"sortBy=POSTING_DATES_DESC",
        },
        timeout=30,
        headers={"User-Agent": "Mozilla/5.0 (compatible; JobScoutAI/1.0)"},
    )
    response.raise_for_status()
    raw = response.json()

    items = raw.get("items") or []
    requisitions = items[0].get("requisitionList", []) if items else []

    search_lower = (search_text or "").strip().lower()
    normalized = []
    for req in requisitions:
        title = req.get("Title") or ""
        if search_lower and search_lower not in title.lower():
            continue
        req_id = req.get("Id")
        normalized.append({
            "title": title,
            "company": company_name,
            "location": req.get("PrimaryLocation"),
            "posted_date": normalize_posted_date(req.get("PostedDate"), "oracle_cloud"),
            "url": (f"{base_url}/hcmUI/CandidateExperience/en/sites/{site_number}/job/{req_id}"
                    if req_id else None),
            "description": req.get("ShortDescriptionStr") or "",
            "source": "oracle_cloud",
            # Oracle's own requisition Id - already the piece the url above is built from, so
            # this is just carrying it through explicitly as `external_id` (see database.py's
            # comment on the jobs table) rather than requiring every caller to re-parse it back
            # out of the url.
            "external_id": str(req_id) if req_id else None,
        })
        if len(normalized) >= max_results:
            break

    return normalized


if __name__ == "__main__":
    # Zero-Gemini-cost sanity check - add a company via the Streamlit "Manage companies" UI first
    # (or repository.add_oracle_company), then swap the name in below. Given the field-names
    # caveat in fetch_oracle_jobs's docstring, worth running this directly first and eyeballing
    # the printed jobs before trusting it in a real search.
    jobs = fetch_oracle_jobs("EXL")
    print(f"Fetched {len(jobs)} jobs")
    for job in jobs[:5]:
        print(f"  - {job['title']} ({job['location']}) posted {job['posted_date']}")
