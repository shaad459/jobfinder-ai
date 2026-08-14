from resume_parser import extract_resume_text
from profile_extractor import extract_structured_profile
from job_aggregator import fetch_all_jobs
from matcher import score_all_jobs

resume_text = extract_resume_text("sample_data/Shaad Khan Product Owner.pdf")
profile = extract_structured_profile(resume_text)

jobs = fetch_all_jobs("product owner", location="Pune, India")
print(f"Fetched {len(jobs)} jobs, scoring first 20 as a test...")

scored = score_all_jobs(profile, jobs[:20], batch_size=10)
scored.sort(key=lambda j: j.get("match_score", 0), reverse=True)

for job in scored:
    print(f"[{job['match_tier']:6}] {job['match_score']:3} - {job['title']} @ {job['company']} ({job['source']})")
    print(f"         Matches: {job['match_points']}")
    print(f"         Gaps:    {job['match_gaps']}")
    print(f"         {job['match_reasoning']}")
    print()