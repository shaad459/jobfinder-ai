from pathlib import Path
from resume_parser import extract_resume_text
from profile_extractor import extract_structured_profile
from resume_builder import build_ats_resume_pdf
from resume_tailor import tailor_profile_for_job, tailored_resume_filename
from job_aggregator import fetch_company_jobs
from matcher import score_all_jobs
from database import init_db
from repository import (
    get_or_create_profile, get_profile_by_hash, save_job, save_match,
    get_scored_job_urls, get_matches, get_gemini_call_counts_today, delete_stale_jobs,
)
 
init_db()
 
deleted = delete_stale_jobs(max_age_days=7)
if deleted:
    print(f"Cleaned up {deleted} job(s) older than 7 days (and their matches).")
 
resume_path = "sample_data/Shaad Khan Product Owner.pdf"
resume_text = extract_resume_text(resume_path)
 
cached = get_profile_by_hash(resume_text)
if cached:
    profile_id = cached.pop("id")
    profile = cached
    print(f"Using cached profile id {profile_id} (skipped Gemini extraction)")
else:
    profile = extract_structured_profile(resume_text)
    profile_id = get_or_create_profile(resume_text, profile)
    print(f"Using profile id {profile_id} (extracted fresh)")
 
# Regenerated every run (cheap - just formatting already-extracted data, no Gemini cost) so
# it always reflects the current profile even if resume_builder.py's layout changes without a
# fresh extraction. Named "<original filename> ATS.pdf" so it's obviously the version to use
# when actually applying - the whole point is that what gets submitted matches what earned
# the match verdicts, rather than your original, differently-formatted file.
print("Optimizing your resume for ATS systems...")
ats_resume_path = str(Path(resume_path).stem) + " ATS.pdf"
build_ats_resume_pdf(profile, ats_resume_path)
print(f"ATS-friendly resume saved to {ats_resume_path}")
 
detected_location = profile.get("current_location") or ""
location_prompt = f"Current location [{detected_location}]: " if detected_location else "Current location: "
location = input(location_prompt).strip() or detected_location
 
company = input("Target company: ").strip()
relocation_ok = input("OK with relocation? (y/n): ").strip().lower().startswith("y")
 
query = profile.get("job_titles", ["product owner"])[0]
jobs = fetch_company_jobs(company, query, location=location, relocation_ok=relocation_ok)
print(f"Fetched {len(jobs)} jobs at {company}" + (" (any location)" if relocation_ok else f" (near {location})"))
 
already_scored = get_scored_job_urls(profile_id)
new_jobs = [j for j in jobs if j["url"] not in already_scored]
print(f"{len(new_jobs)} are new (not previously scored)")
 
 
def save_batch(batch_scored):
    for job in batch_scored:
        save_job(job)
        save_match(profile_id, job)
    print(f"  saved {len(batch_scored)} scored jobs to the database")
 
 
if new_jobs:
    score_all_jobs(profile, new_jobs, batch_size=10, on_batch_scored=save_batch)
 
job_urls_this_search = {j["url"] for j in jobs}
all_matches = get_matches(profile_id)
matches_this_search = [m for m in all_matches if m["url"] in job_urls_this_search]
 
print(f"\n--- Matches for {company} (freshest first) ---")
if not matches_this_search:
    print("(no matches yet - jobs may still be scoring, or none passed prescreen)")
for job in matches_this_search:
    posted = job.get("posted_date") or "date unknown"
    job_location = job.get("location") or "location not listed"
    print(f"[{job['match_tier']:6}] {job['match_score']:3} - {job['title']} @ {job['company']} "
          f"({job_location}, {job['source']}, posted: {posted})")
 
    breakdown = job.get("dimension_breakdown") or {}
    if breakdown:
        for dimension in ("role", "location", "skills", "certification", "experience", "domain"):
            dim = breakdown.get(dimension) or {}
            level = dim.get("level")
            if not level:
                continue
            note = dim.get("note")
            line = f"         {dimension}: {level}"
            if note:
                line += f" - {note}"
            print(line)
 
# On-demand resume tailoring: opt-in, one Gemini call per job you actually pick - never
# automatic for every Strong match, since that would multiply Gemini usage by however many
# Strong results a search turns up. See resume_tailor.py for the grounding/validation rules
# applied to whatever comes back before it's ever written to a PDF.
strong_matches = [job for job in matches_this_search if job.get("match_tier") == "Strong"]
if strong_matches:
    print(f"\n{len(strong_matches)} Strong match(es) - you can generate a tailored, "
          f"job-specific ATS resume for any of them.")
    for i, job in enumerate(strong_matches, start=1):
        print(f"  {i}. {job['title']} @ {job['company']}")
 
    while True:
        choice = input("\nEnter a number to tailor a resume for that job "
                        "(or press Enter to skip/finish): ").strip()
        if not choice:
            break
        if not choice.isdigit() or not (1 <= int(choice) <= len(strong_matches)):
            print("Not a valid number - try again.")
            continue
        selected_job = strong_matches[int(choice) - 1]
        print(f"Tailoring resume for {selected_job['title']} @ {selected_job['company']}...")
        tailored_profile = tailor_profile_for_job(profile, selected_job)
        tailored_path = tailored_resume_filename(resume_path, selected_job["company"])
        build_ats_resume_pdf(tailored_profile, tailored_path)
        print(f"Tailored ATS resume saved to {tailored_path}")
 
print("\n--- Gemini usage today (best-effort count, not Google's official number) ---")
counts = get_gemini_call_counts_today()
if not counts:
    print("(no Gemini calls logged today)")
for model, status_counts in counts.items():
    breakdown = ", ".join(f"{status}: {c}" for status, c in status_counts.items())
    print(f"  {model} — {breakdown}")