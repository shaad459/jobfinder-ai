from connectors.workday_connector import WORKDAY_COMPANIES, fetch_workday_jobs

for company in WORKDAY_COMPANIES:
    jobs = fetch_workday_jobs(company, search_text="product owner")
    print(f"\n--- {company}: {len(jobs)} jobs ---")
    for job in jobs[:3]:
        print(job)