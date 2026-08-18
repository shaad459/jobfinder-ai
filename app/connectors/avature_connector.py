import re
from urllib.parse import urljoin, urlparse
from datetime import datetime, timezone
import requests
from bs4 import BeautifulSoup

try:
    from .date_utils import normalize_posted_date
except ImportError:
    from date_utils import normalize_posted_date

from repository import get_all_avature_companies

# Avature job detail links follow this shape on every customer-hosted Avature career site
# observed so far (e.g. Deloitte USI: usijobs.deloitte.com/en_US/careersUSI/JobDetail/<slug>/<id>)
# - unlike Greenhouse/Lever, Avature is white-labeled onto each company's OWN domain, so there's
# no shared host to match against the way GREENHOUSE_HOST/LEVER_HOST work. /JobDetail/ in the path
# is the one structural constant this connector leans on instead of a CSS-class-based scrape:
# Avature's page theme/markup is customized per customer and changes across redesigns, but the
# /JobDetail/ URL is part of Avature's own routing, not styling, so it's the more stable thing to
# match against.
_JOB_DETAIL_HREF_PATTERN = re.compile(r"/JobDetail/", re.IGNORECASE)

# Mirrors the "load more results" pagination query params seen on Avature's own search results
# (jobOffset=0 is page 1, 10 is page 2, etc., matching jobRecordsPerPage). Capped at _MAX_PAGES so
# a company whose pagination doesn't match this assumed contract can't loop forever - it will just
# stop early and return whatever the first page(s) gave it.
_RESULTS_PER_PAGE = 10
_MAX_PAGES = 15


def parse_avature_url(url: str) -> dict:
    """Parses an Avature careers URL into {careers_url}. Unlike parse_workday_url/
    parse_greenhouse_url/parse_lever_url, there's no shared host or path shape to validate against
    - Avature is deployed on each customer's own domain (e.g. usijobs.deloitte.com), so any
    well-formed http(s) URL is accepted as-is, INCLUDING its query string. That last part is
    deliberate: if the URL you paste already has Avature's own location/keyword filter applied
    (e.g. a state/city dropdown selection baked into the query string), keeping it means every
    fetch is pre-narrowed by Avature's own filtering - which matters a lot here, since (see
    fetch_avature_jobs's docstring) this connector can't reliably extract a job's location from
    the search-results page itself. If it isn't really an Avature site, fetch_avature_jobs simply
    comes back empty (no /JobDetail/ links found) rather than erroring at add-time.
    """
    stripped = (url or "").strip()
    parsed = urlparse(stripped)
    if not (parsed.scheme in ("http", "https") and parsed.netloc):
        raise ValueError(
            "That doesn't look like a valid URL. Paste the full careers page URL, including "
            "https:// - e.g. https://usijobs.deloitte.com/en_US/careersusi"
        )
    return {"careers_url": stripped}


def fetch_avature_jobs(company_name: str, search_text: str = "", max_results: int = 60) -> list[dict]:
    """Fetches open postings from one company's Avature-hosted careers site, resolving
    company_name to a careers_url via the avature_companies table (get_all_avature_companies) -
    same resolution pattern as every other connector's fetch_*_jobs.

    UNLIKE Workday/Greenhouse/Lever, Avature has no confirmed public JSON API for job-seeker-facing
    career sites - this scrapes the server-rendered search-results HTML instead, paging via
    jobOffset the same way the site's own "load more" does. That makes this connector meaningfully
    more fragile than the other three, and it comes with two real gaps worth knowing before you
    rely on it:

    1. LOCATION is not reliably present in the search-results markup (only the job title and its
       /JobDetail/ link are), so every job from this connector has location=None. In
       job_aggregator.filter_by_location_and_freshness, a job with no location only survives a
       location-filtered search via the "remote" fallback - it will otherwise be silently dropped
       whenever you search with relocation_ok=False. Point the configured careers_url at an
       already location-filtered Avature search (see parse_avature_url) to work around this, or
       expect Avature results mainly under "yes to relocation" / the scheduled cache refresh
       (which always runs with relocation_ok=True).
    2. POSTED_DATE is not in the search-results markup either. Rather than leaving it None (which
       would make filter_by_location_and_freshness exclude every Avature job unconditionally -
       "excluded rather than assumed fresh" is that function's explicit, correct policy for a
       missing date), this stamps every job with the date it was FETCHED, not the date it was
       actually posted. That's an honest tradeoff, not a hidden one: it means an Avature posting
       that's been live for three weeks will still look "freshly posted" every time this runs,
       which can skew freshness sort/dedup slightly - but the alternative (posted_date=None) means
       these jobs would never surface anywhere, which is worse.

    search_text is applied client-side as a case-insensitive title substring match, same as
    Greenhouse/Lever (no server-side search parameter confirmed for this connector).
    """
    careers_url = get_all_avature_companies()[company_name]["careers_url"]
    search_lower = (search_text or "").strip().lower()
    fetched_at_iso = datetime.now(timezone.utc).isoformat()

    seen_urls = set()
    normalized = []
    for page in range(_MAX_PAGES):
        offset = page * _RESULTS_PER_PAGE
        try:
            response = requests.get(
                careers_url,
                params={"jobRecordsPerPage": _RESULTS_PER_PAGE, "jobOffset": offset},
                timeout=30,
                headers={"User-Agent": "Mozilla/5.0 (compatible; JobScoutAI/1.0)"},
            )
            response.raise_for_status()
        except requests.RequestException as e:
            if page == 0:
                raise
            print(f"Warning: Avature page {page} fetch failed for {company_name}, stopping "
                  f"pagination early: {e}")
            break

        soup = BeautifulSoup(response.text, "html.parser")
        links = [a for a in soup.find_all("a", href=True)
                 if _JOB_DETAIL_HREF_PATTERN.search(a["href"])]

        new_this_page = 0
        for link in links:
            absolute_url = urljoin(careers_url, link["href"])
            if absolute_url in seen_urls:
                continue
            seen_urls.add(absolute_url)
            new_this_page += 1

            title = link.get_text(strip=True)
            if not title:
                continue
            if search_lower and search_lower not in title.lower():
                continue

            normalized.append({
                "title": title,
                "company": company_name,
                "location": None,
                "posted_date": normalize_posted_date(fetched_at_iso, "avature"),
                "url": absolute_url,
                "description": "",
                "source": "avature",
            })
            if len(normalized) >= max_results:
                return normalized

        if new_this_page == 0:
            # Either this company has fewer postings than _RESULTS_PER_PAGE * (page+1), or the
            # pagination contract doesn't match what this connector assumes - either way, nothing
            # new came back, so stop rather than looping to _MAX_PAGES for no reason.
            break

    return normalized


if __name__ == "__main__":
    # Zero-Gemini-cost sanity check - add a company via the Streamlit "Manage companies" UI first
    # (or repository.add_avature_company), then swap the name in below. Worth running this
    # directly and eyeballing the output before trusting it in a real search - see the
    # location/posted_date caveats in fetch_avature_jobs's docstring above.
    jobs = fetch_avature_jobs("Deloitte USI")
    print(f"Fetched {len(jobs)} jobs")
    for job in jobs[:5]:
        print(f"  - {job['title']} -> {job['url']}")
