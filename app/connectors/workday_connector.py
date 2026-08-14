import requests

WORKDAY_COMPANIES = {
    "Mastercard": {"company": "mastercard", "datacenter": "wd1", "site": "CorporateCareers"},
    "Barclays": {"company": "barclays", "datacenter": "wd3", "site": "External_Career_Site_Barclays"},
    "Deutsche Bank": {"company": "db", "datacenter": "wd3", "site": "DBWebsite"},
    "Apex Group": {"company": "theapexgroup", "datacenter": "wd3", "site": "apexgroupcareers"},
    "Citi": {"company": "citi", "datacenter": "wd5", "site": "2"},
}

WORKDAY_PAGE_SIZE = 20  # Workday's hard per-request cap


def fetch_workday_jobs(company_name: str, search_text: str = "", max_results: int = 60) -> list[dict]:
    config = WORKDAY_COMPANIES[company_name]
    base_url = f"https://{config['company']}.{config['datacenter']}.myworkdayjobs.com"
    api_url = f"{base_url}/wday/cxs/{config['company']}/{config['site']}/jobs"

    normalized = []
    offset = 0

    while offset < max_results:
        response = requests.post(
            api_url,
            json={
                "appliedFacets": {},
                "limit": WORKDAY_PAGE_SIZE,
                "offset": offset,
                "searchText": search_text,
            },
            headers={"Content-Type": "application/json", "Accept": "application/json"},
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
                "posted_date": job.get("postedOn"),
                "url": base_url + job.get("externalPath", ""),
                "description": None,
                "source": "workday",
            })

        offset += WORKDAY_PAGE_SIZE
        if offset >= total_available:
            break  # we've now fetched everything this company has

    return normalized
import re
from bs4 import BeautifulSoup


def _html_to_text(html: str) -> str:
    soup = BeautifulSoup(html or "", "html.parser")
    text = soup.get_text(separator="\n")
    text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
    return text


def fetch_workday_job_description(job: dict) -> str:
    config = WORKDAY_COMPANIES.get(job["company"])
    if not config:
        return ""

    base_url = f"https://{config['company']}.{config['datacenter']}.myworkdayjobs.com"
    external_path = job["url"].replace(base_url, "")
    detail_url = f"{base_url}/wday/cxs/{config['company']}/{config['site']}{external_path}"

    response = requests.get(detail_url, headers={"Accept": "application/json"})
    response.raise_for_status()
    data = response.json()
    html_description = data.get("jobPostingInfo", {}).get("jobDescription", "")
    return _html_to_text(html_description)