"""If you're forking this project to run your OWN job search rather than just reading the code,
this is the one file to edit.

company_sync.py, search_queries_sync.py, and job_cache_sync.py all push/pull data (which
companies to search, which role titles, the shared job cache) via git, against a single repo -
MAIN_REPO_URL below. As checked into this project, that's shaad459/jobfinder-ai. If you clone or
fork this repo and start using the Streamlit app without changing this, your own "add company" or
"upload resume" actions will try to push to shaad459's GitHub repo, not yours - which will fail
(you won't have write access), not silently succeed and pollute someone else's data. Still, don't
rely on that failure as a safety net: fork this repo AND update MAIN_REPO_URL below to point at
YOUR fork before using the app for real, so nothing tries to reach the wrong place at all. Treat
your fork as a standalone instance from that point on, not a copy that stays in sync with this
one.

Two things this file deliberately does NOT cover:

1. Your resume-private repo (see resume_sync.py) - kept as a separate, genuinely private repo
   rather than folded in here, since it holds your actual resume and needs different visibility
   than the rest of this project (which is public). Create your own the same way
   GITHUB_ACTIONS_SETUP.md step 2 describes, and point resume_sync.py at it via the
   PRIVATE_RESUME_REPO_URL environment variable (see resume_sync.py's docstring) rather than
   editing a constant - it already supports this without a code change.

2. The starter content already committed in this fork - companies_config.json currently lists
   the 5 companies this project's original search targeted (Mastercard, Barclays, Deutsche Bank,
   Apex Group, Citi), and search_queries_config.json reflects whatever resumes were active when
   it was last synced. Both are just starting points, not something you need to hand-edit: adding
   your first resume automatically replaces search_queries_config.json's contents (see
   repository.get_or_create_profile), and the "Manage companies" panel in the Streamlit app lets
   you remove/add companies the same way - both propagate to GitHub the normal way once
   MAIN_REPO_URL below points at your own fork.
"""

MAIN_REPO_URL = "https://github.com/shaad459/jobfinder-ai.git"
