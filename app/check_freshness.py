"""Throwaway diagnostic - checks whether Workday's postedOn dates vary across pagination,
to confirm the "always Posted Today" result on page one was just newest-first sorting at a
high-volume company, not a systemic data issue. Fetches pages until Workday's own reported
"total" is exhausted (or a page comes back empty), for Citi and Mastercard.
 
Uses a browser-like User-Agent and a short delay between requests, since Workday's public
career sites sit behind bot-protection that can silently return an empty/non-JSON body if it
decides a request pattern looks automated.
"""
 
import time
import requests
from collections import Counter
from connectors.workday_connector import WORKDAY_COMPANIES
from connectors.date_utils import normalize_posted_date
 
HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
}
 
for company_name in ("Citi", "Mastercard"):
    cfg = WORKDAY_COMPANIES[company_name]
    base_url = f"https://{cfg['company']}.{cfg['datacenter']}.myworkdayjobs.com"
    api_url = f"{base_url}/wday/cxs/{cfg['company']}/{cfg['site']}/jobs"
 
    print(f"=== {company_name} ===")
    counts = Counter()
    total_seen = 0
    offset = 0
    page_size = 20
    total_available = None
 
    while True:
        try:
            resp = requests.post(
                api_url,
                json={"appliedFacets": {}, "limit": page_size, "offset": offset, "searchText": ""},
                headers=HEADERS,
                timeout=30,
            )
        except requests.exceptions.RequestException as e:
            print(f"  request failed at offset {offset}: {e}")
            break
 
        if resp.status_code != 200:
            print(f"  stopped at offset {offset}: HTTP {resp.status_code}")
            print(f"  response body (first 300 chars): {resp.text[:300]!r}")
            break
 
        try:
            data = resp.json()
        except requests.exceptions.JSONDecodeError:
            print(f"  stopped at offset {offset}: got HTTP 200 but body wasn't valid JSON "
                  f"(likely bot-protection). Response body (first 300 chars):")
            print(f"  {resp.text[:300]!r}")
            break
 
        if total_available is None:
            total_available = data.get("total", 0)
            print(f"  Workday reports total={total_available} job(s) available")
 
        postings = data.get("jobPostings", [])
        if not postings:
            break
 
        for job in postings:
            counts[job.get("postedOn")] += 1
            total_seen += 1
 
        offset += page_size
        if offset >= total_available:
            break
 
        time.sleep(1)  # be polite between pages
 
    print(f"  sampled {total_seen} job(s) across pagination")
    for raw_value, count in counts.most_common():
        normalized = normalize_posted_date(raw_value, "workday")
        print(f"    {raw_value!r:30} x{count:<4} -> {normalized}")
    print()
 
    time.sleep(2)  # be polite between companies
 