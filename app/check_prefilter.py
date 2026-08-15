"""Throwaway diagnostic - NOT part of the app. Sanity-checks the new Stage 0 pre-filter logic
in matcher.py (title-keyword relevance + experience-level mismatch) against representative
fake jobs, with zero Gemini cost and zero network calls.
"""

from matcher import _title_matches_profile, _passes_experience_filter

profile = {
    "job_titles": ["Product Owner", "Senior Product Owner"],
    "total_years_experience": 10,
}

print("=== Title relevance ===")
title_cases = [
    ("Senior Product Manager, AI Garage", True),
    ("Product Manager, Search Platforms", True),
    ("Senior Strategist, Trust and Safety", False),
    ("Software Engineer III", False),
    ("Engineering Manager, Looker, Google Cloud", False),
]
for title, expected in title_cases:
    result = _title_matches_profile(title, profile)
    mark = "OK " if result == expected else "FAIL"
    print(f"  [{mark}] {title!r:50} -> {result} (expected {expected})")

print("\n=== Experience-level mismatch (candidate: 10 years) ===")
experience_cases = [
    ({"title": "Product Manager, New Grad Program", "description": ""}, False),
    ({"title": "Senior Product Manager", "description": "Requires 3+ years of relevant experience."}, True),
    ({"title": "Director of Product", "description": "Requires 15+ years of experience in product leadership."}, False),
    ({"title": "Senior Product Manager", "description": "8-10 years of experience preferred."}, True),
    ({"title": "Senior Product Manager", "description": "No explicit years-of-experience requirement mentioned here."}, True),
    ({"title": "Junior Product Analyst", "description": "0-2 years of experience required."}, False),
]
for job, expected in experience_cases:
    passes, reason = _passes_experience_filter(job, profile["total_years_experience"])
    mark = "OK " if passes == expected else "FAIL"
    print(f"  [{mark}] {job['title']!r:35} -> passes={passes} (expected {expected}) reason={reason!r}")
