import requests

WORKDAY_COMPANIES = {
    "Mastercard": {"company": "mastercard", "datacenter": "wd1", "site": "CorporateCareers"},
    "Barclays": {"company": "barclays", "datacenter": "wd3", "site": "External_Career_Site_Barclays"},
    "Deutsche Bank": {"company": "db", "datacenter": "wd3", "site": "DBWebsite"},
    "Apex Group": {"company": "theapexgroup", "datacenter": "wd3", "site": "apexgroupcareers"},
    "Citi": {"company": "citi", "datacenter": "wd5", "site": "2"},
}


def fetch_workday_jobs(company_name: str, search_text: str = "", limit: int = 20) -> list[dict]:
    config = WORKDAY_COMPANIES[company_name]
    base_url = f"https://{config['company']}.{config['datacenter']}.myworkdayjobs.com"
    api_url = f"{base_url}/wday/cxs/{config['company']}/{config['site']}/jobs"

    response = requests.post(
        api_url,
        json={"appliedFacets": {}, "limit": limit, "offset": 0, "searchText": search_text},
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    response.raise_for_status()
    raw_jobs = response.json().get("jobPostings", [])

    normalized = []
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
    return normalized