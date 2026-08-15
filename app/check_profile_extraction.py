"""Diagnostic - NOT part of the main app flow, though it does use the real extraction pipeline.
Runs the new, richer profile_extractor.py schema against your real resume ONE time and prints
the result for visual inspection, without going anywhere near job fetching or matching.

This DOES cost one real Gemini call (profile extraction has no cheaper fallback), so run this
once, check the output looks right, and then let test_matcher.py's normal caching take over
from there rather than re-running this repeatedly.
"""

import json
from database import init_db
from resume_parser import extract_resume_text
from profile_extractor import extract_structured_profile
from repository import get_or_create_profile, get_profile_by_hash

init_db()

resume_text = extract_resume_text("sample_data/Shaad Khan Product Owner.pdf")

cached = get_profile_by_hash(resume_text)
if cached:
    print("Found an existing cached profile - if this still shows the OLD flat schema, run "
          "reset_profile.py first, then re-run this script.")
    profile = cached
else:
    print("No cached profile found - extracting fresh (this is the 1 real Gemini call)...")
    profile = extract_structured_profile(resume_text)
    profile_id = get_or_create_profile(resume_text, profile)
    print(f"Saved as profile id {profile_id}")

print("\n=== Extracted profile ===")
print(json.dumps(profile, indent=2))
