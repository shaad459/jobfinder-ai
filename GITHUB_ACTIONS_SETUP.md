# Setting up the daily email alert

One-time setup for `.github/workflows/daily-job-alert.yml`, which runs `run_scheduled_search.py`
every 3 hours on GitHub's servers and emails you any new Strong/Good matches.

**Why every 3 hours instead of once a day:** spreading Gemini scoring calls across 8 smaller runs
instead of 1 large one avoids a single run's batch tripping a per-minute rate limit and
backlogging everything behind it. It does **not** increase the total number of jobs scored per
day - the same new postings get scored either way, just in smaller batches, more often.

**Cache-first, not a second live fetch:** `refresh-job-cache.yml` (section 6 below) already runs
every ~3 hours and pushes a broad sweep of postings to a shared `data-cache` branch, offset ~20
minutes before this workflow. `run_scheduled_search.py` pulls that branch down first
(`job_cache_sync.sync_job_cache`) and reads each configured company from that local cache before
ever hitting Workday/JSearch/Adzuna live again - only a company the cache has nothing for gets a
live fetch. This matters because JSearch/Adzuna both have their own external rate limits
independent of Gemini's; fetching the same company/query combination twice every 3 hours (once
for the cache refresh, once here) would burn that quota for zero new information. If the cache
sync itself fails for any reason (a brand-new repo where `refresh-job-cache.yml` hasn't run yet,
a transient network error), every company just falls back to a live fetch - same behavior as
before this cache-first mechanic existed, never a hard failure.

## 1. Generate a Gmail App Password

You do **not** need a new Google account - App Passwords work with your existing one, but only
once 2-Step Verification is turned on.

1. If you haven't already, turn on 2-Step Verification: `myaccount.google.com/security` → "2-Step
   Verification" → follow the prompts.
2. Go to `myaccount.google.com/apppasswords`.
3. Create a new app password (name it something like "JobScout AI").
4. Copy the 16-character password it gives you - you'll paste it into a GitHub secret below.
   This is **not** your normal Gmail password and can be revoked independently at any time from
   the same page.

## 2. Create a private repo for your resume, and a token to read it

Earlier versions of this doc had you base64-encode the resume and split it across GitHub
secrets, since a single secret is capped at 48 KB and the encoded resume is bigger than that.
That approach is fragile (easy to mis-paste one chunk, hard to update) and unnecessary - storing
the resume in its own small private repo avoids the size limit entirely, and updating it going
forward is just "push a new file."

**a. Create the private repo:**
On GitHub, click **+** (top right) → **New repository**. Name it `resume-private` (or anything -
just update the `repository:` line in `daily-job-alert.yml` to match). Set visibility to
**Private**. Don't add a README/gitignore/license - keep it empty.

**b. Push your resume(s) to it:**
Easiest path: skip this step entirely and use the Streamlit app instead - open your resume
library, upload the resume(s) you want searched, then click **"Sync my active resumes for
scheduled email alerts"** under any resume's "Details / ATS resume / email alerts" expander. See
**"Updating the resume(s) later"** below for exactly what that does; it pushes every currently
active resume in your library in one go.

If you'd rather push the first one by hand (e.g. to get the repo non-empty before you've set
anything up locally), any filename starting with `resume` and ending in `.pdf` or `.docx` works -
`run_scheduled_search.py` searches every `resume_*.pdf`/`resume_*.docx` (and, for backward
compatibility, a plain `resume.pdf`/`resume.docx`) it finds in the checked-out repo:
```powershell
cd "$env:TEMP"
git clone https://github.com/shaad459/resume-private.git
Copy-Item "D:\training\Ai trainings\pythonprojects\jobfinder-ai\app\sample_data\Shaad Khan Product Owner.pdf" "resume-private\resume_1.pdf"
cd resume-private
git add resume_1.pdf
git commit -m "Add resume"
git push
```
`.docx` works the same way (`resume_1.docx`) - `extract_resume_text()` picks its parser from the
file extension, so nothing else needs to change to mix `.pdf` and `.docx` resumes in the same
repo.

**c. Create a token scoped to ONLY that repo:**
Go to `github.com/settings/personal-access-tokens` → **Generate new token** (this is the
"fine-grained" token type, not "classic" - fine-grained lets you lock it down to one repo).
- **Repository access:** "Only select repositories" → choose `resume-private`.
- **Permissions** → **Repository permissions** → set **Contents** to **Read-only**. Leave
  everything else as "No access."
- **Expiration:** pick something like 90 days or 1 year rather than "No expiration" - this token
  can only ever read one small private repo, but a shorter lifetime limits the blast radius if it
  ever leaks. You'll get an email from GitHub before it expires; when that happens, generate a
  new one and update the secret in step 3 - the workflow will otherwise start failing silently
  until you do.
- Click **Generate token** and copy it immediately (GitHub only shows it once).

## 3. Add repository secrets

On GitHub: your repo → **Settings** → **Secrets and variables** → **Actions** → **New repository
secret**. Add each of these (values from your local `.env` for the API keys):

| Secret name | Value |
|---|---|
| `PRIVATE_RESUME_PAT` | the fine-grained token from step 2c |
| `GEMINI_API_KEY` | same as your local `.env` |
| `GEMINI_API_KEY_2`, `_3`, `_4`, ... | *(optional)* additional Gemini keys for rotation - see "Rotating across multiple Gemini API keys" below |
| `JSEARCH_API_KEY` | same as your local `.env` |
| `ADZUNA_APP_ID` | same as your local `.env` |
| `ADZUNA_APP_KEY` | same as your local `.env` |
| `GMAIL_ADDRESS` | your Gmail address |
| `GMAIL_APP_PASSWORD` | the 16-character app password from step 1 |
| `NOTIFY_EMAIL` | *(optional)* only if you want the digest sent somewhere other than `GMAIL_ADDRESS` itself |

None of these ever appear in your code or commit history - they're injected as environment
variables only at the moment the workflow runs, and GitHub scrubs any exact-match occurrence out
of the logs automatically.

## Rotating across multiple Gemini API keys (optional)

If you have more than one Gemini API key (e.g. from separate Google accounts/projects),
`gemini_utils.call_gemini` will round-robin every call across all of them instead of hammering a
single key's own per-minute quota - see that file's docstring for exactly how the rotation and
rate-limit fallback work. This is entirely opt-in: with only `GEMINI_API_KEY` set, nothing about
this changes.

**a. Locally:** add `GEMINI_API_KEY_2`, `GEMINI_API_KEY_3`, etc. to your `.env` (see
`.env.example`) - numbered, starting at `_2`, with no gap (a missing `_3` stops the scan there
even if `_4` is set, so a typo'd variable name doesn't get silently skipped).

**b. For the scheduled workflows:** add the same numbered secrets in step 3 above, then make sure
`daily-job-alert.yml`'s "Run search and send digest" step passes each one through as an env var
(`GEMINI_API_KEY_2: ${{ secrets.GEMINI_API_KEY_2 }}`, etc.) - only `refresh-job-cache.yml` doesn't
need this, since that workflow never calls Gemini at all (see `refresh_job_cache.py`'s docstring).

**How many keys is worth adding:** each additional key spreads load further below its own
per-minute quota, but the real ceiling that matters for THIS project is Gemini's daily quota per
key (not just per-minute) - rotating across keys multiplies your effective daily budget too, not
just your per-minute headroom, since each key's daily quota is tracked separately. Diminishing
returns kick in once you have comfortably more daily quota than this project's actual call volume
needs; there's no reason to add a 5th key just because you can.

## 4. Push the new files

`email_sender.py`, `run_scheduled_search.py`, and `.github/workflows/daily-job-alert.yml` are all
safe to commit - none of them contain a real key or your resume, only code and placeholders read
from the environment at runtime.

If you'd previously set up the old base64-secret approach, clean up the now-unused secrets:
**Settings → Secrets and variables → Actions** → delete `RESUME_BASE64_1`, `RESUME_BASE64_2`,
`RESUME_BASE64_3` if present (the workflow no longer reads them - leaving them costs nothing
functionally, but there's no reason to keep old resume data sitting there). `encode_resume.ps1`
and `update_resume_secrets.ps1` are no longer needed either - the private repo replaces both.

## 5. Test it

Go to your repo's **Actions** tab → "Daily job alert" → **Run workflow** (the manual trigger,
`workflow_dispatch`) rather than waiting for the schedule. Watch the run's logs for errors, and
check your inbox once it finishes. The very first run will likely email you a fairly large batch
(everything currently open across your 5 configured companies) since nothing's been scored yet -
after that, each run's email should only contain postings that are genuinely new.

## Updating the resume(s) later

Rather than repeating step 2b by hand, manage everything from the Streamlit app's resume
library. Upload/retire resumes there as normal, then open any resume's **"Details / ATS resume /
email alerts"** expander and click **"Sync my active resumes for scheduled email alerts."**

This pushes your WHOLE currently-active library to `resume-private` in one go - one
`resume_<id>.pdf`/`.docx` file per active resume - not just the resume you clicked from. A
resume you've since retired or deleted locally is removed from the private repo on the next
sync too, so the scheduled workflow stops searching it. Runs from a small local clone kept at
`~/.jobscout_ai/resume-private` (Windows: `C:\Users\<you>\.jobscout_ai\resume-private`). See
`resume_sync.sync_all_active_resumes` for exactly what it does; it only ever touches that
separate local clone, never `jobfinder-ai`'s own git repo.

Each resume searches independently in the scheduled run, same as selecting multiple resumes in
the Streamlit search panel - a job that matches more than one of your resumes shows up once per
resume in the digest email, each with its own score and a "Matched as: <label>" line so you can
tell them apart.

## 6. Set up the job-cache refresh workflow (optional, but recommended)

`.github/workflows/refresh-job-cache.yml` runs every 3 hours, refreshes a shared job cache so
Streamlit searches read from it instead of waiting on a live Workday/JSearch/Adzuna call every
time, and emails you an immediate, unscored heads-up for anything brand new (see
`refresh_job_cache.py` and `email_sender.send_new_postings_alert`). It reuses every secret from
step 3 above - **no new secrets to add.**

**a. Allow it to push back to the repo.** This workflow needs to push the refreshed cache to a
`data-cache` branch it creates on its own first run - something `daily-job-alert.yml` never
needed, since that one only reads secrets and sends email. Go to your repo's **Settings** →
**Actions** → **General** → scroll to **Workflow permissions**, and make sure **"Read and write
permissions"** is selected (not "Read repository contents permission" only). Without this, the
workflow's own `permissions: contents: write` declaration gets capped by the repo-level setting
and the push step will fail with a 403.

**b. Test it.** Same as step 5 - **Actions** tab → "Refresh job cache" → **Run workflow**. The
first run creates the `data-cache` branch (you'll see "data-cache branch didn't exist yet -
created it fresh" in the logs) and will very likely email you a batch of "new" postings, since
nothing's been cached before. After that, each run should only alert on genuinely new listings.

**c. Adding a company automatically reaches this workflow too.** Unlike `daily-job-alert.yml`
(which keeps its own separate `jobfinder.db`, restored from Actions cache - see "One thing worth
knowing" below), this workflow reads its company list from `app/companies_config.json`, which is
committed to `main` automatically whenever you add or remove a company in the Streamlit UI's
"Manage companies" panel (see `company_sync.py`). No manual step needed - just note that this
push happens in the background right when you click Add/Remove, using your machine's existing
git credentials, so it needs a working internet connection at that moment to actually reach
GitHub (it fails silently toward the console if it can't, and the company is still saved
locally either way).

**d. What roles it searches for also comes from your resumes, automatically.** The query terms
this workflow searches (per company) are the union of every ACTIVE resume's own extracted job
titles, synced to `app/search_queries_config.json` the same way companies are (see
`search_queries_sync.py`) - triggered whenever you upload a new resume or retire/restore one in
the library. This is what makes the cache actually relevant if you ever upload a resume for a
different role than the one this project was originally built around (e.g. a software engineer
resume instead of product/PM) - the scheduled cache starts searching for that role's titles
instead, with nothing to configure by hand. If `search_queries_config.json` has never been
synced yet (a fresh clone, or before your first resume upload), it falls back to a bootstrap
default of product/program/business-analyst titles rather than searching nothing.

## Forking this for your own job search

Everything above assumes you're using this exact repo (shaad459/jobfinder-ai) as-is. If you're
starting from a fork to run your own search instead, there's one thing to fix before you use the
app for real - not several, now that it's consolidated:

**Edit `app/repo_config.py`.** `company_sync.py`, `search_queries_sync.py`, and
`job_cache_sync.py` all push/pull `companies_config.json`, `search_queries_config.json`, and
`job_cache.json` against a single URL - `repo_config.MAIN_REPO_URL`. As checked in, that's this
repo. Left unchanged, your local "add company" or "upload resume" actions will try to push to
shaad459's GitHub repo instead of yours (and fail, since you won't have write access - it won't
silently succeed and pollute anyone's data, but it also won't do anything useful for you). Change
`MAIN_REPO_URL` to your own fork's URL and treat that fork as a standalone instance from then on,
not something that stays in sync with the original.

**Create your own private resume repo.** Follow step 2 above to create your own
`resume-private` (or any name), then either point `resume_sync.py` at it via a
`PRIVATE_RESUME_REPO_URL` environment variable in your `.env` (no code change needed - see
`resume_sync.py`'s docstring), or update the `repository:` line in
`.github/workflows/daily-job-alert.yml`'s "Checkout private resume repo" step to match, plus its
`PRIVATE_RESUME_PAT` secret.

**The starter content is a starting point, not something to hand-edit.** `companies_config.json`
currently lists the 5 companies this project's original search targeted, and
`search_queries_config.json` reflects whatever resumes were active in the original owner's
library when it was last synced. Neither needs manual cleanup: uploading your first resume
automatically replaces `search_queries_config.json`'s contents with your own resume's titles
(see `repository.get_or_create_profile`), and the "Manage companies" panel in the Streamlit app
lets you add/remove companies the normal way - both propagate to GitHub correctly once
`MAIN_REPO_URL` points at your own fork.

**What this doesn't fix (yet):** `app/cert_aliases.py`'s certification-to-generic-phrasing table
now spans several role families (project/program management, agile/scrum/product, business
analysis, cloud, data & analytics, cybersecurity, DevOps, IT service management, HR, finance),
but it's still a hand-maintained, representative list, not exhaustive - if your field's
certifications aren't well covered, matching still works via Gemini's own judgment (the
GROUNDING RULE in `matcher.py`'s prompt), just without this deterministic assist. Extending the
table for a category that matters to you is a small, contained edit - see that file's docstring.

## GitHub auto-disables inactive scheduled workflows after 60 days

This is a real platform constraint, not something either workflow's code can work around: GitHub
automatically disables a scheduled workflow (the `schedule:` trigger on both
`daily-job-alert.yml` and `refresh-job-cache.yml`) if the **repository** has had no commits/pushes
for 60 consecutive days. Manual runs via `workflow_dispatch` (the **Run workflow** button in the
Actions tab) don't count as activity for this purpose, and running the workflow itself doesn't
count either - it's specifically about pushes to the repo.

Practically: if you stop actively developing this repo (no commits, no resume syncs pushing to
`resume-private` - note that push doesn't count either, since it's a different repo - no company
or resume-driven config syncs to `main`) for two months straight, both scheduled workflows will
silently stop firing. You won't get an error email; the Actions tab will just show the workflow as
disabled, with a banner explaining why. Re-enabling is one click (**Actions** tab → select the
workflow → **Enable workflow**), or push any commit to the repo before the 60 days elapse.

If you want a completely hands-off setup that never needs a login check-in, one option is a small
scheduled no-op commit (e.g. a separate workflow that touches a timestamp file and pushes it) - not
set up here, since it's an extra moving part for a problem that a once-every-couple-months glance
at the repo already solves.

## One thing worth knowing

This workflow keeps its own `jobfinder.db`, persisted in GitHub's Actions cache - it is a
**separate database from the one on your machine** that Streamlit reads. They don't share an
"already scored" list. Practically: a job the scheduled workflow already emailed you about might
still show up as "new" the next time you search manually from the Streamlit app on your own
computer, and vice versa. For a single-user, no-shared-hosting setup like this, that's the
tradeoff for not standing up a shared database (which is what the old Turso-based design in
`PHASE6_EMAIL_ALERTS_SPEC.md` was for, before the project moved to "runs entirely on your own
machine" - that doc is now superseded and can be ignored/deleted).
