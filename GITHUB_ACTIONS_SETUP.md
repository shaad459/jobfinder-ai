# Setting up the daily email alert

One-time setup for `.github/workflows/daily-job-alert.yml`, which runs `run_scheduled_search.py`
once a day on GitHub's servers and emails you any new Strong/Good matches.

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

**b. Push your resume to it:**
```powershell
cd "$env:TEMP"
git clone https://github.com/shaad459/resume-private.git
Copy-Item "D:\training\Ai trainings\pythonprojects\jobfinder-ai\app\sample_data\Shaad Khan Product Owner.pdf" "resume-private\resume.pdf"
cd resume-private
git add resume.pdf
git commit -m "Add resume"
git push
```
If your resume is a `.docx` instead of `.pdf`, name the pushed file `resume.docx` and change
`RESUME_EXT: pdf` to `RESUME_EXT: docx` near the top of `.github/workflows/daily-job-alert.yml`
before you push that file - `extract_resume_text()` picks its parser from the file extension, so
these have to match.

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
| `JSEARCH_API_KEY` | same as your local `.env` |
| `ADZUNA_APP_ID` | same as your local `.env` |
| `ADZUNA_APP_KEY` | same as your local `.env` |
| `GMAIL_ADDRESS` | your Gmail address |
| `GMAIL_APP_PASSWORD` | the 16-character app password from step 1 |
| `NOTIFY_EMAIL` | *(optional)* only if you want the digest sent somewhere other than `GMAIL_ADDRESS` itself |

None of these ever appear in your code or commit history - they're injected as environment
variables only at the moment the workflow runs, and GitHub scrubs any exact-match occurrence out
of the logs automatically.

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
after that, each day's email should only contain postings that are genuinely new.

## Updating the resume later

Rather than repeating step 2b by hand, upload the new resume in the Streamlit app as normal,
then open the **"📧 Scheduled email alerts"** expander that appears below it and click
**"Use this resume for my scheduled email alerts."** This runs the same `git` clone/commit/push
against `resume-private` that step 2b describes, from a small local clone kept at
`~/.jobscout_ai/resume-private` (Windows: `C:\Users\<you>\.jobscout_ai\resume-private`) - it
overwrites whatever resume was there before, so the next scheduled (or manually triggered) run
picks up the new one automatically. See `resume_sync.py` for exactly what it does; it only ever
touches that separate local clone, never `jobfinder-ai`'s own git repo.

## One thing worth knowing

This workflow keeps its own `jobfinder.db`, persisted in GitHub's Actions cache - it is a
**separate database from the one on your machine** that Streamlit reads. They don't share an
"already scored" list. Practically: a job the scheduled workflow already emailed you about might
still show up as "new" the next time you search manually from the Streamlit app on your own
computer, and vice versa. For a single-user, no-shared-hosting setup like this, that's the
tradeoff for not standing up a shared database (which is what the old Turso-based design in
`PHASE6_EMAIL_ALERTS_SPEC.md` was for, before the project moved to "runs entirely on your own
machine" - that doc is now superseded and can be ignored/deleted).
