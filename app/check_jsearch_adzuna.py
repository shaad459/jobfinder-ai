"""Throwaway diagnostic - NOT part of the app. Exercises the JSearch/Adzuna fallback path in
fetch_company_jobs() for a company that's NOT in WORKDAY_COMPANIES, which up to now has never
actually been tested (every real search so far hit Citi or Mastercard, both Workday-configured).
 
Zero Gemini cost - this only calls the job-fetching layer, never the matching cascade.
"""
 
from job_aggregator import fetch_company_jobs
from connectors.workday_connector import WORKDAY_COMPANIES
 
company = input("Company to search (must NOT be Citi/Mastercard/Barclays/Deutsche Bank/Apex Group): ").strip()
if company.lower() in (k.lower() for k in WORKDAY_COMPANIES):
    print(f"'{company}' is a Workday-configured company - this diagnostic is specifically for "
          f"testing the JSearch/Adzuna fallback, so pick a different company.")
    raise SystemExit(1)
 
query = input("Job title / keywords to search (e.g. 'product owner'): ").strip()
location = input("Location (e.g. 'Pune, India') or blank for none: ").strip()
relocation_input = input("OK with relocation / any location? (y/n): ").strip().lower()
relocation_ok = relocation_input == "y"
 
jobs = fetch_company_jobs(company, query, location=location, relocation_ok=relocation_ok)
 
print(f"\n{len(jobs)} job(s) returned after location + freshness filtering\n")
 
by_source = {}
for job in jobs:
    by_source[job.get("source")] = by_source.get(job.get("source"), 0) + 1
print(f"Breakdown by source: {by_source}\n")
 
for job in jobs:
    print(f"[{job.get('source')}] {job.get('title')}")
    print(f"    company: {job.get('company')}")
    print(f"    location: {job.get('location')}")
    print(f"    posted_date: {job.get('posted_date')}")
    print(f"    url: {job.get('url')}")
    print()